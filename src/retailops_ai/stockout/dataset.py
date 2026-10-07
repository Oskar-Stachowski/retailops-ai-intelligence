"""Bounded private label artifacts bind verified AI06 handoff and replay code."""

import hashlib
import platform
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_bytes, read_json
from retailops_ai.source_snapshot.importer import verify_snapshot, write_private
from retailops_ai.source_snapshot.inventory_projection import rows
from retailops_ai.source_snapshot.inventory_protocol import read_windows
from retailops_ai.source_snapshot.protocol import Limits
from retailops_ai.source_snapshot.publish import publish_noreplace
from retailops_ai.stockout.contract import (
    DEFAULT_POLICY,
    Eligibility,
    LabelPolicy,
    LedgerMovement,
    StockState,
)
from retailops_ai.stockout.labels import label_window

QUALIFIED_WINDOWS = (
    "evaluation_truth/qualification/simulation_truth/inventory_qualified_windows.json"
)
MAX_LABEL_BYTES = 4 * 1024**2
INPUT_LIMITS = Limits(max_rows=500000, max_bytes=64 * 1024**2)


def implementation() -> dict[str, Any]:
    code = {}
    for package in ("retailops_ai.stockout", "retailops_ai.source_snapshot"):
        for resource in sorted(files(package).iterdir(), key=lambda r: r.name):
            if resource.is_file() and resource.name.endswith(".py"):
                code[f"{package}/{resource.name}"] = hashlib.sha256(
                    resource.read_bytes()
                ).hexdigest()
    code["retailops_ai.data_contracts/common.py"] = hashlib.sha256(
        files("retailops_ai.data_contracts").joinpath("common.py").read_bytes()
    ).hexdigest()
    return {
        "code_files": code,
        "code_sha256": hashlib.sha256(canonical_json(code)).hexdigest(),
        "dependency_lock_sha256": hashlib.sha256(
            files("retailops_ai.source_snapshot").joinpath("dependencies.lock").read_bytes()
            if files("retailops_ai.source_snapshot").joinpath("dependencies.lock").is_file()
            else (Path(__file__).resolve().parents[3] / "uv.lock").read_bytes()
        ).hexdigest(),
        "python_version": platform.python_version(),
    }


def build_labels(
    source: Path, *, allow_evaluation_truth: bool = False, policy: LabelPolicy = DEFAULT_POLICY
) -> dict[str, Any]:
    """Qualification decides active/covered windows; ledger independently decides the label."""
    if not allow_evaluation_truth:
        raise SnapshotError("stockout_labels_require_evaluation_truth_opt_in")
    snapshot = verify_snapshot(
        source,
        allow_evaluation_truth=True,
        required_use_cases=("inventory_source",),
        limits=INPUT_LIMITS,
    )
    manifest = snapshot.manifest
    if (
        manifest["schema_version"] != "1.1.0"
        or not manifest["descriptor"]["include_evaluation_truth"]
    ):
        raise SnapshotError("stockout_labels_require_inventory_private_snapshot_11")
    context = manifest["source"]["descriptor"]["context"]
    projection = context["projection"]
    if (
        any(
            projection[k] != getattr(policy, k)
            for k in ("stock_measure", "reservation_policy", "episode_policy")
        )
        or projection["diagnostic_horizon_days"] != policy.horizon_days
    ):
        raise SnapshotError("stockout_label_policy_source_mismatch")
    qualified = read_windows(source, QUALIFIED_WINDOWS)
    if len(qualified) > policy.max_windows:
        raise SnapshotError("stockout_label_window_limit")
    tables = {t["table"]: t for t in manifest["tables"]}
    if tables["inventory_ledger"]["row_count"] > policy.max_ledger_rows:
        raise SnapshotError("stockout_ledger_row_limit")
    positions: dict[tuple[str, str], list[LedgerMovement]] = defaultdict(list)
    for row in rows(source, tables["inventory_ledger"], INPUT_LIMITS):
        movement = LedgerMovement.model_validate(
            {
                **{
                    k: row[k]
                    for k in (
                        "product_id",
                        "stock_location_id",
                        "occurred_at",
                        "available_at",
                        "sequence",
                        "quantity_delta",
                        "movement_type",
                    )
                },
                "event_id": row["inventory_event_id"],
            }
        )
        positions[movement.product_id, movement.stock_location_id].append(movement)
    snapshots = {
        (row["product_id"], row["stock_location_id"], row["snapshot_at"]): row
        for row in rows(source, tables["inventory_daily_snapshots"], INPUT_LIMITS)
    }
    coverage = {
        row["coverage_id"]: row
        for row in rows(source, tables["inventory_history_coverage"], INPUT_LIMITS)
    }
    points = []
    for window in qualified:
        origin = datetime.fromisoformat(window["origin"])
        key = window["product_id"], window["stock_location_id"]
        raw = snapshots.get((*key, origin))
        if raw is None:
            raise SnapshotError("stockout_qualified_origin_snapshot_missing")
        state = StockState.model_validate(
            {
                "product_id": key[0],
                "stock_location_id": key[1],
                "snapshot_at": raw["snapshot_at"],
                "status": raw["status"],
                **{k: raw[k] for k in ("on_hand", "reserved_qty", "available_qty")},
                "available_at": max(raw["snapshot_at"], raw["source_available_at"])
                if raw["status"] == "known" and raw["source_available_at"] is not None
                else None,
            }
        )
        certificate = coverage.get(window["inventory_coverage_id"])
        if (
            certificate is not None
            and (certificate["product_id"], certificate["stock_location_id"]) != key
        ):
            raise SnapshotError("stockout_coverage_physical_grain_mismatch")
        evidence = Eligibility.model_validate(
            {
                "reason": window["reason"],
                "covered_from_at": certificate["covered_from_at"] if certificate else None,
                "covered_through_at": certificate["covered_through_at"] if certificate else None,
                "available_at": max(
                    certificate["available_at"],
                    datetime.fromisoformat(window["label_available_at"]),
                )
                if certificate and window["label_available_at"]
                else None,
                "truth_delay_seconds": projection["truth_delay_seconds"],
            }
        )
        point = label_window(
            state,
            positions[key],
            as_of=origin,
            evaluated_at=datetime.fromisoformat(window["evaluated_at"]),
            eligibility=evidence,
            policy=policy,
        )
        if (point.status, point.incident_stockout) != (
            window["status"],
            window["incident_stockout"],
        ):
            raise SnapshotError("stockout_qualified_label_disagrees_with_ledger")
        if point.status == "evaluable" and point.label_available_at != datetime.fromisoformat(
            window["label_available_at"]
        ):
            raise SnapshotError("stockout_qualified_label_availability_mismatch")
        points.append(point.model_dump(mode="json"))
    points.sort(key=lambda r: (r["product_id"], r["stock_location_id"], r["as_of"]))
    descriptor = {
        "schema_version": "1.0.0",
        "role": "stockout_labels",
        "data_class": "labels",
        "target": "incident_stockout_7d",
        "grain": ["product_id", "stock_location_id", "as_of"],
        "source_dataset_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "qualification_id": manifest["descriptor"]["parent_qualification_id"],
        "qualification_descriptor": manifest["descriptor"]["qualification"],
        "policy": policy.model_dump(mode="json"),
        "input_limits": {"max_rows": INPUT_LIMITS.max_rows, "max_bytes": INPUT_LIMITS.max_bytes},
        "output_byte_limit": MAX_LABEL_BYTES,
        "implementation": implementation(),
        "rows": len(points),
        "points_sha256": hashlib.sha256(canonical_json(points)).hexdigest(),
    }
    result = {
        "label_dataset_id": "labels-sha256-"
        + hashlib.sha256(canonical_json(descriptor)).hexdigest(),
        "descriptor": descriptor,
        "points": points,
        "report": {
            "statuses": dict(sorted(Counter(p["status"] for p in points).items())),
            "reasons": dict(sorted(Counter(p["reason"] for p in points if p["reason"]).items())),
            "positive_labels": sum(p["incident_stockout"] == 1 for p in points),
            "negative_labels": sum(p["incident_stockout"] == 0 for p in points),
            "label_ledger_replay": "passed",
            "model_ready": False,
            "scope": "bounded qualified labels; no features, splits, calibration or model acceptance",
        },
    }
    if len(canonical_json(result)) > MAX_LABEL_BYTES:
        raise SnapshotError("stockout_label_output_byte_limit")
    return result


def write_labels(document: dict[str, Any], target: Path) -> str:
    """Publish one private immutable JSON; never overwrite a different artifact."""
    raw = canonical_json(document) + b"\n"
    if len(raw) > MAX_LABEL_BYTES:
        raise SnapshotError("stockout_label_output_byte_limit")
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise SnapshotError("stockout_label_symlink_output")
    if ".." in target.parts:
        raise SnapshotError("stockout_label_unsafe_output_path")
    if target.exists():
        if read_bytes(target.parent, target.name, MAX_LABEL_BYTES) != raw:
            raise SnapshotError("stockout_label_immutable_output_conflict")
        return "reused"
    with tempfile.TemporaryDirectory(prefix=".stockout-labels-", dir=target.parent) as tmp:
        staged = Path(tmp) / "labels.json"
        write_private(staged, raw)
        try:
            publish_noreplace(staged, target)
        except FileExistsError:
            if read_bytes(target.parent, target.name, MAX_LABEL_BYTES) != raw:
                raise SnapshotError("stockout_label_immutable_output_conflict") from None
            return "reused"
    return "published"


def verify_labels(
    target: Path, source: Path, *, allow_evaluation_truth: bool = False
) -> dict[str, Any]:
    document = read_json(target.parent, target.name)
    if not isinstance(document, dict) or not isinstance(document.get("descriptor"), dict):
        raise SnapshotError("invalid_stockout_label_document")
    policy = LabelPolicy.model_validate(document["descriptor"].get("policy"))
    replay = build_labels(source, allow_evaluation_truth=allow_evaluation_truth, policy=policy)
    if document != replay:
        raise SnapshotError("stockout_label_full_replay_mismatch")
    return replay
