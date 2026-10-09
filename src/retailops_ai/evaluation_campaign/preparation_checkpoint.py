"""Lossless completed diagnostic stages with independent native completion evidence.

The existing forecast archive is reused strictly as a bounded storage format.
AI09 lineage and trusted completion bindings are additional, mandatory checks;
neither its hashes nor its storage receipt constitute scientific qualification.
"""

from __future__ import annotations

import math
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import preparation_witness as native
from retailops_ai.forecasting.functional_v12_archive import (
    restore_checkpoint,
    seal_checkpoint,
    verify_checkpoint,
)
from retailops_ai.source_snapshot.files import checked_directory, read_json, relative_path
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

VERSION = "ai09-completed-preparation-checkpoint-1.0.0"
PHASES = tuple(native.VALIDATORS)
PARENTS = ((), (0,), (0, 1), (2,), (3,))
RESULT_PATHS = dict(
    zip(PHASES, ("directory", "directory", "directory", "destination", "destination"), strict=True)
)
IDENTITY_FIELDS = {
    "scope",
    "plan_sha256",
    "generation_sha256",
    "consumer_commit",
    "producer_commit",
    "consumer_lock_sha256",
    "producer_lock_sha256",
    "exporter_lock_sha256",
    "consumer_runtime_sha256",
    "producer_runtime_sha256",
    "validators_sha256",
}
SCOPE = "isolated_resource_diagnostic_on_previously_exposed_development_dates"


def _digest(value: object, length: int = 64) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None
    )


def validate_identity(identity: dict[str, Any]) -> None:
    if set(identity) != IDENTITY_FIELDS or identity["scope"] != SCOPE:
        raise ValueError("preparation_checkpoint_identity_scope")
    for name in IDENTITY_FIELDS - {"scope", "validators_sha256"}:
        if not _digest(identity[name], 40 if name.endswith("_commit") else 64):
            raise ValueError("preparation_checkpoint_identity_pin")
    validators = identity["validators_sha256"]
    if (
        not isinstance(validators, dict)
        or set(validators) != set(PHASES)
        or any(not _digest(value) for value in validators.values())
    ):
        raise ValueError("preparation_checkpoint_validator_pins")


def _completion(
    phase: str,
    identity: dict[str, Any],
    result: dict[str, Any],
    measurement: dict[str, Any],
    witness: dict[str, Any],
) -> None:
    validate_identity(identity)
    if phase not in PHASES or not isinstance(result.get(RESULT_PATHS[phase]), str):
        raise ValueError("preparation_checkpoint_phase_result")
    if (
        measurement.get("status") != "passed"
        or measurement.get("phase") != phase
        or measurement.get("reason") is not None
        or type(measurement.get("exit_code")) is not int
        or measurement["exit_code"] != 0
    ):
        raise ValueError("preparation_checkpoint_incomplete_phase")
    for name in ("wall_seconds", "sampled_worker_cpu_seconds"):
        value = measurement.get(name)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError("preparation_checkpoint_missing_measured_cost")
    expected = {
        "version": native.VERSION,
        "phase": phase,
        "native_validator": native.VALIDATORS[phase],
        "validator_code_sha256": identity["validators_sha256"][phase],
        "identity_sha256": canonical_sha256(identity),
        "native_validation_completed": True,
        "project_generation_receipt": False,
        "final_test_authorized": False,
        "quality_qualified": False,
    }
    if set(witness) != set(expected) | {
        "output_id",
        "input_ids",
        "validation_reports",
        "output_files",
    } or any(witness.get(key) != value for key, value in expected.items()):
        raise ValueError("preparation_checkpoint_native_validation_binding")
    if any(witness[key] is not value for key, value in expected.items() if type(value) is bool):
        raise ValueError("preparation_checkpoint_native_validation_binding")
    if (
        not isinstance(witness["output_id"], str)
        or not witness["output_id"]
        or not isinstance(witness["input_ids"], list)
        or len(witness["input_ids"]) != len(PARENTS[PHASES.index(phase)])
        or any(not isinstance(value, str) or not value for value in witness["input_ids"])
    ):
        raise ValueError("preparation_checkpoint_native_parent_identity")
    files, reports = witness["output_files"], witness["validation_reports"]
    if (
        not isinstance(files, dict)
        or not 1 <= len(files) <= native.MAX_FILES
        or not isinstance(reports, dict)
        or not reports
    ):
        raise ValueError("preparation_checkpoint_missing_native_inventory")
    total = 0
    for name, ref in files.items():
        relative_path(name)
        if (
            not isinstance(ref, dict)
            or set(ref) != {"size_bytes", "sha256"}
            or not _digest(ref["sha256"])
        ):
            raise ValueError("preparation_checkpoint_native_inventory_format")
        if (
            type(ref["size_bytes"]) is not int
            or not 0 <= ref["size_bytes"] <= native.MAX_FILE_BYTES
        ):
            raise ValueError("preparation_checkpoint_native_inventory_budget")
        total += ref["size_bytes"]
    if total > native.MAX_BYTES or any(
        name not in files or ref != files[name] for name, ref in reports.items()
    ):
        raise ValueError("preparation_checkpoint_native_report_binding")


def _private_tree(root: Path) -> None:
    """Keep new AI09 storage private independently of the caller's umask."""
    root.chmod(0o700)
    for directory, directories, names in os.walk(root, followlinks=False):
        for name in (*directories, *names):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                path.chmod(0o700)
            elif stat.S_ISREG(mode):
                path.chmod(0o600)
            else:
                raise ValueError("preparation_checkpoint_special_file")
    fsync_tree(root)


def seal_stage(
    phase: str,
    directory: Path,
    output: Path,
    *,
    identity: dict[str, Any],
    result: dict[str, Any],
    measurement: dict[str, Any],
    witness: dict[str, Any],
    previous_checkpoint_id: str | None,
) -> tuple[Path, dict[str, Any], dict[str, float]]:
    """Seal only successfully validated work; caller durably retains returned binding."""
    started, cpu_started = time.perf_counter(), time.process_time()
    _completion(phase, identity, result, measurement, witness)
    directory = checked_directory(directory)
    if output.absolute().is_relative_to(directory):
        raise ValueError("preparation_checkpoint_archive_inside_source")
    if result[RESULT_PATHS[phase]] != str(directory):
        raise ValueError("preparation_checkpoint_output_path_binding")
    if (phase == PHASES[0]) != (previous_checkpoint_id is None):
        raise ValueError("preparation_checkpoint_predecessor_required")
    if (
        previous_checkpoint_id is not None
        and re.fullmatch(r"functional-checkpoint-sha256-[0-9a-f]{64}", previous_checkpoint_id)
        is None
    ):
        raise ValueError("preparation_checkpoint_predecessor_id")
    if native.inventory(directory) != witness["output_files"]:
        raise ValueError("preparation_checkpoint_output_changed_after_native_validation")
    lineage = {
        "version": VERSION,
        "phase": phase,
        "identity": identity,
        "previous_checkpoint_id": previous_checkpoint_id,
        "result_sha256": canonical_sha256(result),
        "measurement_sha256": canonical_sha256(measurement),
        "witness_sha256": canonical_sha256(witness),
    }
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(output)
    with tempfile.TemporaryDirectory(prefix=".completion-", dir=output) as temporary:
        evidence = Path(temporary)
        for name, value in (("result", result), ("measurement", measurement), ("witness", witness)):
            path = evidence / (name + ".json")
            path.write_bytes(canonical_bytes(value) + b"\n")
            path.chmod(0o600)
        checkpoint = seal_checkpoint(
            {"data": directory, "evidence": evidence}, output, lineage=lineage
        )
    binding = {"checkpoint_id": checkpoint.name, "lineage": lineage}
    verify_stage(checkpoint, expected=binding)
    _private_tree(checkpoint)
    return (
        checkpoint,
        binding,
        {
            "wall_seconds": time.perf_counter() - started,
            "process_cpu_seconds": time.process_time() - cpu_started,
        },
    )


def verify_stage(checkpoint: Path, *, expected: dict[str, Any]) -> dict[str, Any]:
    """Require an independent trusted binding, never trust an archive's own hashes alone."""
    manifest = verify_checkpoint(checked_directory(checkpoint))
    lineage = manifest["descriptor"]["lineage"]
    if (
        expected != {"checkpoint_id": manifest["checkpoint_id"], "lineage": lineage}
        or lineage.get("version") != VERSION
    ):
        raise ValueError("preparation_checkpoint_trusted_binding_mismatch")
    validate_identity(lineage["identity"])
    files = manifest["descriptor"]["files"]
    if {name for name in files if not name.startswith("data/")} != {
        "evidence/result.json",
        "evidence/measurement.json",
        "evidence/witness.json",
    }:
        raise ValueError("preparation_checkpoint_archive_namespace")
    return manifest


def restore_stage(
    checkpoint: Path,
    output: Path,
    *,
    expected: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    """Restore original bytes and return a separate path translation; never regenerate."""
    started, cpu_started = time.perf_counter(), time.process_time()
    manifest = verify_stage(checkpoint, expected=expected)
    lineage = manifest["descriptor"]["lineage"]
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = checked_directory(output)
    destination = output / manifest["checkpoint_id"]
    with tempfile.TemporaryDirectory(prefix=".ai09-restore-", dir=output) as temporary:
        restored = restore_checkpoint(checkpoint, Path(temporary))
        values = {
            name: read_json(restored / "evidence", name + ".json")
            for name in ("result", "measurement", "witness")
        }
        for name, value in values.items():
            if canonical_sha256(value) != lineage[name + "_sha256"]:
                raise ValueError("preparation_checkpoint_completion_receipt_changed")
        _completion(
            lineage["phase"],
            lineage["identity"],
            values["result"],
            values["measurement"],
            values["witness"],
        )
        if native.inventory(restored / "data") != values["witness"]["output_files"]:
            raise ValueError("preparation_checkpoint_restored_native_inventory_changed")
        _private_tree(restored)
        publish_noreplace(restored, destination)
    result = {**values["result"], RESULT_PATHS[lineage["phase"]]: str(destination / "data")}
    return destination, {
        "phase": lineage["phase"],
        "checkpoint_id": manifest["checkpoint_id"],
        "original_result": values["result"],
        "resumed_result": result,
        "cold_preparation_measurement": values["measurement"],
        "restore_measurement": {
            "wall_seconds": time.perf_counter() - started,
            "process_cpu_seconds": time.process_time() - cpu_started,
        },
        "native_output_id": values["witness"]["output_id"],
        "native_input_ids": values["witness"]["input_ids"],
        "source_regenerated": False,
        "native_validation_repeated": False,
        "verification": "trusted_native_completion_binding_and_complete_retained_bytes",
        "project_generation_receipt": False,
        "final_test_authorized": False,
    }


def restore_chain(
    checkpoints: list[tuple[Path, dict[str, Any]]],
    output: Path,
    *,
    identity: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resume only a contiguous prefix with identical frozen inputs and native parents."""
    validate_identity(identity)
    if not 1 <= len(checkpoints) <= len(PHASES):
        raise ValueError("preparation_checkpoint_chain_length")
    previous = None
    for index, (_, binding) in enumerate(checkpoints):
        lineage = binding.get("lineage", {})
        if (
            lineage.get("phase") != PHASES[index]
            or lineage.get("identity") != identity
            or lineage.get("previous_checkpoint_id") != previous
        ):
            raise ValueError("preparation_checkpoint_chain_changed_or_incomplete")
        previous = binding.get("checkpoint_id")
    receipts: list[dict[str, Any]] = []
    native_outputs: list[str] = []
    # Stage the complete prefix. A late parent mismatch exposes no resumed tree.
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = checked_directory(output)
    destination = output / ("resumed-" + str(previous))
    with tempfile.TemporaryDirectory(prefix=".ai09-prefix-", dir=output) as temporary:
        staging = Path(temporary)
        for index, (checkpoint, binding) in enumerate(checkpoints):
            restored, receipt = restore_stage(checkpoint, staging, expected=binding)
            if receipt["native_input_ids"] != [native_outputs[i] for i in PARENTS[index]]:
                raise ValueError("preparation_checkpoint_native_parent_chain_mismatch")
            native_outputs.append(receipt["native_output_id"])
            field = RESULT_PATHS[receipt["phase"]]
            receipt["resumed_result"][field] = str(destination / restored.name / "data")
            receipts.append(receipt)
        fsync_tree(staging)
        publish_noreplace(staging, destination)
    return receipts
