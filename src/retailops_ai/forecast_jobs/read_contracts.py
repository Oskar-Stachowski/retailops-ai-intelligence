"""Closed read projection of published outputs; never a reconstructed ModelRecord."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Channel,
    Contract,
    CuratedID,
    FeatureID,
    PredictionDatasetID,
    PredictionID,
    RunID,
    Sha256,
    SourceID,
    Symbol,
    UtcTime,
)
from retailops_ai.forecast_jobs.contracts import MechanicsPrediction, ProfileID, ReleaseID

ReadErrorCode = Literal[
    "forecast-read-denied",
    "forecast-scope-invalid",
    "forecast-scope-limit",
    "forecast-output-not-found",
    "forecast-view-changed",
    "forecast-view-required",
    "forecast-read-budget",
    "forecast-output-invalid",
]


class ForecastQuery(Contract):
    product_id: Symbol | None = None
    selling_location_id: Symbol | None = None
    channel: Channel | None = None
    target_from: date | None = None
    target_to: date | None = None
    as_of: UtcTime | None = None
    inference_run_id: RunID | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0, le=2800)] = 0
    view_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if (self.target_from is None) != (self.target_to is None):
            raise ValueError("forecast_date_window_requires_both_bounds")
        if (
            self.target_from is not None
            and self.target_to is not None
            and (self.target_to < self.target_from or (self.target_to - self.target_from).days > 31)
        ):
            raise ValueError("forecast_date_window_limit")
        if self.as_of is not None and self.as_of.time().isoformat() != "23:59:59":
            raise ValueError("forecast_origin_must_close_utc_day")
        return self


class ReadPolicy(Contract):
    schema_version: Literal["1.1"] = "1.1"
    policy_id: Literal["forecast-read-v2"] = "forecast-read-v2"
    max_origin_age_seconds: Literal[86400] = 86400
    max_source_watermark_origin_lag_seconds: Literal[0] = 0
    max_observation_lag_days: Literal[1] = 1
    max_candidate_outputs: Literal[32] = 32


FreshnessReason = Literal[
    "source_watermark_unavailable",
    "source_watermark_policy_unsupported",
    "source_watermark_not_ready",
    "source_watermark_lag_exceeded",
    "source_observation_unavailable",
    "source_observation_lag_exceeded",
    "origin_age_exceeded",
    "newer_run_unpublished",
    "within_policy",
]


class ForecastFreshness(Contract):
    status: Literal["current", "stale", "unknown"]
    reason: FreshnessReason
    source_watermark: UtcTime | None
    source_watermark_as_of: UtcTime | None
    source_watermark_policy_version: Symbol | None
    source_completeness_status: Literal["complete", "not_ready", "unavailable"]
    source_watermark_age_seconds: Annotated[float, Field(ge=0)] | None
    source_watermark_origin_lag_seconds: Annotated[float, Field(ge=0)] | None
    latest_complete_observation_date: date | None
    observation_lag_days: Annotated[int, Field(ge=0)] | None
    evaluated_at: UtcTime
    origin_age_seconds: Annotated[float, Field(ge=0)]
    policy_id: Literal["forecast-read-v2"] = "forecast-read-v2"
    max_origin_age_seconds: Literal[86400] = 86400
    max_source_watermark_origin_lag_seconds: Literal[0] = 0
    max_observation_lag_days: Literal[1] = 1

    @model_validator(mode="after")
    def consistency(self) -> Self:
        expected = (
            "current"
            if self.reason == "within_policy"
            else "unknown"
            if self.reason
            in {
                "source_watermark_unavailable",
                "source_watermark_policy_unsupported",
                "source_watermark_not_ready",
                "source_observation_unavailable",
            }
            else "stale"
        )
        measures = (self.source_watermark_age_seconds, self.source_watermark_origin_lag_seconds)
        if self.status != expected or (
            any(v is not None for v in measures)
            if self.source_watermark is None
            else any(v is None for v in measures)
        ):
            raise ValueError("forecast_freshness_status_or_watermark_mismatch")
        if (
            self.source_watermark_as_of is not None
            and self.source_watermark_as_of > self.evaluated_at
        ):
            raise ValueError("forecast_freshness_declaration_from_future")
        if self.status == "current" and (
            self.source_watermark is None
            or self.source_completeness_status != "complete"
            or self.source_watermark_origin_lag_seconds != 0
            or self.observation_lag_days is None
            or self.observation_lag_days > self.max_observation_lag_days
            or self.origin_age_seconds > self.max_origin_age_seconds
        ):
            raise ValueError("forecast_freshness_current_without_complete_daily_inputs")
        return self


class ForecastItem(MechanicsPrediction):
    """Public projection has its own identity and exposes no artifact URI or other scope."""

    prediction_id: PredictionID
    prediction_dataset_id: PredictionDatasetID
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    unit_of_measure: Literal["unit"] = "unit"
    prediction_interval: None = None
    interval_unavailable_reason: Literal["not_published"] = "not_published"
    model_name: Literal["retailops-demand-forecast"] = "retailops-demand-forecast"
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    qualification_sha256: Sha256
    model_artifact_sha256: Sha256
    model_config_sha256: Sha256
    evaluation_id: Symbol
    feature_schema_version: Literal["forecast-features-v1"] = "forecast-features-v1"
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    release_id: ReleaseID
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    inference_run_id: RunID
    profile_id: ProfileID
    execution_profile_id: ProfileID
    generated_at: UtcTime
    freshness: ForecastFreshness
    quality_status: Literal["passed"] = "passed"


class Pagination(Contract):
    limit: Annotated[int, Field(ge=1, le=200)]
    offset: Annotated[int, Field(ge=0, le=2800)]
    total: Annotated[int, Field(ge=0, le=2800)]
    next_offset: Annotated[int, Field(ge=0, le=2800)] | None


class ForecastPage(Contract):
    schema_version: Literal["1.1"] = "1.1"
    items: tuple[ForecastItem, ...] = Field(max_length=200)
    pagination: Pagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    selection: Literal["latest_per_series_horizon", "origin", "inference_run"]
    view_sha256: Sha256
    freshness_policy: ReadPolicy = ReadPolicy()
