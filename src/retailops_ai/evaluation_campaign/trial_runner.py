"""Retrospective checksummed inventory and a preregistered development-only runner."""

import re
import time
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.development import (
    DEFAULT_COMPARISON_POLICY,
    _protocol,
    run_development_comparison,
)
from retailops_ai.evaluation_campaign.development_contract import (
    COMPARISON_HEADS,
    DevelopmentComparisonManifest,
    DevelopmentComparisonPolicy,
    DevelopmentProtocol,
)
from retailops_ai.evaluation_campaign.development_storage import development_parents
from retailops_ai.evaluation_campaign.trial_contract import AttemptSnapshot
from retailops_ai.evaluation_campaign.trial_registry import finish, inspect, reserve
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    read_bytes,
    read_json,
    relative_path,
)


def development_protocol(
    *,
    features: Path,
    split: Path,
    curated: Path,
    fold_name: str,
    policy: DevelopmentComparisonPolicy = DEFAULT_COMPARISON_POLICY,
) -> DevelopmentProtocol:
    with development_parents(features, split, fold_name, policy.tensorflow.max_windows) as parents:
        feature, manifest, fold, train, validation = parents
        return _protocol(curated, feature, manifest, fold, train, validation, policy)


def snapshot_attempt(output: Path) -> AttemptSnapshot:
    """Observe sealed or partial artifacts without fits, label reads, reload or edits."""
    output = checked_directory(output)
    protocol = DevelopmentProtocol.model_validate_json(read_bytes(output, "protocol.json"))
    protocol_hash = canonical_sha256(protocol.model_dump(mode="json"))
    inventory: dict[str, dict[str, Any]] = {}
    total = 0
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise SnapshotError("trial_snapshot_symlink")
        if not path.is_file():
            if not path.is_dir():
                raise SnapshotError("trial_snapshot_nonregular_file")
            continue
        name = path.relative_to(output).as_posix()
        relative_path(name)
        size, digest = file_hash(output, name)
        total += size
        if total > protocol.policy.max_output_bytes or len(inventory) >= 10000:
            raise SnapshotError("trial_snapshot_output_budget")
        inventory[name] = {"size_bytes": size, "sha256": digest}
    attempt = read_json(output, "attempt.json")
    if (
        attempt.get("final_test_accessed") is not False
        or attempt.get("promotion_allowed") is not False
    ):
        raise SnapshotError("trial_snapshot_not_development_only")
    status = attempt["status"]
    if "manifest.json" in inventory:
        manifest = DevelopmentComparisonManifest.model_validate_json(
            read_bytes(output, "manifest.json")
        )
        if (
            manifest.protocol != protocol
            or status != "completed_development_diagnostic"
            or {k: v for k, v in inventory.items() if k != "manifest.json"}
            != {k: v.model_dump() for k, v in manifest.files.items()}
        ):
            raise SnapshotError("trial_snapshot_completed_inventory_mismatch")
    elif status == "failed":
        pass
    elif status == "running":
        status = "unresolved"
        if "external_interruption.json" in inventory:
            interruption = read_json(output, "external_interruption.json")
            if (
                not (
                    interruption.get("status")
                    in {"interrupted", "externally_interrupted_before_completed_comparison"}
                    or interruption.get("exit_code") == -9
                    and interruption.get("error") == "whole_comparison_budget_exceeded"
                )
                or interruption.get("final_test_accessed") is not False
                or interruption.get("promotion_allowed") is not False
            ):
                raise SnapshotError("trial_snapshot_invalid_interruption")
            status = "interrupted"
    else:
        raise SnapshotError("trial_snapshot_incomplete_manifest")
    events = read_bytes(output, "trials.jsonl", 128 * 1024).splitlines()
    if not events or len(events) > 32:
        raise SnapshotError("trial_snapshot_event_budget")
    starts: set[str] = set()
    completions: set[str] = set()
    for index, line in enumerate(events):
        event = decode_json(line)
        if index == 0:
            if (
                event.get("event") != "protocol_frozen"
                or event.get("protocol_sha256") != protocol_hash
            ):
                raise SnapshotError("trial_snapshot_protocol_event_mismatch")
        elif event.get("event") in {"started", "completed"}:
            model = event.get("model")
            if model not in (*COMPARISON_HEADS, "tensorflow"):
                raise SnapshotError("trial_snapshot_unknown_model")
            if event["event"] == "started":
                if model in starts:
                    raise SnapshotError("trial_snapshot_duplicate_model_start")
                configuration = (
                    protocol.policy.tensorflow.model_dump(mode="json")
                    if model == "tensorflow"
                    else protocol.policy.trees.model.model_dump(mode="json")
                )
                if event.get("configuration") != configuration:
                    raise SnapshotError("trial_snapshot_configuration_mismatch")
                starts.add(model)
            else:
                if model not in starts or model in completions:
                    raise SnapshotError("trial_snapshot_invalid_model_completion")
                completions.add(model)
        elif event.get("event") not in {"failed", "comparison_completed"}:
            raise SnapshotError("trial_snapshot_unknown_event")
    if status == "completed_development_diagnostic" and len(completions) != 4:
        raise SnapshotError("trial_snapshot_missing_completed_models")
    costs = tuple(
        sorted(
            name
            for name in inventory
            if name.endswith(".resources.json")
            or name
            in {
                "resources.json",
                "tensorflow/manifest.json",
                "tensorflow/worker_receipt.json",
                "external_interruption.json",
            }
        )
    )
    return AttemptSnapshot.model_validate_json(
        canonical_bytes(
            {
                "output": str(output),
                "protocol_sha256": protocol_hash,
                "status": status,
                "files": inventory,
                "model_starts": len(starts),
                "model_completions": len(completions),
                "cost_files": costs,
            }
        )
    )


def run_registered_comparison(
    *,
    registry: Path,
    protocol_sha256: str,
    features: Path,
    split: Path,
    curated: Path,
    output: Path,
) -> dict[str, Any]:
    # Reservation is durable before even parent/label verification or output creation.
    reservation = reserve(registry, protocol_sha256, output)
    started = time.monotonic()
    ledger = inspect(registry)
    protocol = next(
        p
        for p in ledger.plan.protocols
        if canonical_sha256(p.model_dump(mode="json")) == protocol_sha256
    )
    try:
        result = run_development_comparison(
            features=features,
            split=split,
            curated=curated,
            fold_name=protocol.fold.name,
            output=output,
            policy=protocol.policy,
            expected_protocol_sha256=protocol_sha256,
        )
    except Exception as exc:
        code = str(exc) if isinstance(exc, SnapshotError) else type(exc).__name__
        if re.fullmatch(r"[A-Za-z0-9_]{1,128}", code) is None:
            code = "registered_comparison_failed"
        snapshot = snapshot_attempt(output) if (output / "attempt.json").exists() else None
        finish(
            registry,
            reservation.attempt_id,
            outcome="failed",
            error_code=code,
            wall_seconds=time.monotonic() - started,
            snapshot=snapshot,
        )
        raise
    snapshot = snapshot_attempt(output)
    finish(
        registry,
        reservation.attempt_id,
        outcome="completed_development_diagnostic",
        wall_seconds=time.monotonic() - started,
        snapshot=snapshot,
    )
    return result | {"registry_attempt_id": reservation.attempt_id}
