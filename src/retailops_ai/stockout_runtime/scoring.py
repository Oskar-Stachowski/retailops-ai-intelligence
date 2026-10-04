"""Pure worker scoring of verified internal points; no paths, truth or mutable aliases."""

from datetime import datetime, timedelta
from typing import Any, cast

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeaturePoint
from retailops_ai.stockout.upstream_contract import UpstreamPoint
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.contract import risk_band
from retailops_ai.stockout_runtime.contracts import (
    FactualFactor,
    RiskBand,
    RiskItem,
    RuntimeLineage,
    RuntimeReleasePin,
    ScoringPolicy,
    ScoringRecipe,
)
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline
from retailops_ai.stockout_selection.pipeline import predict_conditional
from retailops_ai.stockout_training.pipeline import predict


def score_point(
    feature: FeaturePoint,
    *,
    category_id: str | None,
    category_available_at: datetime | None,
    upstream: UpstreamPoint | None,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    release: RuntimeReleasePin,
    lineage: RuntimeLineage,
    run_id: str,
    generated_at: datetime,
) -> RiskItem:
    # These are internal worker inputs, obtained from sealed public preparation.
    # Revalidate even model_copy/construct values before status handling.
    feature = FeaturePoint.model_validate_json(feature.model_dump_json())
    recipe = ScoringRecipe.model_validate_json(recipe.model_dump_json())
    policy = ScoringPolicy.model_validate_json(policy.model_dump_json())
    release = RuntimeReleasePin.model_validate_json(release.model_dump_json())
    lineage = RuntimeLineage.model_validate_json(lineage.model_dump_json())
    if (
        recipe.pin != policy.pin
        or release.recipe_content_sha256 != digest(recipe.model_dump(mode="json"))
        or release.policy_content_sha256 != digest(policy.model_dump(mode="json"))
        or feature.as_of < recipe.pin.selection_known_at
        or (
            category_available_at is not None
            and (
                category_available_at.utcoffset() != timedelta(0)
                or category_available_at > feature.as_of
            )
        )
        or generated_at.utcoffset() != timedelta(0)
        or generated_at < feature.as_of
    ):
        raise ValueError("stockout_runtime_release_policy_or_origin_mismatch")
    if upstream is not None:
        upstream = UpstreamPoint.model_validate_json(upstream.model_dump_json())
        if (feature.product_id, feature.stock_location_id, feature.as_of) != (
            upstream.product_id,
            upstream.stock_location_id,
            upstream.as_of,
        ):
            raise ValueError("stockout_runtime_upstream_physical_key_mismatch")
    values = feature.values
    base = (
        recipe.pipeline.base
        if isinstance(recipe.pipeline, ConditionalRiskPipeline)
        else recipe.pipeline
    )
    age = values.snapshot_age_hours
    probability = None
    band = None
    inventory_freshness = "current"
    reason: str | None = None
    facts: list[FactualFactor] = []
    if age is not None and age * 3600 > DEFAULT_FEATURE_POLICY.max_snapshot_age_seconds:
        status, inventory_freshness = "stale_input", "stale"
        reason = "stale_inventory_snapshot"
        facts.append(FactualFactor(code="stale_inventory", field="snapshot_age_hours", value=age))
    elif values.available_qty is None or age is None:
        status, inventory_freshness = "insufficient_data", "unknown"
        reason = "inventory_unknown"
        facts.append(FactualFactor(code="input_not_evaluable", field="available_qty", value=None))
    elif feature.status == "already_stockout":
        status = "already_stockout"
        reason = "already_stockout"
        facts.append(FactualFactor(code="current_stockout", field="available_qty", value=0))
    elif feature.status != "eligible":
        status = "insufficient_data"
        reason = feature.reason
        facts.append(
            FactualFactor(
                code="input_not_evaluable",
                field="history_known_days",
                value=values.history_known_days,
            )
        )
    else:
        if (
            values.available_qty <= 0
            or values.history_known_days < DEFAULT_FEATURE_POLICY.minimum_known_days
            or feature.feature_available_at is None
            or category_id is None
            or category_available_at is None
            or (base.variant == "with_upstream" and upstream is None)
        ):
            raise ValueError("stockout_runtime_eligible_feature_or_upstream_required")
        forecast = upstream.forecast_units_7d if upstream is not None else None
        numeric: dict[str, Any] = {
            **values.model_dump(mode="json"),
            "forecast_units_7d": forecast,
            "forecast_days_of_supply": 7 * values.available_qty / forecast if forecast else None,
            "forecast_unavailable": int(upstream is None or upstream.status != "available"),
        }
        row = dict(
            product_id=feature.product_id,
            stock_location_id=feature.stock_location_id,
            as_of=feature.model_dump(mode="json")["as_of"],
            category_id=category_id,
            values=numeric,
        )
        probability = (
            float(predict_conditional(recipe.pipeline, [row])[0])
            if isinstance(recipe.pipeline, ConditionalRiskPipeline)
            else predict(recipe.pipeline, [row])[0]
        )
        band = cast(RiskBand, risk_band(probability, policy.spec.thresholds))
        status = "scored"
        reason = None
        for field, code in (
            ("available_qty", "known_available_stock"),
            ("snapshot_age_hours", "inventory_snapshot_age"),
            ("days_of_supply_observed", "supply_from_observed_sales"),
            ("days_of_supply_in_stock", "supply_from_verified_in_stock_sales"),
            ("due_within_7d_quantity", "known_delivery_plan_within_horizon"),
            ("overdue_order_quantity", "known_overdue_order_quantity"),
            ("history_constrained_days", "history_had_inventory_constraints"),
            ("history_inventory_unknown_days", "history_inventory_coverage_missing"),
        ):
            value = getattr(values, field)
            if value is not None:
                facts.append(
                    FactualFactor.model_validate(dict(code=code, field=field, value=value))
                )
    logical_key = dict(
        product_id=feature.product_id,
        stock_location_id=feature.stock_location_id,
        as_of=feature.model_dump(mode="json")["as_of"],
        run_id=run_id,
        release_id=release.release_id,
        source=lineage.model_dump(mode="json"),
    )
    return RiskItem.model_validate_json(
        canonical_json(
            dict(
                risk_id="risk-sha256-" + digest(logical_key),
                product_id=feature.product_id,
                stock_location_id=feature.stock_location_id,
                as_of=logical_key["as_of"],
                status=status,
                status_reason=reason,
                probability=probability,
                risk_band=band,
                threshold_version=policy.policy_id,
                calibrator_version="stockout-calibrator-sha256-" + recipe.pin.calibrator_sha256,
                model_name=release.model_name,
                model_version=release.model_version,
                release_id=release.release_id,
                top_factors=[f.model_dump(mode="json") for f in facts],
                inventory_freshness_status=inventory_freshness,
                freshness_status="unknown"
                if lineage.source_watermark is None
                or lineage.source_completeness_status != "complete"
                else "current"
                if (generated_at - feature.as_of).total_seconds() <= 86400
                and lineage.source_watermark >= feature.as_of
                else "stale",
                lineage=lineage.model_dump(mode="json"),
                upstream_lineage_sha256=digest(upstream.model_dump(mode="json"))
                if upstream
                else None,
                feature_lineage_sha256=digest([r.model_dump(mode="json") for r in feature.lineage]),
                inference_run_id=run_id,
                generated_at=generated_at.isoformat(),
                quality_status="mechanics_only"
                if release.model_name.endswith("test-mechanics")
                else "passed_at_publication",
            )
        )
    )
