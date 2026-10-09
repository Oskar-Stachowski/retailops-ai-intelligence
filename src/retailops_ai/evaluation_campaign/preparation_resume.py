"""Resume a verified, deliberately stopped prefix without resetting measured cost.

Only fully measured successful prefixes are accepted here. Failed/interrupted
remote attempts need a separate complete attempt-history settlement; they cannot
be silently replaced by a previously uploaded successful prefix.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.source_snapshot.files import checked_directory, read_json
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def measured_cost(measurement: dict[str, Any]) -> tuple[float, float]:
    if (
        measurement.get("status") != "passed"
        or measurement.get("reason") is not None
        or type(measurement.get("exit_code")) is not int
        or measurement["exit_code"] != 0
    ):
        raise ValueError("preparation_resume_operation_not_complete")
    wall, cpu = measurement.get("wall_seconds"), measurement.get("sampled_worker_cpu_seconds")
    if not execution._nonnegative(wall) or not execution._nonnegative(cpu):
        raise ValueError("preparation_resume_unknown_cost")
    return float(wall), float(cpu)


def resume_prefix(
    previous: Path,
    output: Path,
    *,
    identity: dict[str, Any],
    expected_event_sha256: str,
    restored: list[dict[str, Any]],
    transport_receipt: dict[str, Any],
    transport_measurement: dict[str, Any],
    restore_measurement: dict[str, Any],
) -> dict[str, Any]:
    """Caller supplies independently trusted transport proof and guarded restore result."""
    state = execution.inspect(previous)
    count = state["completed_phases"]
    if (
        state["identity"] != identity
        or state["status"] != "prepared"
        or state["unmeasured_wall_cost_present"]
        or state["unmeasured_cpu_cost_present"]
        or not 1 <= count < len(checkpoints.PHASES)
        or len(state["events"]) != 4 * count
        or canonical_sha256(state["events"]) != expected_event_sha256
        or len(restored) != count
        or transport_receipt.get("verified_github_artifact") is not True
        or transport_receipt.get("event_sha256") != expected_event_sha256
        or transport_receipt.get("identity_sha256") != canonical_sha256(identity)
        or transport_receipt.get("deliberate_prefix_stop") is not True
    ):
        raise ValueError("preparation_resume_untrusted_incomplete_or_failed_history")
    transport_wall, transport_cpu = measured_cost(transport_measurement)
    restore_wall, restore_cpu = measured_cost(restore_measurement)
    if transport_wall + restore_wall >= state["remaining_wall_seconds"]:
        raise ValueError("preparation_resume_budget_exhausted")
    if not output.is_absolute() or output.exists() or ".." in output.parts:
        raise ValueError("preparation_resume_fresh_absolute_output_required")
    checked_directory(output.parent)
    receipt = {
        "version": "ai09-verified-prefix-resume-1.0.0",
        "original_events_sha256": expected_event_sha256,
        "original_charged_wall_seconds": state["charged_wall_seconds"],
        "transport_receipt": transport_receipt,
        "transport_measurement": transport_measurement,
        "restore_measurement": restore_measurement,
        "restored": restored,
        "source_regenerated": False,
        "project_journal_initialized": False,
        "final_test_authorized": False,
    }
    adjustment: dict[str, Any] = {
        "kind": "verified_checkpoint_resume",
        "before_event_count": len(state["events"]),
        "wall_seconds": transport_wall + restore_wall,
        "worker_cpu_seconds_lower_bound": transport_cpu + restore_cpu,
        "receipt_sha256": canonical_sha256(receipt),
    }
    with tempfile.TemporaryDirectory(prefix=".resumed-session-", dir=output.parent) as temporary:
        staging = Path(temporary) / "session"
        execution.initialize(
            staging,
            plan=state["plan"],
            identity=identity,
            cost_adjustments=[*state["cost_adjustments"], adjustment],
        )
        for index, event in enumerate(state["events"]):
            execution.write_once(staging / "events" / f"{index:03d}.json", event)
        (staging / "resume-receipts").mkdir(mode=0o700)
        for prior in state["cost_adjustments"]:
            name = prior["receipt_sha256"] + ".json"
            execution.write_once(
                staging / "resume-receipts" / name, read_json(previous, "resume-receipts/" + name)
            )
        execution.write_once(
            staging / "resume-receipts" / (adjustment["receipt_sha256"] + ".json"), receipt
        )
        for index, entry in enumerate(restored):
            phase = checkpoints.PHASES[index]
            binding = read_json(previous, phase + ".checkpoint.json")
            if (
                entry.get("phase") != phase
                or entry.get("checkpoint_id") != binding["checkpoint_id"]
                or canonical_sha256(entry["original_result"]) != binding["lineage"]["result_sha256"]
                or canonical_sha256(entry["cold_preparation_measurement"])
                != binding["lineage"]["measurement_sha256"]
                or entry.get("source_regenerated") is not False
                or entry.get("native_validation_repeated") is not False
            ):
                raise ValueError("preparation_resume_original_native_completion_mismatch")
            original = entry["original_result"]
            field = checkpoints.RESULT_PATHS[phase]
            changed = entry["resumed_result"]
            if {key: value for key, value in original.items() if key != field} != {
                key: value for key, value in changed.items() if key != field
            }:
                raise ValueError("preparation_resume_result_rewrite_forbidden")
            execution.write_once(staging / (phase + ".json"), changed)
            execution.write_once(staging / (phase + ".checkpoint.json"), binding)
        verified = execution.inspect(staging)
        if (
            verified["charged_wall_seconds"]
            != state["charged_wall_seconds"] + adjustment["wall_seconds"]
        ):
            raise ValueError("preparation_resume_cost_not_preserved")
        fsync_tree(staging)
        publish_noreplace(staging, output)
    return receipt
