"""Invented mechanics forecast; contract/transport evidence, never ML quality evidence."""

from datetime import UTC, datetime, timedelta

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem
from retailops_ai.intelligence_events.contracts import (
    ForecastGenerated,
    forecast_event_id,
    prediction_identity,
)


def fixture_event(*, origin=None, suffix="a", product_id="fixture-product"):
    origin = origin or datetime(2026, 10, 1, 23, 59, 59, tzinfo=UTC)
    generated = origin + timedelta(seconds=1)
    functional = {"median": 12.0, "mean": 13.0, "interval": {"lower": 10.0, "upper": 14.0}}
    raw = dict(
        product_id=product_id,
        selling_location_id="fixture-store",
        channel="store",
        forecast_origin=origin.isoformat(),
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=(origin.date() + timedelta(days=1)).isoformat(),
        horizon_days=1,
        prediction={
            "key": "explicit-ai10-contract-fixture",
            "candidate": functional,
            "baseline": {**functional, "mean": 12.5},
            "metadata": {
                "selected": "seasonal_naive",
                "baseline": "seasonal_naive",
                "mean_source": "fixture",
                "exact_reference_median": True,
                "exact_reference_interval": True,
                "recipe_id": "functional-v12-recipe-sha256-" + "a" * 64,
            },
        },
        execution_profile_id="batch-profile-sha256-" + "a" * 64,
        prediction_id="prediction-sha256-" + "0" * 64,
        prediction_dataset_id="v12-forecasts-sha256-" + suffix * 64,
        model_name="retailops-demand-forecast-v12-mechanics",
        model_version="1",
        approval_sha256="a" * 64,
        runtime_pin_sha256="b" * 64,
        image_digest="sha256:" + "c" * 64,
        release_id="v12-model-release-sha256-" + "a" * 64,
        receipt_id="v12-computation-sha256-" + suffix * 64,
        source_dataset_id="source-sha256-" + "d" * 64,
        curated_dataset_id="curated-sha256-" + "e" * 64,
        feature_set_id="features-sha256-" + "f" * 64,
        inference_run_id="run-" + suffix * 32,
        profile_id="batch-profile-sha256-" + "a" * 64,
        generated_at=generated.isoformat(),
        approval_valid_until="2029-01-01T00:00:00Z",
        freshness={
            "status": "unknown",
            "reason": "source_watermark_unavailable",
            "source_watermark": None,
            "source_watermark_as_of": None,
            "source_watermark_policy_version": None,
            "source_completeness_status": "unavailable",
            "source_watermark_age_seconds": None,
            "source_watermark_origin_lag_seconds": None,
            "latest_complete_observation_date": None,
            "observation_lag_days": None,
            "evaluated_at": generated.isoformat(),
            "origin_age_seconds": 1.0,
        },
    )
    item = V12ForecastItem.model_validate_json(canonical_bytes(raw))
    raw = item.model_dump(mode="json")
    raw["prediction_id"] = prediction_identity(item)
    item = V12ForecastItem.model_validate_json(canonical_bytes(raw))
    return ForecastGenerated(
        event_id=forecast_event_id(item.prediction_id),
        event_type="forecast_generated",
        schema_version="2.0",
        topic="retailops.intelligence.v2",
        source="retailops-ai",
        correlation_id=item.inference_run_id,
        occurred_at=item.generated_at,
        ingested_at=item.generated_at,
        payload=item,
    )
