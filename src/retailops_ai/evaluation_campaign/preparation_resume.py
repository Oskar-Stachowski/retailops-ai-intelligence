"""Resume a verified, deliberately stopped prefix without resetting measured cost.

Only fully measured successful prefixes are accepted here. Failed/interrupted
remote attempts need a separate complete attempt-history settlement; they cannot
be silently replaced by a previously uploaded successful prefix.
"""

from __future__ import annotations

import hashlib
import stat
import tempfile
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints
from retailops_ai.evaluation_campaign import preparation_execution as execution
from retailops_ai.source_snapshot.files import checked_directory, read_json, regular_file
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace


def retain_prefix_archives(previous: Path, output: Path) -> dict[str, Any]:
    """Copy and verify inherited archives inside the caller's guarded restore worker."""
    state = execution.inspect(previous)
    count = state["completed_phases"]
    if (
        state["status"] != "prepared"
        or not 1 <= count < len(checkpoints.PHASES)
        or len(state["events"]) != count * 4
        or not output.is_absolute()
        or ".." in output.parts
        or output.exists()
    ):
        raise ValueError("preparation_resume_archive_retention_input")
    checked_directory(output.parent)
    bindings = {}
    files = {}
    copied = 0
    with tempfile.TemporaryDirectory(prefix=".retained-prefix-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for phase in checkpoints.PHASES[:count]:
            binding = read_json(previous, phase + ".checkpoint.json")
            identifier = binding["checkpoint_id"]
            if (
                not isinstance(identifier, str)
                or not identifier.startswith("functional-checkpoint-sha256-")
                or not checkpoints._digest(identifier.removeprefix("functional-checkpoint-sha256-"))
            ):
                raise ValueError("preparation_resume_archive_identifier")
            source = previous / "checkpoints" / phase / identifier
            destination = staging / phase / identifier
            destination.parent.mkdir(mode=0o700)
            destination.mkdir(mode=0o700)
            for name in ("checkpoint_manifest.json", "payload.tar.gz"):
                digest, size = hashlib.sha256(), 0
                target = destination / name
                with regular_file(source, name) as stream, target.open("xb") as saved:
                    while chunk := stream.read(1024**2):
                        size += len(chunk)
                        copied += len(chunk)
                        if copied > 4 * 1024**3:
                            raise ValueError("preparation_resume_archive_retention_budget")
                        digest.update(chunk)
                        saved.write(chunk)
                target.chmod(0o600)
                info = target.stat()
                files[target.relative_to(staging).as_posix()] = {
                    "size_bytes": size,
                    "sha256": digest.hexdigest(),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "mtime_ns": info.st_mtime_ns,
                }
            # Check the actual copied bytes against the independent native binding.
            checkpoints.verify_stage(destination, expected=binding)
            bindings[phase] = canonical_sha256(binding)
        fsync_tree(staging)
        publish_noreplace(staging, output)
    return {
        "version": "ai09-retained-prefix-archives-1.0.0",
        "directory": str(output),
        "identity_sha256": canonical_sha256(state["identity"]),
        "events_sha256": canonical_sha256(state["events"]),
        "bindings_sha256": bindings,
        "files": files,
        "verified_in_guarded_restore_worker": True,
    }


def retained_archive_handoff(
    receipt: dict[str, Any], previous: Path, state: dict[str, Any]
) -> Path:
    """Check the owned worker handoff without a second unmetered archive scan.

    The receipt comes from the successful guarded worker, not an external input.
    File identities detect accidental changes before an atomic same-filesystem move.
    Later bundling still checks complete payload hashes and native bindings.
    """
    count = state["completed_phases"]
    bindings = {
        phase: read_json(previous, phase + ".checkpoint.json")
        for phase in checkpoints.PHASES[:count]
    }
    if (
        receipt.get("version") != "ai09-retained-prefix-archives-1.0.0"
        or receipt.get("verified_in_guarded_restore_worker") is not True
        or receipt.get("identity_sha256") != canonical_sha256(state["identity"])
        or receipt.get("events_sha256") != canonical_sha256(state["events"])
        or receipt.get("bindings_sha256")
        != {phase: canonical_sha256(binding) for phase, binding in bindings.items()}
    ):
        raise ValueError("preparation_resume_archive_handoff_binding")
    root = Path(receipt["directory"])
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("preparation_resume_archive_handoff_path")
    checked_directory(root)
    expected = {
        phase + "/" + binding["checkpoint_id"] + "/" + name
        for phase, binding in bindings.items()
        for name in ("checkpoint_manifest.json", "payload.tar.gz")
    }
    if set(receipt["files"]) != expected:
        raise ValueError("preparation_resume_archive_handoff_inventory")
    actual = set()
    expected_directories = {
        relative
        for phase, binding in bindings.items()
        for relative in (phase, phase + "/" + binding["checkpoint_id"])
    }
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("preparation_resume_archive_handoff_private_directory")
    for path in root.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if (
                path.relative_to(root).as_posix() not in expected_directories
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ValueError("preparation_resume_archive_handoff_inventory")
            continue
        name = path.relative_to(root).as_posix()
        if not stat.S_ISREG(info.st_mode) or name not in expected:
            raise ValueError("preparation_resume_archive_handoff_inventory")
        ref = receipt["files"][name]
        if (
            not checkpoints._digest(ref.get("sha256"))
            or info.st_size != ref.get("size_bytes")
            or info.st_dev != ref.get("device")
            or info.st_ino != ref.get("inode")
            or info.st_mtime_ns != ref.get("mtime_ns")
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ValueError("preparation_resume_retained_archive_changed")
        actual.add(name)
    if actual != expected:
        raise ValueError("preparation_resume_archive_handoff_inventory")
    return root


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
    retained_archives: dict[str, Any],
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
    retained = retained_archive_handoff(retained_archives, previous, state)
    if (
        retained == previous
        or previous.is_relative_to(retained)
        or retained.is_relative_to(previous)
    ):
        raise ValueError("preparation_resume_original_archives_must_be_preserved")
    receipt = {
        "version": "ai09-verified-prefix-resume-1.0.0",
        "original_events_sha256": expected_event_sha256,
        "original_charged_wall_seconds": state["charged_wall_seconds"],
        "transport_receipt": transport_receipt,
        "transport_measurement": transport_measurement,
        "restore_measurement": restore_measurement,
        "restored": restored,
        "retained_archives": retained_archives,
        "checkpoint_storage_directory": str(output / "checkpoints"),
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
        # Archive copying and verification have already been charged to restore.
        # Only transfer the owned durable directory; original archives stay intact.
        publish_noreplace(retained, staging / "checkpoints")
        publish_noreplace(staging, output)
    return receipt
