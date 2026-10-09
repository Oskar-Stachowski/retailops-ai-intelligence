"""Run separately guarded native/checkpoint phases, preserving costs between Actions steps.

The canonical CLI remains behind the recipe's dispatch-readiness gate. No final
generation, Project journal, fit, automatic retry or remote resume is authorized.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from retailops_ai.data_contracts.identity import canonical_sha256  # noqa: E402
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints  # noqa: E402
from retailops_ai.evaluation_campaign import preparation_execution as execution  # noqa: E402
from retailops_ai.evaluation_campaign import preparation_witness as native  # noqa: E402


def modules() -> tuple[Any, Any]:
    import ai09_preparation_worker as worker
    import measure_ai09_development_capacity as probe

    return worker, probe


def environment(output: Path) -> dict[str, str]:
    temporary = output / "temporary"
    temporary.mkdir(exist_ok=True, mode=0o700)
    return {
        **{
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "ACTIONS_RUNTIME_TOKEN",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            }
        },
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
        "TMPDIR": str(temporary),
    }


def initialize(source: Path, output: Path, *, plan: dict[str, Any], producer_python: Path) -> None:
    if any(
        not root.is_absolute()
        or ".." in root.parts
        or any(path.is_symlink() for path in (root, *root.parents))
        for root in (source, output)
    ):
        raise ValueError("preparation_controller_absolute_unlinked_paths_required")
    import psutil  # type: ignore[import-untyped]

    worker, probe = modules()
    head = probe.git(ROOT, "rev-parse", "HEAD")
    probe.clean_pin(ROOT, head)
    probe.clean_pin(source, plan["producer_commit"])
    if (
        probe.sha(ROOT / "uv.lock") != plan["consumer_lock_sha256"]
        or probe.sha(source / "data/requirements-parquet.txt") != plan["exporter_lock_sha256"]
        or probe.sha(source / plan["producer_audit"]["evidence"])
        != plan["producer_audit"]["evidence_sha256"]
    ):
        raise ValueError("preparation_controller_dependency_or_audit_changed")
    snapshot = source / "data/generated/ai09-capacity-snapshot"
    if output.exists() or snapshot.exists() or source.is_relative_to(output):
        raise ValueError("preparation_controller_fresh_owned_output_required")
    budgets = plan["budgets"]
    if (
        psutil.virtual_memory().available
        < budgets["tree_rss_bytes"] + budgets["minimum_available_memory_bytes"]
        or shutil.disk_usage(output.parent).free
        < budgets["scratch_bytes"] + budgets["minimum_free_disk_bytes"]
    ):
        raise ValueError("preparation_controller_preflight_reserve")
    # Preflight has its own retained cost, separate from the 180-minute preparation budget.
    setup = output.with_name(output.name + "-preflight")
    if setup.exists():
        raise ValueError("preparation_controller_previous_preflight_retained")
    setup.mkdir(mode=0o700)
    started = time.perf_counter()
    measured = probe.monitor(
        [
            str(producer_python),
            "-I",
            "-B",
            str(ROOT / "scripts/ai09_preparation_worker.py"),
            "--source",
            str(source),
            "--output",
            str(setup / "producer-runtime.json"),
            "--inspect-runtime",
            "producer",
        ],
        cwd=source,
        env=environment(setup),
        log=setup / "producer-runtime.log",
        roots=(setup,),
        budgets=budgets,
        deadline=started + 120,
    )
    execution.write_once(setup / "measurement.json", measured)
    if measured["status"] != "passed":
        raise ValueError("preparation_controller_runtime_inspection_failed")
    producer_runtime = probe.read(setup / "producer-runtime.json")
    consumer_runtime = worker.runtime(ROOT, producer=False)
    validators = {}
    for phase, validator in native.VALIDATORS.items():
        relative = validator.rsplit(".", 1)[0].replace(".", "/") + ".py"
        validators[phase] = probe.sha(
            (source if phase in probe.PHASES[:3] else ROOT / "src") / relative
        )
    identity = {
        "scope": checkpoints.SCOPE,
        "plan_sha256": canonical_sha256(plan),
        "generation_sha256": canonical_sha256(plan["generation"]),
        "producer_commit": plan["producer_commit"],
        "consumer_commit": head,
        **{
            name: plan[name]
            for name in ("consumer_lock_sha256", "producer_lock_sha256", "exporter_lock_sha256")
        },
        "producer_runtime_sha256": worker.digest(producer_runtime),
        "consumer_runtime_sha256": worker.digest(consumer_runtime),
        "validators_sha256": validators,
    }
    execution.write_once(setup / "consumer-runtime.json", consumer_runtime)
    execution.write_once(
        setup / "setup-cost.json",
        {
            "wall_seconds": time.perf_counter() - started,
            "producer_inspection_measurement": measured,
            "scope": "preflight_setup_outside_preparation_budget_inside_job_deadline",
            "not_zero_or_included_in_worker_cost": True,
        },
    )
    execution.initialize(output, plan=plan, identity=identity)


def seal_worker(source: Path, output: Path, phase: str) -> None:
    worker, probe = modules()
    state = execution.inspect(output)
    identity = state["identity"]
    if worker.digest(worker.runtime(ROOT, producer=False)) != identity["consumer_runtime_sha256"]:
        raise ValueError("preparation_controller_sealer_runtime_changed")
    probe.clean_pin(source, identity["producer_commit"])
    probe.clean_pin(ROOT, identity["consumer_commit"])
    # The controller has durably started sealing; native completion precedes it.
    events = state["events"]
    if (
        not events
        or events[-1]["kind"] != "seal"
        or events[-1]["event"] != "started"
        or events[-1]["phase"] != phase
    ):
        raise ValueError("preparation_controller_seal_not_reserved")
    measurement = events[-2]["measurement"]
    previous = None
    index = probe.PHASES.index(phase)
    if index:
        previous = probe.read(output / (probe.PHASES[index - 1] + ".checkpoint.json"))[
            "checkpoint_id"
        ]
    result = probe.read(output / (phase + ".json"))
    proof = probe.read(output / (phase + ".witness.json"))
    _, binding, cost = checkpoints.seal_stage(
        phase,
        Path(result[checkpoints.RESULT_PATHS[phase]]),
        output / "checkpoints" / phase,
        identity=identity,
        result=result,
        measurement=measurement,
        witness=proof,
        previous_checkpoint_id=previous,
    )
    execution.write_once(output / (phase + ".checkpoint.json"), binding)
    execution.write_once(output / (phase + ".seal-cost.json"), cost)


def phase(
    source: Path,
    output: Path,
    name: str,
    *,
    producer_python: Path,
    native_command: list[str] | None = None,
    seal_command: list[str] | None = None,
) -> None:
    worker, probe = modules()
    state = execution.inspect(output)
    identity = state["identity"]
    if worker.digest(worker.runtime(ROOT, producer=False)) != identity["consumer_runtime_sha256"]:
        raise ValueError("preparation_controller_runtime_changed")
    plan = state["plan"]
    roots = (output, source / "data/generated/ai09-capacity-snapshot")

    def accept_native(measurement: dict[str, Any]) -> None:
        result = probe.read(output / (name + ".json"))
        proof = probe.read(output / (name + ".witness.json"))
        peak = result.get("worker_peak_self_rss_bytes")
        if type(peak) is not int or not 1 <= peak <= plan["budgets"]["tree_rss_bytes"]:
            raise ValueError("preparation_controller_native_self_rss")
        checkpoints._completion(name, identity, result, measurement, proof)
        measurement["worker_peak_self_rss_bytes"] = peak

    if len(state["events"]) == probe.PHASES.index(name) * 4:
        interpreter = producer_python if name in probe.PHASES[:3] else Path(sys.executable)
        command = native_command or [
            str(interpreter),
            "-I",
            "-B",
            str(ROOT / "scripts/ai09_preparation_worker.py"),
            "--source",
            str(source),
            "--output",
            str(output),
            "--worker",
            name,
        ]
        live = None
        if plan.get("live_progress"):
            import ai09_live_progress

            live = ai09_live_progress.LiveProgress(
                name,
                started=time.perf_counter() - state["charged_wall_seconds"],
                budget_seconds=plan["budgets"]["wall_seconds"],
                artifact=output / (name + ".native.progress.jsonl"),
                interval_seconds=plan["live_progress"]["heartbeat_seconds"],
            )
        measured = execution.operate(
            output,
            phase=name,
            kind="native",
            command=command,
            cwd=source if name in probe.PHASES[:3] else ROOT,
            env=environment(output),
            roots=roots,
            monitor=probe.monitor,
            accept=accept_native,
            live_progress=live,
        )
        if measured["status"] != "passed":
            raise ValueError("preparation_controller_native_failed")

    def accept_seal(measurement: dict[str, Any]) -> None:
        binding = probe.read(output / (name + ".checkpoint.json"))
        lineage = binding["lineage"]
        # Full archive/native inventory verification ran in the guarded sealer process.
        # Bind its successful result to the exact independent native completion now.
        measured_native = execution.inspect(output)["events"][-2]["measurement"]
        if lineage != {
            "version": checkpoints.VERSION,
            "phase": name,
            "identity": identity,
            "previous_checkpoint_id": None
            if name == probe.PHASES[0]
            else probe.read(
                output / (probe.PHASES[probe.PHASES.index(name) - 1] + ".checkpoint.json")
            )["checkpoint_id"],
            "result_sha256": canonical_sha256(probe.read(output / (name + ".json"))),
            "measurement_sha256": canonical_sha256(measured_native),
            "witness_sha256": canonical_sha256(probe.read(output / (name + ".witness.json"))),
        }:
            raise ValueError("preparation_controller_sealed_completion_mismatch")
        measurement["checkpoint_binding_sha256"] = canonical_sha256(binding)

    measured = execution.operate(
        output,
        phase=name,
        kind="seal",
        command=seal_command
        or [
            str(Path(sys.executable)),
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--source",
            str(source),
            "--output",
            str(output),
            "--seal-worker",
            name,
        ],
        cwd=ROOT,
        env=environment(output),
        roots=roots,
        monitor=probe.monitor,
        accept=accept_seal,
    )
    if measured["status"] != "passed":
        raise ValueError("preparation_controller_checkpoint_failed")
    execution.write_once(
        output / (name + ".completed.json"),
        {
            "phase": name,
            "checkpoint": probe.read(output / (name + ".checkpoint.json")),
            "execution_event_sha256": canonical_sha256(execution.inspect(output)["events"][-1]),
            "project_generation_receipt": False,
            "final_test_authorized": False,
        },
    )


def finalize(output: Path) -> dict[str, Any]:
    state = execution.inspect(output)
    result = {
        key: value for key, value in state.items() if key not in {"identity", "plan", "events"}
    }
    result.update(
        version=execution.VERSION,
        identity=state["identity"],
        events_sha256=canonical_sha256(state["events"]),
        native_measurements=[
            e["measurement"]
            for e in state["events"]
            if e["event"] == "finished" and e["kind"] == "native"
        ],
        seal_measurements=[
            e["measurement"]
            for e in state["events"]
            if e["event"] == "finished" and e["kind"] == "seal"
        ],
        project_journal_initialized=False,
        new_project_fits=0,
        final_test_opened=False,
        canonical_ai_training_qualified=False,
        quality_qualified=False,
        stage_ready=False,
    )
    execution.write_once(output / "resource.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--phase", choices=checkpoints.PHASES)
    parser.add_argument("--seal-worker", choices=checkpoints.PHASES)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if (
        sum(bool(value) for value in (args.initialize, args.phase, args.seal_worker, args.finalize))
        != 1
    ):
        parser.error("select one operation")
    _, probe = modules()
    probe.require_remote()
    plan = probe.read(probe.PLAN_PATH)
    probe.validate_plan(plan)
    probe.require_dispatch_readiness(plan)
    if args.initialize:
        initialize(
            args.source, args.output, plan=plan, producer_python=args.source / ".venv/bin/python"
        )
    else:
        if execution.inspect(args.output)["plan"] != plan:
            raise ValueError("preparation_controller_canonical_plan_changed")
        if args.phase:
            phase(
                args.source,
                args.output,
                args.phase,
                producer_python=args.source / ".venv/bin/python",
            )
        elif args.seal_worker:
            seal_worker(args.source, args.output, args.seal_worker)
        else:
            result = finalize(args.output)
            if result["status"] != "complete":
                raise SystemExit(1)


if __name__ == "__main__":
    main()
