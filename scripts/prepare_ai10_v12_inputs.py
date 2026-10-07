"""Prepare 102-day AI10 inference parents from an existing immutable Source snapshot."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import build_curated, verify_curated
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.contracts import BatchScope
from retailops_ai.forecast_jobs.inputs import build_inputs_package, verify_inputs_package
from retailops_ai.forecasting.calendar import build_calendar
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.manifests import build_feature_set, input_models, verify_feature_set
from retailops_ai.source_snapshot.files import checked_directory, read_json
from retailops_ai.source_snapshot.importer import import_snapshot

ORIGINAL_LOCK = "33c53d1a1f08d5c90b3b61c79e6aeb732f0eebc6e8be36e735c93ca277492587"


def prepare(snapshot: Path, output: Path, origin: datetime) -> dict[str, Any]:
    """Read all Source tables; select a bounded inference scope without reading model metrics."""
    if origin.tzinfo != UTC or origin.time().isoformat() != "23:59:59":
        raise ValueError("ai10_v12_origin_must_close_utc_day")
    if origin > datetime.now(UTC):
        raise ValueError("ai10_v12_origin_from_future")
    source = read_json(checked_directory(snapshot), "snapshot_manifest.json")
    if len(source["tables"]) != 43 or source["descriptor"]["include_evaluation_truth"]:
        raise ValueError("ai10_v12_complete_label_free_snapshot_required")
    # Never overwrite another invocation's mutable receipt or staging files.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = checked_directory(output)
    started = time.monotonic()
    generated = output / "data/generated"
    imported = import_snapshot(snapshot, generated)
    curated = build_curated(imported.directory, generated)
    manifest = verify_curated(curated.directory)
    parameters = manifest["descriptor"]["source_parameters"]
    if (
        parameters["profile"] != "ai-temporal-smoke"
        or parameters["days"] != 102
        or parameters["forecast_plan_days"] != 14
        or parameters["end_date"] != origin.date().isoformat()
        or parameters["seed"] != 42
        or parameters["business_timezone"] != "UTC"
        or manifest["readiness"]["forecast_source"] != "passed"
    ):
        raise ValueError("ai10_v12_temporal_source_parameters")
    calendar = build_calendar(
        curated.directory, OriginWindow(start=origin.date(), end=origin.date())
    )
    features = build_feature_set(curated.directory, calendar, generated / "features")
    feature_manifest = verify_feature_set(features)
    if feature_manifest.descriptor.code.dependency_lock_sha256 != ORIGINAL_LOCK:
        raise ValueError("ai10_v12_original_dependency_lock_required")
    histories = {
        (row.product_id, row.selling_location_id)
        for row in input_models(features, "history")
        if row.channel == "store" and row.forecast_origin == origin
    }
    products = tuple(sorted({product for product, _ in histories})[:2])
    locations = tuple(sorted({location for _, location in histories})[:2])
    if (
        len(products) != 2
        or len(locations) != 2
        or not {(product, location) for product in products for location in locations} <= histories
    ):
        raise ValueError("ai10_v12_four_complete_histories_required")
    inputs_dir = build_inputs_package(
        features,
        curated.directory,
        generated / "inference-inputs",
        as_of=origin,
        scope=BatchScope(product_ids=products, selling_location_ids=locations, channel="store"),
        horizon_days=14,
    )
    inputs = verify_inputs_package(inputs_dir)
    if len(inputs.rows) != 56 or len(inputs.histories) != 4 or inputs.schema_version != "1.1":
        raise ValueError("ai10_v12_complete_inference_scope_required")
    return dict(
        status="passed",
        scope="new_verified_Source_inference_parents_only",
        snapshot_dir=str(snapshot.resolve()),
        import_dir=str(imported.directory),
        curated_dir=str(curated.directory),
        feature_dir=str(features),
        inputs_dir=str(inputs_dir),
        source_dataset_id=source["source_dataset_id"],
        snapshot_id=source["snapshot_id"],
        profile_id=inputs.profile_id,
        origin=origin.isoformat(),
        history_days=102,
        forecast_plan_days=14,
        rows=len(inputs.rows),
        histories=len(inputs.histories),
        original_dependency_lock_match=True,
        original_training_or_quality_evaluation=False,
        model_refits=0,
        seconds=round(time.monotonic() - started, 2),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    try:
        origin = datetime.fromisoformat(args.origin.replace("Z", "+00:00"))
        result = prepare(args.snapshot, args.output, origin)
        path = args.output / "parents.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_bytes(result) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        print('{"status":"failed","category":"ai10_v12_inference_parent_preparation"}')
        return 1
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("status", "profile_id", "rows", "histories", "model_refits", "seconds")
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
