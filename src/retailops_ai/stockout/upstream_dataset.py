"""Bound historical baseline lineage and equal-grain ablation inputs, no outcomes."""

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import TABLES
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    decode_json,
    read_bytes,
)
from retailops_ai.stockout.feature_contract import FeaturePoint, FeatureValues
from retailops_ai.stockout.feature_dataset import (
    MAX_FEATURE_BYTES,
    MAX_FEATURE_POINTS,
    feature_implementation,
    load_features_input,
    read_sealed_tables,
)
from retailops_ai.stockout.upstream import (
    UPSTREAM_TABLES,
    baseline_code,
    model_version,
    upstream_point,
)
from retailops_ai.stockout.upstream_contract import (
    DEFAULT_UPSTREAM_POLICY,
    UpstreamPoint,
    UpstreamPolicy,
)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def build_upstream(
    curated: Path, features: dict[str, Any], *, policy: UpstreamPolicy = DEFAULT_UPSTREAM_POLICY
) -> dict[str, Any]:
    """Preparation CLI has already fully replayed the base features."""
    document, facts = load_features_input(curated)
    if (
        features["descriptor"]["role"] != "stockout_features"
        or features["descriptor"]["curated_dataset_id"] != document["curated_dataset_id"]
        or features["descriptor"]["implementation"] != feature_implementation()
    ):
        raise SnapshotError("stockout_upstream_feature_parent_or_code_mismatch")
    if len(features["points"]) > MAX_FEATURE_POINTS:
        raise SnapshotError("stockout_upstream_point_limit")
    extra = tuple(name for name in UPSTREAM_TABLES if name not in facts)
    facts.update(read_sealed_tables(curated, document, extra))
    records = {name: facts[name] for name in UPSTREAM_TABLES}
    version = model_version(policy)
    points = []
    last_day = None
    view = None
    base = sorted(
        features["points"], key=lambda p: (p["as_of"], p["product_id"], p["stock_location_id"])
    )
    if len({(p["product_id"], p["stock_location_id"], p["as_of"]) for p in base}) != len(base):
        raise SnapshotError("stockout_upstream_duplicate_physical_origin")
    for raw in base:
        point = FeaturePoint.model_validate_json(canonical_json(raw))
        if point.as_of.date() != last_day:
            last_day = point.as_of.date()
            view = OriginFeatures({name: records[name] for name in TABLES}, make_origin(last_day))
        points.append(
            upstream_point(
                records,
                product=point.product_id,
                stock=point.stock_location_id,
                as_of=point.as_of,
                policy=policy,
                version=version,
                view=view,
            ).model_dump(mode="json")
        )
    points.sort(key=lambda p: (p["product_id"], p["stock_location_id"], p["as_of"]))
    eligible = {
        (p["product_id"], p["stock_location_id"], p["as_of"])
        for p in base
        if p["status"] == "eligible"
    }
    available = sum(
        p["status"] == "available"
        and (p["product_id"], p["stock_location_id"], p["as_of"]) in eligible
        for p in points
    )
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_upstream",
        data_class="features",
        feature_dataset_id=features["feature_dataset_id"],
        curated_dataset_id=document["curated_dataset_id"],
        source_dataset_id=features["descriptor"]["source_dataset_id"],
        qualification_id=features["descriptor"]["qualification_id"],
        policy=policy.model_dump(mode="json"),
        implementation=feature_implementation(),
        forecast_code=baseline_code(),
        upstream_model_version=version,
        rows=len(points),
        points_sha256=digest(points),
    )
    result = dict(
        upstream_id="upstream-sha256-" + digest(descriptor),
        descriptor=descriptor,
        points=points,
        report=dict(
            statuses=dict(sorted(Counter(p["status"] for p in points).items())),
            reasons=dict(sorted(Counter(p["reason"] for p in points if p["reason"]).items())),
            base_eligible_rows=len(eligible),
            base_eligible_with_forecast=available,
            upstream_lineage_status="passed",
            upstream_forecast_ready=bool(eligible) and available == len(eligible),
            outcomes_used_for_selection=False,
            parameters_fitted=False,
            model_ready=False,
        ),
    )
    if len(canonical_json(result)) + 1 > MAX_FEATURE_BYTES:
        raise SnapshotError("stockout_upstream_output_byte_limit")
    return result


def verify_upstream(target: Path, curated: Path, features: dict[str, Any]) -> dict[str, Any]:
    stored = decode_json(
        read_bytes(target.parent, target.name, MAX_FEATURE_BYTES), limit=MAX_FEATURE_BYTES
    )
    policy = UpstreamPolicy.model_validate(stored["descriptor"]["policy"])
    expected = build_upstream(curated, features, policy=policy)
    if stored != expected:
        raise SnapshotError("stockout_upstream_full_replay_mismatch")
    return expected


def build_comparison(features: dict[str, Any], upstream: dict[str, Any]) -> dict[str, Any]:
    """Keep all physical keys and base statuses identical; nullable forecasts are explicit."""
    if upstream["descriptor"]["feature_dataset_id"] != features["feature_dataset_id"]:
        raise SnapshotError("stockout_comparison_feature_pin_mismatch")
    indexed = {
        (p["product_id"], p["stock_location_id"], p["as_of"]): UpstreamPoint.model_validate_json(
            canonical_json(p)
        )
        for p in upstream["points"]
    }
    keys = {(p["product_id"], p["stock_location_id"], p["as_of"]) for p in features["points"]}
    if (
        len(indexed) != len(upstream["points"])
        or len(keys) != len(features["points"])
        or keys != set(indexed)
    ):
        raise SnapshotError("stockout_comparison_physical_grain_mismatch")
    rows = []
    for raw in sorted(
        features["points"], key=lambda p: (p["product_id"], p["stock_location_id"], p["as_of"])
    ):
        feature = FeaturePoint.model_validate_json(canonical_json(raw))
        forecast = indexed[(raw["product_id"], raw["stock_location_id"], raw["as_of"])]
        units = forecast.forecast_units_7d
        quantity = feature.values.available_qty
        rows.append(
            dict(
                product_id=feature.product_id,
                stock_location_id=feature.stock_location_id,
                as_of=raw["as_of"],
                status=feature.status,
                reason=feature.reason,
                base=feature.values.model_dump(mode="json"),
                upstream=dict(
                    forecast_units_7d=units,
                    forecast_days_of_supply=7 * quantity / units
                    if quantity is not None and units
                    else None,
                    forecast_unavailable=int(forecast.status != "available"),
                ),
            )
        )
    columns = list(FeatureValues.model_fields)
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_comparison_inputs",
        data_class="features",
        feature_dataset_id=features["feature_dataset_id"],
        upstream_id=upstream["upstream_id"],
        variants={
            "without_upstream": columns,
            "with_upstream": columns
            + ["forecast_units_7d", "forecast_days_of_supply", "forecast_unavailable"],
        },
        common_grain_sha256=digest(sorted(keys)),
        rows_sha256=digest(rows),
        rows=len(rows),
        preprocessing="retain_nullable_values_fit_only_in_training_later",
    )
    result = dict(
        comparison_id="comparison-sha256-" + digest(descriptor),
        descriptor=descriptor,
        rows=rows,
        report=dict(
            common_keys_identical=True,
            model_specific_drops=0,
            preprocessing_fitted=False,
            ablation_model_results="pending",
            upstream_forecast_ready=upstream["report"]["upstream_forecast_ready"],
            model_ready=False,
        ),
    )
    if len(canonical_json(result)) + 1 > MAX_FEATURE_BYTES:
        raise SnapshotError("stockout_comparison_output_byte_limit")
    return result
