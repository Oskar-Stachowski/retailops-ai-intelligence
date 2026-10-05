"""Portable scoring mechanics only: no quality acceptance or operational approval."""

from datetime import timedelta

import pytest
from test_stockout_features import ORIGIN, point
from test_stockout_features import records as records

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_contract import FeatureValues
from retailops_ai.stockout.upstream_contract import SeriesForecast, UpstreamPoint
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    ModelPolicyPin,
    OperatorCapacity,
    PolicySpec,
    RiskThresholds,
)
from retailops_ai.stockout_runtime.contracts import (
    RiskItem,
    RuntimeLineage,
    RuntimeReleasePin,
    ScoringPolicy,
    ScoringRecipe,
)
from retailops_ai.stockout_runtime.scoring import score_point
from retailops_ai.stockout_training.contract import LinearEstimator, RiskPipeline, Sigmoid
from retailops_ai.stockout_training.pipeline import fit_preprocessing, predict


@pytest.fixture
def context(records):
    feature = point(records)
    rows = [
        dict(
            product_id="product",
            stock_location_id="stock",
            category_id="category",
            as_of=(ORIGIN - timedelta(days=5)).isoformat(),
            values={**feature.values.model_dump(), "available_qty": v},
        )
        for v in (10, 100)
    ]
    prep = fit_preprocessing(rows, "without_upstream", scaled=True)
    pipeline = RiskPipeline(
        family="logistic_regression",
        variant="without_upstream",
        fit_known_at=ORIGIN - timedelta(days=4),
        preprocessing=prep,
        estimator=LinearEstimator(
            weights=(-1.0, *([0.0] * (len(prep.output_columns) - 1))), intercept=0.0
        ),
        train_labels_sha256="0" * 64,
        sigmoid=Sigmoid(
            slope=0.9,
            intercept=0.1,
            fit_known_at=ORIGIN - timedelta(days=3),
            calibration_rows=20,
            calibration_keys_sha256="0" * 64,
            calibration_labels_sha256="0" * 64,
        ),
    )
    pin = ModelPolicyPin(
        qualification_id="stockout-qualification-sha256-" + "0" * 64,
        model_id="risk-model-sha256-" + digest(pipeline.model_dump(mode="json")),
        calibrator_sha256=digest(pipeline.sigmoid.model_dump(mode="json")),
        selection_known_at=ORIGIN - timedelta(days=2),
    )
    recipe = ScoringRecipe(pin=pin, pipeline=pipeline)
    spec = PolicySpec(
        thresholds=RiskThresholds(medium_from=0.25, high_from=0.5, critical_from=0.9),
        capacity=OperatorCapacity(mode="top_n", top_n=1),
        costs=AttentionCosts(false_attention=1.0, missed_incident=5.0),
    )
    body = dict(
        version="stockout-scoring-policy-1.0.0",
        pin=pin.model_dump(mode="json"),
        proposal_id="stockout-policy-proposal-sha256-" + "0" * 64,
        spec=spec.model_dump(mode="json"),
        operational_approval="requires_separate_lifecycle_approval",
    )
    policy = ScoringPolicy.model_validate_json(
        canonical_json(dict(policy_id="stockout-scoring-policy-sha256-" + digest(body), **body))
    )
    release = RuntimeReleasePin(
        model_name="retailops-stockout-risk-test-mechanics",
        model_version="1",
        release_id="stockout-release-sha256-" + "0" * 64,
        image_digest="sha256:" + "0" * 64,
        recipe_content_sha256=digest(recipe.model_dump(mode="json")),
        policy_content_sha256=digest(policy.model_dump(mode="json")),
    )
    lineage = RuntimeLineage(
        source_dataset_id="source-sha256-" + "0" * 64,
        curated_dataset_id="curated-sha256-" + "0" * 64,
        feature_set_id="feature-partitions-sha256-" + "0" * 64,
        upstream_bundle_id="upstream-partitions-sha256-" + "0" * 64,
        source_watermark=ORIGIN,
        source_completeness_status="complete",
    )
    return feature, dict(
        category_id="category",
        category_available_at=ORIGIN - timedelta(days=28),
        upstream=None,
        recipe=recipe,
        policy=policy,
        release=release,
        lineage=lineage,
        run_id="run-" + "0" * 32,
        generated_at=ORIGIN + timedelta(seconds=1),
    )


def altered(feature, **changes):
    values = FeatureValues.model_validate({**feature.values.model_dump(), **changes})
    return feature.model_copy(update={"values": values})


def test_scoring_matches_the_pinned_portable_pipeline(context):
    f, kw = context
    r = score_point(f, **kw)
    row = dict(
        product_id=f.product_id,
        stock_location_id=f.stock_location_id,
        as_of=f.as_of.isoformat(),
        category_id=kw["category_id"],
        values=f.values.model_dump(),
    )
    assert r.probability == predict(kw["recipe"].pipeline, [row])[0]
    assert r.status == "scored" and r.inventory_freshness_status == "current"
    assert r.quality_status == "mechanics_only"
    assert all(
        v.interpretation == "verified_PIT_fact_not_causal_attribution" for v in r.top_factors
    )
    assert r == score_point(f, **kw)


def test_current_zero_is_never_a_probability_of_one(context):
    f, kw = context
    f = altered(f, available_qty=0).model_copy(update={"status": "already_stockout"})
    r = score_point(f, **kw)
    assert (r.status, r.probability, r.risk_band) == ("already_stockout", None, None)


def test_unknown_inventory_is_never_a_low_probability(context):
    f, kw = context
    f = altered(f, available_qty=None).model_copy(
        update={"status": "insufficient_data", "reason": "inventory_unknown"}
    )
    r = score_point(f, **kw)
    assert r.status == "insufficient_data" and r.probability is None and r.risk_band is None
    assert r.inventory_freshness_status == "unknown"


def test_old_zero_snapshot_is_stale_not_current_stockout(context):
    f, kw = context
    f = altered(f, available_qty=0, snapshot_age_hours=25.0).model_copy(
        update={"status": "already_stockout"}
    )
    r = score_point(f, **kw)
    assert (r.status, r.probability, r.risk_band) == ("stale_input", None, None)


def test_snapshot_at_exact_freshness_boundary_is_still_scored(context):
    f, kw = context
    assert score_point(altered(f, snapshot_age_hours=24.0), **kw).status == "scored"
    assert score_point(altered(f, snapshot_age_hours=24.00001), **kw).status == "stale_input"


def test_insufficient_data_keeps_the_specific_causal_reason(context):
    f, kw = context
    f = f.model_copy(
        update={"status": "insufficient_data", "reason": "inactive_or_unknown_assortment_routing"}
    )
    r = score_point(f, **kw)
    assert r.status_reason == "inactive_or_unknown_assortment_routing" and r.probability is None


@pytest.mark.parametrize("field", ["recipe_content_sha256", "policy_content_sha256"])
def test_release_digest_mismatch_refuses_to_score_even_a_zero(context, field):
    f, kw = context
    kw["release"] = kw["release"].model_copy(update={field: "1" * 64})
    with pytest.raises(ValueError, match="release_policy"):
        score_point(f, **kw)


def test_caller_resealed_policy_does_not_change_its_model_binding(context):
    f, kw = context
    p = kw["policy"].model_dump(mode="json")
    p["pin"]["qualification_id"] = "stockout-qualification-sha256-" + "1" * 64
    p["policy_id"] = "stockout-scoring-policy-sha256-" + digest(
        {k: v for k, v in p.items() if k != "policy_id"}
    )
    kw["policy"] = ScoringPolicy.model_validate_json(canonical_json(p))
    kw["release"] = kw["release"].model_copy(update={"policy_content_sha256": digest(p)})
    with pytest.raises(ValueError, match="release_policy"):
        score_point(f, **kw)


def test_category_known_after_origin_cannot_be_used(context):
    f, kw = context
    kw["category_available_at"] = f.as_of + timedelta(microseconds=1)
    with pytest.raises(ValueError, match="origin_mismatch"):
        score_point(f, **kw)


def test_model_chosen_after_origin_cannot_be_used(context):
    f, kw = context
    recipe = kw["recipe"].model_copy(
        update={
            "pin": kw["recipe"].pin.model_copy(
                update={"selection_known_at": f.as_of + timedelta(days=1)}
            )
        }
    )
    kw["recipe"] = recipe
    with pytest.raises(ValueError):
        score_point(f, **kw)


def test_missing_catalog_is_allowed_only_for_unscorable_points(context):
    f, kw = context
    kw.update(category_id=None, category_available_at=None)
    with pytest.raises(ValueError, match="eligible_feature"):
        score_point(f, **kw)
    f = f.model_copy(
        update={"status": "insufficient_data", "reason": "inactive_or_unknown_product"}
    )
    assert score_point(f, **kw).status == "insufficient_data"


def test_fresh_generation_does_not_hide_stale_or_unknown_source(context):
    f, kw = context
    kw["lineage"] = kw["lineage"].model_copy(
        update={"source_watermark": ORIGIN - timedelta(seconds=1)}
    )
    assert score_point(f, **kw).freshness_status == "stale"
    kw["lineage"] = kw["lineage"].model_copy(update={"source_watermark": None})
    assert score_point(f, **kw).freshness_status == "unknown"
    kw["lineage"] = kw["lineage"].model_copy(
        update={"source_watermark": ORIGIN, "source_completeness_status": "not_ready"}
    )
    assert score_point(f, **kw).freshness_status == "unknown"


def test_old_origin_is_stale_even_when_output_is_created_now(context):
    f, kw = context
    kw["generated_at"] = ORIGIN + timedelta(days=2)
    assert score_point(f, **kw).freshness_status == "stale"


@pytest.mark.parametrize("field,value", [("probability", 0.0), ("risk_band", "low")])
def test_unscorable_wire_output_refuses_fake_probability_or_band(context, field, value):
    f, kw = context
    f = altered(f, available_qty=0).model_copy(update={"status": "already_stockout"})
    r = score_point(f, **kw)
    with pytest.raises(ValueError, match="output_status"):
        RiskItem.model_validate_json(canonical_json({**r.model_dump(mode="json"), field: value}))


def with_upstream(context, *, available=True):
    feature, kw = context
    series = SeriesForecast(
        selling_location_id="store",
        channel="store",
        route_record_sha256="0" * 64,
        history_context_sha256="0" * 64,
        input_rows_sha256="0" * 64,
        source_available_at=ORIGIN - timedelta(seconds=1),
        daily_units=(2.0,) * 7 if available else (None,) * 7,
        reason=None if available else "target_calendar_unknown",
    )
    cutoff = ORIGIN.replace(microsecond=0)
    upstream = UpstreamPoint(
        product_id=feature.product_id,
        stock_location_id=feature.stock_location_id,
        as_of=ORIGIN,
        forecast_origin=cutoff,
        training_cutoff=cutoff,
        selection_cutoff=cutoff,
        source_available_at=cutoff,
        upstream_model_version="baseline-sha256-" + "0" * 64,
        status="available" if available else "insufficient_data",
        reason=None if available else "target_calendar_unknown",
        forecast_units_7d=14.0 if available else None,
        series=(series,),
    )
    numeric = {
        **feature.values.model_dump(),
        "forecast_units_7d": upstream.forecast_units_7d,
        "forecast_days_of_supply": 7 * feature.values.available_qty / 14 if available else None,
        "forecast_unavailable": int(not available),
    }
    row = dict(
        product_id=feature.product_id,
        stock_location_id=feature.stock_location_id,
        category_id=kw["category_id"],
        as_of=ORIGIN.isoformat(),
        values=numeric,
    )
    reference = {
        **row,
        "as_of": (ORIGIN - timedelta(days=1)).isoformat(),
        "values": {**numeric, "forecast_units_7d": 7.0, "forecast_days_of_supply": 3.0},
    }
    prep = fit_preprocessing([row, reference], "with_upstream", scaled=True)
    weights = [0.0] * len(prep.output_columns)
    weights[prep.output_columns.index("forecast_days_of_supply")] = 0.7
    weights[prep.output_columns.index("missing:forecast_units_7d")] = 0.3
    old = kw["recipe"].pipeline
    pipeline = old.model_copy(
        update={
            "variant": "with_upstream",
            "preprocessing": prep,
            "estimator": LinearEstimator(weights=tuple(weights), intercept=0.5),
        }
    )
    pin = kw["recipe"].pin.model_copy(
        update={"model_id": "risk-model-sha256-" + digest(pipeline.model_dump(mode="json"))}
    )
    recipe = ScoringRecipe(pin=pin, pipeline=pipeline)
    policy_body = {
        **kw["policy"].model_dump(mode="json", exclude={"policy_id"}),
        "pin": pin.model_dump(mode="json"),
    }
    policy = ScoringPolicy.model_validate_json(
        canonical_json(
            {**policy_body, "policy_id": "stockout-scoring-policy-sha256-" + digest(policy_body)}
        )
    )
    release = kw["release"].model_copy(
        update={
            "recipe_content_sha256": digest(recipe.model_dump(mode="json")),
            "policy_content_sha256": digest(policy.model_dump(mode="json")),
        }
    )
    return (
        feature,
        {**kw, "recipe": recipe, "policy": policy, "release": release, "upstream": upstream},
        row,
    )


@pytest.mark.parametrize("available", [True, False])
def test_verified_upstream_and_missing_indicator_match_portable_model(context, available):
    f, kw, row = with_upstream(context, available=available)
    result = score_point(f, **kw)
    assert result.probability == predict(kw["recipe"].pipeline, [row])[0]
    assert result.upstream_lineage_sha256 == digest(kw["upstream"].model_dump(mode="json"))


def test_selected_upstream_variant_refuses_absent_feature(context):
    f, kw, _ = with_upstream(context)
    kw["upstream"] = None
    with pytest.raises(ValueError, match="upstream_required"):
        score_point(f, **kw)


def test_forecast_for_other_physical_stock_cannot_be_used(context):
    f, kw, _ = with_upstream(context)
    kw["upstream"] = kw["upstream"].model_copy(update={"stock_location_id": "other"})
    with pytest.raises(ValueError, match="physical_key_mismatch"):
        score_point(f, **kw)


def test_future_upstream_cutoff_is_revalidated_by_worker(context):
    f, kw, _ = with_upstream(context)
    kw["upstream"] = kw["upstream"].model_copy(
        update={"training_cutoff": ORIGIN + timedelta(seconds=1)}
    )
    with pytest.raises(ValueError, match="cutoff_mismatch"):
        score_point(f, **kw)
