"""Deterministic synthetic metadata examples, not observed source data or model runs."""

import hashlib
import json
from datetime import date, timedelta
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256, logical_rows_sha256
from retailops_ai.data_contracts.registry import validate_document


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def valid(family: str, payload: dict[str, Any]) -> dict[str, Any]:
    return validate_document(family, json.dumps(payload).encode()).model_dump(mode="json")


def provenance(source: bool = False) -> dict[str, Any]:
    return {
        "repository": "retailops-cloud-native-platform" if source else "retailops-ai-intelligence",
        "commit_sha": "1" * 40,
        "code_state": "clean",
        "dirty_patch_sha256": None,
        "dependency_lock_sha256": sha("synthetic-fixture-lock"),
        "transform_version": "fixture-v1",
    }


def manifest(
    role: str,
    parents: dict[str, Any],
    content_hash: str,
    rows: int,
    *,
    date_from: str = "2026-05-01",
    date_to: str = "2026-08-31",
) -> dict[str, Any]:
    names = {
        "source_dataset_id",
        "curated_dataset_id",
        "feature_set_id",
        "label_dataset_id",
        "split_id",
        "model_id",
        "inference_run_id",
    }
    classification = "facts" if role == "source" else role
    descriptor = {
        "canonicalization_version": "retailops-canonical-json-v1",
        "role": role,
        "classification": classification,
        "parents": {name: parents.get(name) for name in sorted(names)},
        "config": {
            "seed": 42,
            "scenario": "schema-fixture",
            "business_timezone": "UTC",
            "calendar_version": "calendar-utc-v1",
            "contract_version": "1.0",
            "requested_parameters": {"horizon_days": None, "products": 1},
            "resolved_parameters": {"horizon_days": 14, "products": 1},
        },
        "provenance": provenance(role == "source"),
        "logical_content_sha256": content_hash,
    }
    date_field = {
        "features": "forecast_origin",
        "labels": "target_date",
        "split": "origin_date",
        "predictions": "target_date",
    }.get(role, "business_date")
    return valid(
        "dataset",
        {
            "schema_version": "1.0",
            "contract_type": "dataset",
            "dataset_id": role + "-sha256-" + canonical_sha256(descriptor),
            "identity": descriptor,
            "classification": classification,
            "generated_at": "2026-09-01T00:00:00Z",
            "watermarks": {"daily_observations": "2026-08-31T23:59:59Z"},
            "artifacts": [
                {
                    "path": role + "/fixture.parquet",
                    "byte_sha256": sha("illustrative-bytes-" + role),
                    "size_bytes": 128,
                    "rows": rows,
                    "schema_version": "1.0",
                    "logical_table": role,
                    "classification": classification,
                    "grain": [
                        "product_id",
                        "selling_location_id",
                        "channel",
                        "forecast_origin",
                        "target_date",
                    ]
                    if role in {"features", "labels", "predictions"}
                    else ["origin_date"]
                    if role == "split"
                    else ["product_id", "selling_location_id", "channel", date_field],
                    "date_field": date_field,
                    "date_from": date_from,
                    "date_to": date_to,
                }
            ],
            "readiness": {
                "forecast": "passed",
                "inventory_ready": False,
                "anomaly": "not_ready",
                "stockout": "not_ready",
                "policy_version": "synthetic-fixture-only-v1",
            },
        },
    )


def forecast_key(origin_date: str, horizon: int) -> dict[str, Any]:
    return {
        "product_id": "p-101",
        "selling_location_id": "s-03",
        "channel": "store",
        "forecast_origin": origin_date + "T23:59:59Z",
        "business_timezone": "UTC",
        "cutoff_policy": "end_of_day_second_v1",
        "target_date": (date.fromisoformat(origin_date) + timedelta(days=horizon)).isoformat(),
        "horizon_days": horizon,
    }


def feature_record(key: dict[str, Any], lineage: dict[str, Any], feature_id: str) -> dict[str, Any]:
    values = []
    for name, kind, value in [
        ("lag_1_units", "observed", 0),
        ("target_weekday", "calendar", date.fromisoformat(key["target_date"]).weekday()),
        ("planned_price_minor_units", "known_plan", 1025),
    ]:
        values.append(
            {
                "name": name,
                "kind": kind,
                "source_class": "facts" if kind == "observed" else kind,
                "source_dataset_id": lineage["source_dataset_id"],
                "status": "available",
                "value": value,
                "source_available_at": key["forecast_origin"],
                "observed_through_date": key["forecast_origin"][:10]
                if kind == "observed"
                else None,
                "effective_date": key["target_date"] if kind != "observed" else None,
                "currency": "PLN" if name == "planned_price_minor_units" else None,
                "reason": None,
            }
        )
    return valid(
        "feature",
        {
            "schema_version": "1.0",
            "contract_type": "feature",
            "feature_set_id": feature_id,
            "lineage": lineage,
            "key": key,
            "allowlist_version": "forecast-observed-sales-v1",
            "values": values,
        },
    )


def build_examples() -> dict[str, dict[str, Any]]:
    source = manifest("source", {}, sha("illustrative-source-logical-rows"), 2)
    curated = manifest(
        "curated",
        {"source_dataset_id": source["dataset_id"]},
        sha("illustrative-curated-logical-rows"),
        2,
    )
    lineage = {
        "source_dataset_id": source["dataset_id"],
        "curated_dataset_id": curated["dataset_id"],
    }
    keys = [forecast_key("2026-05-30", 1), forecast_key("2026-08-22", 3)]
    features = [feature_record(key, lineage, "features-sha256-" + "0" * 64) for key in keys]
    feature_manifest = manifest(
        "features",
        lineage,
        logical_rows_sha256(
            [{k: v for k, v in row.items() if k != "feature_set_id"} for row in features],
            keys=["key"],
        ),
        2,
        date_from="2026-05-30",
        date_to="2026-08-22",
    )
    for row in features:
        row["feature_set_id"] = feature_manifest["dataset_id"]
    labels = [
        valid(
            "label",
            {
                "schema_version": "1.0",
                "contract_type": "label",
                "label_dataset_id": "labels-sha256-" + "0" * 64,
                "lineage": lineage,
                "key": key,
                "target_type": "observed_sales_units",
                "status": "eligible" if i == 0 else "censored",
                "observed_sales_units": 0 if i == 0 else None,
                "label_available_at": "2026-05-31T23:59:59Z" if i == 0 else None,
                "reason": None if i == 0 else "incomplete_window",
            },
        )
        for i, key in enumerate(keys)
    ]
    label_manifest = manifest(
        "labels",
        lineage,
        logical_rows_sha256(
            [{k: v for k, v in row.items() if k != "label_dataset_id"} for row in labels],
            keys=["key"],
        ),
        2,
        date_from="2026-05-31",
        date_to="2026-08-25",
    )
    for row in labels:
        row["label_dataset_id"] = label_manifest["dataset_id"]
    split_payload = {
        "schema_version": "1.0",
        "contract_type": "split",
        "lineage": lineage,
        "feature_set_id": feature_manifest["dataset_id"],
        "label_dataset_id": label_manifest["dataset_id"],
        "protocol": "fixed_origin",
        "max_horizon_days": 14,
        "purge_days": 14,
        "train": {"start": "2026-05-01", "end": "2026-05-31"},
        "validation": {"start": "2026-06-15", "end": "2026-06-30"},
        "calibration": {"start": "2026-07-15", "end": "2026-07-20"},
        "test": {"start": "2026-08-04", "end": "2026-08-31"},
    }
    split_manifest = manifest(
        "split",
        {
            **lineage,
            "feature_set_id": feature_manifest["dataset_id"],
            "label_dataset_id": label_manifest["dataset_id"],
        },
        canonical_sha256(split_payload),
        1,
    )
    split = valid("split", {**split_payload, "split_id": split_manifest["dataset_id"]})
    training_id, inference_id = "run-" + "3" * 32, "run-" + "4" * 32
    model_payload = {
        "schema_version": "1.0",
        "contract_type": "model",
        "model_name": "retailops-demand-forecast",
        "model_version": "1",
        "target_type": "observed_sales_units",
        "feature_schema_version": "1.0",
        "feature_allowlist_version": "forecast-observed-sales-v1",
        "model_family": "baseline",
        "training_run_id": training_id,
        "training_lineage": lineage,
        "feature_set_id": feature_manifest["dataset_id"],
        "label_dataset_id": label_manifest["dataset_id"],
        "split_id": split["split_id"],
        "training_cutoff": "2026-06-14T23:59:59Z",
        "selection_cutoff": "2026-08-03T23:59:59Z",
        "model_seed": 42,
        "artifact_sha256": sha("illustrative-model-bytes"),
        "config_sha256": sha("illustrative-model-config"),
        "provenance": provenance(),
    }
    model = valid(
        "model", {**model_payload, "model_id": "model-sha256-" + canonical_sha256(model_payload)}
    )
    train = valid(
        "run",
        {
            "schema_version": "1.0",
            "contract_type": "run",
            "run_id": training_id,
            "run_type": "training",
            "status": "succeeded",
            "attempt": 1,
            "requested_at": "2026-08-23T00:00:00Z",
            "started_at": "2026-08-23T00:01:00Z",
            "completed_at": "2026-08-23T00:02:00Z",
            "requested_by": "fixture-pipeline",
            "input_ref": {
                **lineage,
                "feature_set_id": feature_manifest["dataset_id"],
                "label_dataset_id": label_manifest["dataset_id"],
                "split_id": split["split_id"],
                "as_of_time": model["training_cutoff"],
            },
            "resolved_model": None,
            "output_ref": {"kind": "model", "artifact_id": model["model_id"], "complete": True},
            "error": None,
        },
    )
    prediction_payload = {
        "schema_version": "1.0",
        "contract_type": "prediction",
        "key": keys[1],
        "target_type": "observed_sales_units",
        "unit_of_measure": "unit",
        "predicted_units": 0.0,
        "interval": None,
        "interval_reason": "not_calibrated",
        "lineage": lineage,
        "feature_set_id": feature_manifest["dataset_id"],
        "inference_run_id": inference_id,
        "model": model,
        "release_id": "synthetic-release-fixture",
        "quality_status": "passed",
    }
    prediction_manifest = manifest(
        "predictions",
        {
            **lineage,
            "feature_set_id": feature_manifest["dataset_id"],
            "model_id": model["model_id"],
            "inference_run_id": inference_id,
        },
        logical_rows_sha256([prediction_payload], keys=["key"]),
        1,
        date_from="2026-08-25",
        date_to="2026-08-25",
    )
    prediction = valid(
        "prediction",
        {
            **prediction_payload,
            "prediction_id": "prediction-sha256-" + canonical_sha256(prediction_payload),
            "prediction_dataset_id": prediction_manifest["dataset_id"],
            "generated_at": "2026-08-23T00:04:00Z",
            "freshness_status": "unknown",
        },
    )
    run = valid(
        "run",
        {
            "schema_version": "1.0",
            "contract_type": "run",
            "run_id": inference_id,
            "run_type": "forecast_batch",
            "status": "succeeded",
            "attempt": 1,
            "requested_at": "2026-08-23T00:02:00Z",
            "started_at": "2026-08-23T00:03:00Z",
            "completed_at": "2026-08-23T00:05:00Z",
            "requested_by": "fixture-pipeline",
            "input_ref": {
                **lineage,
                "feature_set_id": feature_manifest["dataset_id"],
                "label_dataset_id": None,
                "split_id": None,
                "as_of_time": keys[1]["forecast_origin"],
            },
            "resolved_model": model,
            "output_ref": {
                "kind": "predictions",
                "artifact_id": prediction_manifest["dataset_id"],
                "complete": True,
            },
            "error": None,
        },
    )
    request = valid(
        "tool_request",
        {
            "schema_version": "1.0",
            "contract_type": "tool_request",
            "tool": "get_demand_forecast",
            "as_of": keys[1]["forecast_origin"],
            "scope": {
                "product_ids": ["p-101"],
                "selling_location_ids": ["s-03"],
                "channel": "store",
                "target_from": "2026-08-23",
                "target_to": "2026-08-31",
            },
            "limit": 20,
        },
    )
    result = valid(
        "tool_result",
        {
            "schema_version": "1.0",
            "contract_type": "tool_result",
            "tool": "get_demand_forecast",
            "request": request,
            "status": "ok",
            "as_of": request["as_of"],
            "freshness_status": "unknown",
            "source_ref": prediction_manifest["dataset_id"],
            "items": [prediction],
            "error": None,
        },
    )
    bundle = valid(
        "bundle",
        {
            "schema_version": "1.0",
            "contract_type": "bundle",
            "datasets": [
                source,
                curated,
                feature_manifest,
                label_manifest,
                split_manifest,
                prediction_manifest,
            ],
            "features": features,
            "labels": labels,
            "splits": [split],
            "models": [model],
            "runs": [train, run],
            "predictions": [prediction],
            "tool_results": [result],
        },
    )
    return {
        "dataset": source,
        "feature": features[1],
        "label": labels[0],
        "prediction": prediction,
        "run": run,
        "tool_request": request,
        "tool_result": result,
        "split": split,
        "model": model,
        "bundle": bundle,
    }
