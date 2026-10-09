"""Fixed tiny Actions prefix/upload/restore/resume control; no canonical dispatch.

Source subprocesses load only stdlib plus their native dependencies. All public
worker paths require the exact ten-day control plan before Source access.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
PHASES = ("generation", "qualification", "export", "import", "curation")
VERSION = "ai09-artifact-prefix-resume-control-1.0.0"


def probe_module() -> Any:
    import measure_ai09_development_capacity

    return measure_ai09_development_capacity


def control_plan() -> dict[str, Any]:
    probe = probe_module()
    plan: dict[str, Any] = probe.read(probe.PLAN_PATH)
    return {
        **plan,
        "version": VERSION,
        "generation": {
            "profile": "ai-smoke",
            "seed": 42,
            "days": 10,
            "products": 8,
            "stores": 2,
            "warehouses": 1,
            "start_date": "2026-07-22",
            "end_date": "2026-07-31",
            "forecast_plan_days": 14,
        },
        "budgets": {
            **plan["budgets"],
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 512 * 1024**2,
            "wall_seconds": 600,
        },
    }


def consumer() -> Any:
    import run_ai09_preparation

    return run_ai09_preparation


def validate_control(output: Path) -> None:
    if probe_module().read(output / "preparation-plan.json") != control_plan():
        raise ValueError("checkpoint_control_exact_tiny_plan_required")


def phase(source: Path, output: Path, name: str, producer_python: Path) -> None:
    controller = consumer()
    validate_control(output)
    prefix = [
        "-I",
        "-B",
        str(Path(__file__).resolve()),
        "--source",
        str(source),
        "--output",
        str(output),
    ]
    interpreter = producer_python if name in PHASES[:3] else Path(sys.executable)
    controller.phase(
        source,
        output,
        name,
        producer_python=producer_python,
        native_command=[str(interpreter), *prefix, "--operation", "native", "--phase", name],
        seal_command=[sys.executable, *prefix, "--operation", "seal", "--phase", name],
    )


def prepare_prefix(source: Path, output: Path, producer_python: Path) -> None:
    controller = consumer()
    import ai09_checkpoint_transport as transport

    if output.exists() or not output.is_absolute() or any(p.is_symlink() for p in output.parents):
        raise ValueError("checkpoint_control_fresh_owned_output_required")
    output.mkdir(mode=0o700, parents=True)
    cold = output / "cold"
    controller.initialize(source, cold, plan=control_plan(), producer_python=producer_python)
    for name in PHASES[:2]:
        phase(source, cold, name, producer_python)
    result = controller.finalize(cold)
    if result["status"] != "prepared" or result["completed_phases"] != 2:
        raise ValueError("checkpoint_control_prefix_incomplete")
    transport.bundle_prefix(cold, output / "prefix-artifact")
    controller.execution.write_once(
        output / "expected-identity.json", probe_module().read(cold / "preparation-identity.json")
    )


def retrieve_worker(output: Path, artifact_id: int) -> None:
    controller = consumer()
    import ai09_checkpoint_transport as transport

    identity = probe_module().read(output / "expected-identity.json")
    head, run_id = os.environ["GITHUB_SHA"], int(os.environ["GITHUB_RUN_ID"])
    if identity["consumer_commit"] != head:
        raise ValueError("checkpoint_control_current_workflow_head_mismatch")
    receipt = transport.retrieve(
        artifact_id=artifact_id,
        run_id=run_id,
        head=head,
        name=f"ai09-checkpoint-control-{head}-{run_id}-prefix",
        identity=identity,
        output=output / "download",
        token=os.environ.get("GITHUB_TOKEN"),
        maximum=64 * 1024**2,
    )
    controller.execution.write_once(output / "retrieved.json", receipt)


def restore_worker(output: Path) -> None:
    controller = consumer()
    import ai09_checkpoint_transport as transport

    probe = probe_module()
    identity = probe.read(output / "expected-identity.json")
    prefix = output / "download/prefix"
    manifest = transport.verify_bundle(prefix, identity=identity)
    bindings = []
    for name in PHASES[: manifest["completed_phases"]]:
        binding = probe.read(prefix / (name + ".checkpoint.json"))
        bindings.append((prefix / "checkpoints" / name / binding["checkpoint_id"], binding))
    restored = controller.checkpoints.restore_chain(
        bindings, output / "restored", identity=identity
    )
    controller.execution.write_once(output / "restored.json", {"phases": restored})


def retrieve_and_resume(
    source: Path, output: Path, producer_python: Path, artifact_id: int
) -> None:
    controller, probe = consumer(), probe_module()
    from retailops_ai.evaluation_campaign import preparation_resume

    validate_control(output / "cold")
    cold = controller.execution.inspect(output / "cold")
    started = time.perf_counter()
    measurements = {}
    for operation in ("retrieve-worker", "restore-worker"):
        environment = controller.environment(output)
        if operation == "retrieve-worker" and os.environ.get("GITHUB_TOKEN"):
            environment["GITHUB_TOKEN"] = os.environ["GITHUB_TOKEN"]
        measured = probe.monitor(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).resolve()),
                "--source",
                str(source),
                "--output",
                str(output),
                "--operation",
                operation,
                "--artifact-id",
                str(artifact_id),
            ],
            cwd=ROOT,
            env=environment,
            log=output / (operation + ".log"),
            roots=(output,),
            budgets=control_plan()["budgets"],
            deadline=started + cold["remaining_wall_seconds"],
        )
        controller.execution.write_once(output / (operation + ".measurement.json"), measured)
        measurements[operation] = measured
        if measured["status"] != "passed":
            raise ValueError("checkpoint_control_" + operation + "_failed")
    transport_receipt = probe.read(output / "retrieved.json")
    resumed = output / "resumed"
    preparation_resume.resume_prefix(
        output / "download/prefix",
        resumed,
        identity=probe.read(output / "expected-identity.json"),
        expected_event_sha256=transport_receipt["event_sha256"],
        restored=probe.read(output / "restored.json")["phases"],
        transport_receipt=transport_receipt,
        transport_measurement=measurements["retrieve-worker"],
        restore_measurement=measurements["restore-worker"],
    )
    # Preserve original inputs elsewhere; their old paths cannot satisfy the resumed workers.
    moved = {}
    for name in ("raw", "qualification"):
        original = output / "cold" / name
        retained = output / ("cold-" + name + "-retained")
        if retained.exists() or not original.is_dir():
            raise ValueError("checkpoint_control_original_input_relocation")
        original.rename(retained)
        moved[str(original)] = str(retained)
    controller.execution.write_once(output / "retained-original-paths.json", moved)
    try:
        for name in PHASES[2:]:
            phase(source, resumed, name, producer_python)
    finally:
        result = controller.finalize(resumed)
    if result["status"] != "complete" or result["completed_phases"] != 5:
        raise ValueError("checkpoint_control_resumed_preparation_incomplete")
    controller.execution.write_once(
        output / "checkpoint-summary.json",
        {
            "version": VERSION,
            "control_commit": cold["identity"]["consumer_commit"],
            "source_commit": cold["identity"]["producer_commit"],
            "status": "passed",
            "completed_phases": 5,
            "source_generation_processes": 1,
            "original_source_paths_unavailable_during_resumed_work": all(
                not Path(p).exists() for p in moved
            ),
            "original_inputs_retained": True,
            "cold_prefix_charged_wall_seconds": cold["charged_wall_seconds"],
            "transport_measurement": measurements["retrieve-worker"],
            "restore_measurement": measurements["restore-worker"],
            "total_charged_wall_seconds": result["charged_wall_seconds"],
            "remaining_control_budget_seconds": result["remaining_wall_seconds"],
            "remote_artifact": transport_receipt,
            "canonical_diagnostic_dispatched": False,
            "new_project_fits": 0,
            "project_journal_initialized": False,
            "fresh_final_reads": 0,
            "stage_ready": False,
        },
    )


def observe(output: Path) -> None:
    controller, probe = consumer(), probe_module()
    import ai09_live_progress

    started = time.perf_counter()
    live = ai09_live_progress.LiveProgress(
        "visibility_hold_no_source_work",
        started=started,
        budget_seconds=180,
        artifact=output / "visibility.progress.jsonl",
        interval_seconds=60,
    )
    measured = probe.monitor(
        [sys.executable, "-I", "-c", "import time; time.sleep(130)"],
        cwd=ROOT,
        env=controller.environment(output),
        log=output / "visibility.log",
        roots=(output,),
        budgets=control_plan()["budgets"],
        deadline=started + 180,
        live_progress=live,
    )
    controller.execution.write_once(
        output / "visibility.json",
        {
            "purpose": "explicit_idle_UI_observation_window_not_source_work",
            "hold_seconds": 130,
            "resource": measured,
            "actions_UI_visibility": "requires_independent_live_UI_observation",
        },
    )
    if measured["status"] != "passed":
        raise ValueError("checkpoint_control_visibility_hold_failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--producer-python", type=Path)
    parser.add_argument(
        "--operation",
        required=True,
        choices=(
            "prefix",
            "resume",
            "observe",
            "native",
            "seal",
            "retrieve-worker",
            "restore-worker",
        ),
    )
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--artifact-id", type=int)
    args = parser.parse_args()
    probe = probe_module()
    probe.require_remote()
    if args.operation in {"native", "seal"}:
        validate_control(args.output)
        if args.phase is None:
            parser.error("worker phase required")
        if args.operation == "native":
            import ai09_preparation_worker

            args.worker = args.phase
            ai09_preparation_worker.run(args)
        else:
            consumer().seal_worker(args.source, args.output, args.phase)
    elif args.operation == "prefix":
        prepare_prefix(
            args.source, args.output, args.producer_python or args.source / ".venv/bin/python"
        )
    elif args.operation == "resume":
        if args.artifact_id is None:
            parser.error("explicit artifact id required")
        retrieve_and_resume(
            args.source,
            args.output,
            args.producer_python or args.source / ".venv/bin/python",
            args.artifact_id,
        )
    elif args.operation == "retrieve-worker":
        if args.artifact_id is None:
            parser.error("explicit artifact id required")
        retrieve_worker(args.output, args.artifact_id)
    elif args.operation == "restore-worker":
        restore_worker(args.output)
    else:
        observe(args.output)


if __name__ == "__main__":
    main()
