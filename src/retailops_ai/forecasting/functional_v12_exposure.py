"""Persistent, output-independent reservations and exposure receipts for fresh cohorts.

The registry is shared by campaigns, and its fixed location and starting inventory
belong in the campaign freeze. A new output directory never makes a seen seed fresh.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import SnapshotError, read_json, regular_file


def _validate_freeze(freeze: dict[str, Any]) -> None:
    body = freeze["descriptor"]
    if (
        freeze["freeze_id"] != "functional-v12-freeze-sha256-" + canonical_sha256(body)
        or body["version"] != "forecast-functional-cohort-plan-1.0.0"
        or body["holdout_metrics_evaluated_before_freeze"] is not False
    ):
        raise SnapshotError("cohort_freeze_identity")
    seeds = body["seeds"]
    if (
        not isinstance(seeds, list)
        or not 1 <= len(seeds) <= 256
        or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds)
        or len(set(seeds)) != len(seeds)
        or seeds != sorted(seeds)
        or set(seeds) & set(body["previously_used_seeds"])
    ):
        raise SnapshotError("cohort_seed_inventory_or_previous_exposure")


def make_freeze(descriptor: dict[str, Any]) -> dict[str, Any]:
    """Content identity only; callers must separately verify code, tests and disk receipts."""
    freeze = {
        "freeze_id": "functional-v12-freeze-sha256-" + canonical_sha256(descriptor),
        "descriptor": descriptor,
    }
    _validate_freeze(freeze)
    return freeze


@contextmanager
def _locked(registry: Path) -> Iterator[None]:
    registry.mkdir(parents=True, exist_ok=True)
    if registry.is_symlink():
        raise SnapshotError("cohort_registry_symlink")
    descriptor = os.open(registry / "registry.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    with path.open("xb") as stream:
        stream.write(canonical_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _plans(registry: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(registry.glob("plan-*.json")):
        plan = read_json(registry, path.name)
        _validate_freeze(plan)
        if path.name != "plan-" + plan["freeze_id"] + ".json":
            raise SnapshotError("cohort_registry_plan_filename")
        result.append(plan)
    return result


def reserve_plan(registry: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    """Reserve the entire seed list before generation; a changed method needs new seeds."""
    _validate_freeze(freeze)
    seeds = set(freeze["descriptor"]["seeds"])
    with _locked(registry):
        for existing in _plans(registry):
            if existing["freeze_id"] == freeze["freeze_id"]:
                return existing
            if seeds & set(existing["descriptor"]["seeds"]):
                raise SnapshotError("cohort_seed_already_reserved_for_different_freeze")
        _write_new(registry / ("plan-" + freeze["freeze_id"] + ".json"), freeze)
    return freeze


def exposure_inventory(registry: Path) -> list[dict[str, Any]]:
    if not registry.exists():
        return []
    result = []
    for path in sorted(registry.glob("exposure-*.json")):
        receipt = read_json(registry, path.name)
        body = receipt["descriptor"]
        expected = "cohort-exposure-sha256-" + canonical_sha256(body)
        if (
            receipt["exposure_id"] != expected
            or path.name != "exposure-" + expected + ".json"
            or body["status"] != "holdout_opened_cannot_become_unseen_again"
        ):
            raise SnapshotError("cohort_exposure_identity")
        result.append(receipt)
    return result


def open_holdout(
    registry: Path,
    freeze: dict[str, Any],
    *,
    seed: int,
    source_dataset_id: str,
    snapshot_id: str,
    fitted_recipes_sha256: str,
) -> dict[str, Any]:
    """Persist exposure BEFORE reading holdout outcomes; exact replay is idempotent."""
    _validate_freeze(freeze)
    if (
        seed not in freeze["descriptor"]["seeds"]
        or not source_dataset_id.startswith("source-sha256-")
        or not snapshot_id.startswith("snapshot-sha256-")
        or len(fitted_recipes_sha256) != 64
        or any(c not in "0123456789abcdef" for c in fitted_recipes_sha256)
        or source_dataset_id in freeze["descriptor"]["previously_used_source_ids"]
        or snapshot_id in freeze["descriptor"]["previously_used_snapshot_ids"]
    ):
        raise SnapshotError("cohort_holdout_not_independent_or_not_planned")
    binding = {
        "freeze_id": freeze["freeze_id"],
        "seed": seed,
        "source_dataset_id": source_dataset_id,
        "snapshot_id": snapshot_id,
        "fitted_recipes_sha256": fitted_recipes_sha256,
    }
    with _locked(registry):
        plan_path = registry / ("plan-" + freeze["freeze_id"] + ".json")
        with regular_file(registry, plan_path.name):
            if read_json(registry, plan_path.name) != freeze:
                raise SnapshotError("cohort_plan_not_reserved")
        for existing in exposure_inventory(registry):
            body = existing["descriptor"]
            if any(
                body[name] == binding[name] for name in ("seed", "source_dataset_id", "snapshot_id")
            ):
                if all(body[name] == value for name, value in binding.items()):
                    return existing
                raise SnapshotError("cohort_holdout_already_exposed")
        body = binding | {
            "opened_at": datetime.now(UTC).isoformat(),
            "status": "holdout_opened_cannot_become_unseen_again",
        }
        receipt: dict[str, Any] = {
            "exposure_id": "cohort-exposure-sha256-" + canonical_sha256(body),
            "descriptor": body,
        }
        _write_new(registry / ("exposure-" + receipt["exposure_id"] + ".json"), receipt)
    return receipt


def require_complete_exposure(registry: Path, freeze: dict[str, Any]) -> list[dict[str, Any]]:
    """Completion includes every preregistered seed, including unsuccessful cohorts."""
    _validate_freeze(freeze)
    receipts = [
        row
        for row in exposure_inventory(registry)
        if row["descriptor"]["freeze_id"] == freeze["freeze_id"]
    ]
    if sorted(row["descriptor"]["seed"] for row in receipts) != freeze["descriptor"]["seeds"]:
        raise SnapshotError("cohort_incomplete_preregistered_inventory")
    return receipts
