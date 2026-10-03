"""Daily schedule policy; publication time cannot make old or incomplete inputs current."""

from datetime import datetime

from retailops_ai.data_contracts.common import ForecastKey, end_of_day
from retailops_ai.forecast_jobs.read_contracts import ForecastFreshness, FreshnessReason, ReadPolicy
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness, observation_key


def freshness(
    row: ForecastKey,
    evidence: SourceFreshness | None,
    *,
    now: datetime,
    newer_unpublished: bool,
) -> ForecastFreshness:
    policy = ReadPolicy()
    age = (now - row.forecast_origin).total_seconds()
    watermark = evidence.watermark if evidence else None
    effective = (
        min(end_of_day(watermark.complete_through), row.forecast_origin)
        if watermark and watermark.supported and watermark.complete_through is not None
        else None
    )
    observation = (
        next(r for r in evidence.observations if observation_key(r) == observation_key(row))
        if evidence
        else None
    )
    observed_date = observation.latest_complete_observation_date if observation else None
    lag = (row.forecast_origin - effective).total_seconds() if effective else None
    observation_lag = (row.forecast_origin.date() - observed_date).days if observed_date else None
    reason: FreshnessReason
    if newer_unpublished:
        reason = "newer_run_unpublished"
    elif age > policy.max_origin_age_seconds:
        reason = "origin_age_exceeded"
    elif watermark is None:
        reason = "source_watermark_unavailable"
    elif not watermark.supported:
        reason = "source_watermark_policy_unsupported"
    elif watermark.completeness_status != "complete":
        reason = "source_watermark_not_ready"
    elif lag is not None and lag > policy.max_source_watermark_origin_lag_seconds:
        reason = "source_watermark_lag_exceeded"
    elif observation_lag is None:
        reason = "source_observation_unavailable"
    elif observation_lag > policy.max_observation_lag_days:
        reason = "source_observation_lag_exceeded"
    else:
        reason = "within_policy"
    return ForecastFreshness(
        status=(
            "current"
            if reason == "within_policy"
            else "unknown"
            if reason
            in {
                "source_watermark_unavailable",
                "source_watermark_policy_unsupported",
                "source_watermark_not_ready",
                "source_observation_unavailable",
            }
            else "stale"
        ),
        reason=reason,
        source_watermark=effective,
        source_watermark_as_of=watermark.as_of_time if watermark else None,
        source_watermark_policy_version=watermark.policy_version if watermark else None,
        source_completeness_status=watermark.completeness_status if watermark else "unavailable",
        source_watermark_age_seconds=(now - effective).total_seconds() if effective else None,
        source_watermark_origin_lag_seconds=lag,
        latest_complete_observation_date=observed_date,
        observation_lag_days=observation_lag,
        evaluated_at=now,
        origin_age_seconds=age,
    )
