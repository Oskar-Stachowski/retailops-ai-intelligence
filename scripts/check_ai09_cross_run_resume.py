"""Fixed tiny failure/interruption and separate-run recovery control, never canonical.

The original job genuinely fails after native export. Its always-run finalizer
publishes the immutable attempt snapshot only after all preparation has stopped.
A new job reconstructs its runtime identity before downloading either artifact.
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
import check_ai09_checkpoint_control as control  # noqa: E402

VERSION = "ai09-cross-run-failed-prefix-control-1.0.0"
MODES = ("failed_process", "controller_interruption")
REQUEST_KEYS = {"previous_run_id", "previous_job_id", "prefix_artifact_id", "attempt_artifact_id"}
TRANSFER_SECONDS = 60
MINIMUM_PREFIX_REMAINING = 300
INJECTION_STEP = "Inject an explicit failure after real native export"
FINALIZE_STEP = "Finalize attempt history after all native work has stopped"
ATTEMPT_UPLOAD_STEP = "Preserve the complete terminal attempt for an explicitly selected recovery"


def validate_paths(source: Path, output: Path) -> None:
    if any(
        not path.is_absolute()
        or ".." in path.parts
        or any(p.is_symlink() for p in (path, *path.parents))
        for path in (source, output)
    ):
        raise ValueError("cross_run_control_absolute_owned_paths_required")


def validate_request(request: dict[str, Any]) -> None:
    if set(request) != REQUEST_KEYS or any(type(v) is not int or v <= 0 for v in request.values()):
        raise ValueError("cross_run_control_explicit_positive_artifact_run_job_ids_required")
    if request["previous_run_id"] == int(os.environ["GITHUB_RUN_ID"]):
        raise ValueError("cross_run_control_requires_a_different_workflow_run")


def intent(mode: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    from retailops_ai.data_contracts.identity import canonical_sha256

    if mode not in MODES or len(events) != 8:
        raise ValueError("cross_run_control_exact_qualified_prefix_required")
    return {
        "version": VERSION,
        "mode": mode,
        "phase": "export",
        "prefix_events_sha256": canonical_sha256(events),
        "native_wall_limit_seconds": 30,
        "minimum_prefix_remaining_seconds": MINIMUM_PREFIX_REMAINING,
        "real_native_export_before_injection": True,
        "automatic_retry_authorized": False,
    }


def native_completion(output: Path) -> dict[str, Any]:
    probe = control.probe_module()
    return {
        "version": VERSION,
        "native_export_returned": True,
        "result_sha256": probe.sha(output / "export.json"),
        "witness_sha256": probe.sha(output / "export.witness.json"),
    }


def failing_native(source: Path, output: Path) -> None:
    # This branch runs under Source Python; do not import consumer packages here.
    control.validate_control(output)
    import ai09_preparation_worker as worker

    worker.run(argparse.Namespace(source=source, output=output, worker="export"))
    control.probe_module().write(
        output / "injection-native-completed.json", native_completion(output)
    )
    raise SystemExit(23)


def inject(source: Path, output: Path, mode: str, producer_python: Path) -> None:
    controller, probe = control.consumer(), control.probe_module()
    cold = output / "cold"
    control.validate_control(cold)
    state = controller.execution.inspect(cold)
    if state["status"] != "prepared" or state["remaining_wall_seconds"] < MINIMUM_PREFIX_REMAINING:
        raise ValueError("cross_run_control_prefix_not_ready_or_insufficient_reserve")
    controller.execution.write_once(output / "failure-intent.json", intent(mode, state["events"]))
    command = (
        [
            str(producer_python),
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--source",
            str(source),
            "--output",
            str(cold),
            "--operation",
            "native-failing-worker",
        ]
        if mode == "failed_process"
        else [
            str(producer_python),
            "-I",
            "-B",
            str(ROOT / "scripts/check_ai09_checkpoint_control.py"),
            "--source",
            str(source),
            "--output",
            str(cold),
            "--operation",
            "native",
            "--phase",
            "export",
        ]
    )

    def bounded_monitor(*args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs["deadline"] = min(kwargs["deadline"], time.perf_counter() + 30)
        measured: dict[str, Any] = probe.monitor(*args, **kwargs)
        return measured

    def interrupt_after_reaped_child(measured: dict[str, Any]) -> None:
        if mode != "controller_interruption" or measured["exit_code"] != 0:
            raise ValueError("cross_run_control_unexpected_native_success")
        controller.execution.write_once(
            cold / "injection-native-completed.json", native_completion(cold)
        )
        # monitor has already reaped the owned child and checked its descendants.
        # Abrupt controller exit deliberately leaves the durable started record.
        os._exit(24)

    measured = controller.execution.operate(
        cold,
        phase="export",
        kind="native",
        command=command,
        cwd=ROOT,
        env=controller.environment(cold),
        roots=(output,),
        monitor=bounded_monitor,
        accept=interrupt_after_reaped_child,
    )
    if (
        mode != "failed_process"
        or measured["exit_code"] != 23
        or measured["reason"] != "worker_exit"
    ):
        raise ValueError("cross_run_control_unexpected_failure")
    raise SystemExit(23)


def publish_attempt(output: Path) -> None:
    controller, probe = control.consumer(), control.probe_module()
    from retailops_ai.evaluation_campaign import preparation_attempt

    cold = output / "cold"
    control.validate_control(cold)
    state = controller.execution.inspect(cold)
    expected = probe.read(output / "failure-intent.json")
    mode = expected.get("mode")
    if expected != intent(mode, state["events"][:8]):
        raise ValueError("cross_run_control_injection_intent_changed")
    completion = probe.read(cold / "injection-native-completed.json")
    if completion != native_completion(cold):
        raise ValueError("cross_run_control_native_export_not_completed")
    if (
        mode == "failed_process"
        and (
            state["status"] != "failed"
            or len(state["events"]) != 10
            or state["events"][-1]["measurement"]["exit_code"] != 23
        )
    ) or (
        mode == "controller_interruption"
        and (state["status"] != "unfinished" or len(state["events"]) != 9)
    ):
        raise ValueError("cross_run_control_failure_did_not_match_intent")
    captured = preparation_attempt.snapshot(cold)
    captured.update(control_failure_intent=expected, control_native_completion=completion)
    destination = output / "attempt"
    destination.mkdir(mode=0o700)
    controller.execution.write_once(destination / "attempt.json", captured)
    # prepare_prefix already saved resource.json. Keep that earlier observation,
    # and write the terminal state separately rather than overwrite its receipt.
    controller.execution.write_once(
        output / "terminal-attempt-state.json",
        {
            "version": VERSION,
            **{
                key: value
                for key, value in state.items()
                if key not in {"events", "plan", "identity"}
            },
            "project_journal_initialized": False,
            "stage_ready": False,
        },
    )


def admit_history(
    previous: Path, history: dict[str, Any], request: dict[str, int]
) -> dict[str, Any]:
    controller = control.consumer()
    from retailops_ai.evaluation_campaign import preparation_attempt

    state = controller.execution.inspect(previous)
    captured = history["snapshot"]
    expected = captured.get("control_failure_intent", {})
    steps = history.get("remote", {}).get("job", {}).get("steps", [])
    for name, conclusion in (
        (INJECTION_STEP, "failure"),
        (FINALIZE_STEP, "success"),
        (ATTEMPT_UPLOAD_STEP, "success"),
    ):
        matching = [step for step in steps if step.get("name") == name]
        if len(matching) != 1 or matching[0].get("conclusion") != conclusion:
            raise ValueError("cross_run_control_injection_or_history_publication_not_verified")
    if any(
        step.get("conclusion") not in {"success", "skipped"}
        for step in steps
        if step.get("name") != INJECTION_STEP
    ):
        raise ValueError("cross_run_control_additional_unreviewed_job_failure")
    if (
        state["plan"] != control.control_plan()
        or state["status"] != "prepared"
        or state["completed_phases"] != 2
        or len(state["events"]) != 8
        or state["cost_adjustments"]
        or state["remaining_wall_seconds"] < MINIMUM_PREFIX_REMAINING
        or expected != intent(expected.get("mode"), state["events"])
        or captured.get("control_native_completion", {}).get("native_export_returned") is not True
    ):
        raise ValueError("cross_run_control_only_frozen_injected_failure_may_resume")
    mode = expected["mode"]
    if (
        len(captured["events"]) != (10 if mode == "failed_process" else 9)
        or mode == "failed_process"
        and captured["events"][-1]["measurement"]["exit_code"] != 23
    ):
        raise ValueError("cross_run_control_only_exact_intended_exit_may_resume")
    settled = preparation_attempt.validate_history(
        history,
        identity=state["identity"],
        plan=state["plan"],
        events=state["events"],
        charged_wall_seconds=state["charged_wall_seconds"],
        prior_adjustments=[],
        source_run_id=request["previous_run_id"],
    )
    if state["remaining_wall_seconds"] - settled["charged_wall_seconds"] <= TRANSFER_SECONDS:
        raise ValueError("cross_run_control_insufficient_budget_after_terminal_settlement")
    return settled


def retrieve_worker(output: Path) -> None:
    controller, probe = control.consumer(), control.probe_module()
    import ai09_attempt_transport as terminal
    import ai09_checkpoint_transport as transport

    request = probe.read(output / "recovery-request.json")
    validate_request(request)
    identity = probe.read(output / "expected-identity.json")
    head = os.environ["GITHUB_SHA"]
    if identity["consumer_commit"] != head:
        raise ValueError("cross_run_control_current_head_mismatch")
    receipt = transport.retrieve(
        artifact_id=request["prefix_artifact_id"],
        run_id=request["previous_run_id"],
        head=head,
        name=f"ai09-checkpoint-control-{head}-{request['previous_run_id']}-prefix",
        identity=identity,
        output=output / "download",
        token=os.environ.get("GITHUB_TOKEN"),
        maximum=64 * 1024**2,
    )
    history = terminal.retrieve_history(
        output / "download/prefix",
        output / "attempt-download",
        artifact_id=request["attempt_artifact_id"],
        run_id=request["previous_run_id"],
        job_id=request["previous_job_id"],
        identity=identity,
        token=os.environ.get("GITHUB_TOKEN"),
    )
    settled = admit_history(output / "download/prefix", history, request)
    controller.execution.write_once(output / "retrieved.json", receipt)
    controller.execution.write_once(output / "attempt-settlement.json", settled)


def recover(source: Path, output: Path, producer_python: Path, request: dict[str, int]) -> None:
    controller, probe = control.consumer(), control.probe_module()
    from retailops_ai.evaluation_campaign import preparation_resume

    validate_request(request)
    if output.exists():
        raise ValueError("cross_run_control_fresh_owned_recovery_required")
    output.mkdir(mode=0o700, parents=True)
    # No archive supplies the expected identity: recompute it in this fresh runner.
    controller.initialize(
        source,
        output / "identity-only",
        plan=control.control_plan(),
        producer_python=producer_python,
    )
    identity = probe.read(output / "identity-only/preparation-identity.json")
    controller.execution.write_once(output / "expected-identity.json", identity)
    controller.execution.write_once(output / "recovery-request.json", request)
    measurements = {}
    remaining = float(TRANSFER_SECONDS)
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
            ],
            cwd=ROOT,
            env=environment,
            log=output / (operation + ".log"),
            roots=(output,),
            budgets=control.control_plan()["budgets"],
            deadline=time.perf_counter() + remaining,
        )
        controller.execution.write_once(output / (operation + ".measurement.json"), measured)
        measurements[operation] = measured
        if measured["status"] != "passed":
            raise ValueError("cross_run_control_" + operation + "_failed")
        if operation == "retrieve-worker":
            previous = controller.execution.inspect(output / "download/prefix")
            settled = probe.read(output / "attempt-settlement.json")
            remaining = (
                previous["remaining_wall_seconds"]
                - settled["charged_wall_seconds"]
                - measured["wall_seconds"]
            )
            if remaining <= 0:
                raise ValueError("cross_run_control_cumulative_budget_exhausted")
    restored = probe.read(output / "restored.json")
    receipt = probe.read(output / "retrieved.json")
    history = probe.read(output / "attempt-download/verified-history.json")
    resumed = output / "resumed"
    preparation_resume.resume_prefix(
        output / "download/prefix",
        resumed,
        identity=identity,
        expected_event_sha256=receipt["event_sha256"],
        restored=restored["phases"],
        transport_receipt=receipt,
        transport_measurement=measurements["retrieve-worker"],
        restore_measurement=measurements["restore-worker"],
        retained_archives=restored["retained_archives"],
        attempt_history=history,
    )
    if (output / "cold").exists():
        raise ValueError("cross_run_control_original_paths_must_be_unavailable")
    try:
        for name in control.PHASES[2:]:
            control.phase(source, resumed, name, producer_python)
    finally:
        result = controller.finalize(resumed)
    if result["status"] != "complete" or result["completed_phases"] != 5:
        raise ValueError("cross_run_control_native_recovery_incomplete")
    controller.execution.write_once(
        output / "cross-run-summary.json",
        {
            "version": VERSION,
            "status": "passed",
            "control_commit": identity["consumer_commit"],
            "source_commit": identity["producer_commit"],
            "previous_run_id": request["previous_run_id"],
            "recovery_run_id": int(os.environ["GITHUB_RUN_ID"]),
            "failure_mode": history["snapshot"]["control_failure_intent"]["mode"],
            "identity_reconstructed_before_download": True,
            "original_paths_unavailable": True,
            "source_generation_processes_original": 1,
            "source_generation_processes_recovery": 0,
            "completed_phases": 5,
            "events": len(controller.execution.inspect(resumed)["events"]),
            "cold_prefix_charged_wall_seconds": previous["charged_wall_seconds"],
            "terminal_settlement": settled,
            "transport_measurement": measurements["retrieve-worker"],
            "restore_measurement": measurements["restore-worker"],
            "total_charged_wall_seconds": result["charged_wall_seconds"],
            "remaining_wall_seconds": result["remaining_wall_seconds"],
            "unmeasured_cpu_cost_present": result["unmeasured_cpu_cost_present"],
            "unmeasured_wall_cost_present": result["unmeasured_wall_cost_present"],
            "reserved_unknown_wall_seconds": result["reserved_unknown_wall_seconds"],
            "new_project_fits": 0,
            "fresh_final_reads": 0,
            "project_journal_initialized": False,
            "canonical_diagnostic_dispatched": False,
            "stage_ready": False,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--producer-python", type=Path)
    parser.add_argument(
        "--operation",
        required=True,
        choices=(
            "inject",
            "publish-attempt",
            "recover",
            "retrieve-worker",
            "restore-worker",
            "native-failing-worker",
        ),
    )
    parser.add_argument("--failure-mode", choices=MODES)
    for key in sorted(REQUEST_KEYS):
        parser.add_argument("--" + key.replace("_", "-"), type=int)
    args = parser.parse_args()
    control.probe_module().require_remote()
    validate_paths(args.source, args.output)
    producer_python = args.producer_python or args.source / ".venv/bin/python"
    if args.operation == "native-failing-worker":
        failing_native(args.source, args.output)
    elif args.operation == "inject":
        if args.failure_mode is None:
            parser.error("explicit controlled failure mode required")
        inject(args.source, args.output, args.failure_mode, producer_python)
    elif args.operation == "publish-attempt":
        publish_attempt(args.output)
    elif args.operation == "retrieve-worker":
        retrieve_worker(args.output)
    elif args.operation == "restore-worker":
        control.restore_worker(args.output)
    else:
        recover(
            args.source,
            args.output,
            producer_python,
            {key: getattr(args, key) for key in REQUEST_KEYS},
        )


if __name__ == "__main__":
    main()
