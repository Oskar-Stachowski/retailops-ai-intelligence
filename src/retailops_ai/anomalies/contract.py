"""Versioned, bounded daily demand inputs with separate fit and scoring cutoffs."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, SellingKey, Sha256, UtcTime

TABLES = (
    "daily_demand_versions",
    "price_plans",
    "promotion_plans",
    "inventory_daily_snapshots",
)
MODEL_FEATURES = (
    "observed_units",
    "expected_units",
    "residual_units",
    "robust_scale_units",
    "standardized_residual",
    "planned_price",
    "promotion_offered",
    "on_hand",
)
MAX_INPUT_ROWS = 100_000
MAX_INPUT_BYTES = 128 * 1024**2
MAX_OUTPUT_ROWS = 100_000
MAX_OUTPUT_BYTES = 256 * 1024**2
Units = Annotated[int, Field(ge=0)]


class Policy(Contract):
    version: Literal["anomaly-demand-inputs-1.0.0"] = "anomaly-demand-inputs-1.0.0"
    metric: Literal["observed_sales_units"] = "observed_sales_units"
    history_days: Literal[28] = 28
    seasonal_lag_days: Literal[7] = 7
    minimum_history_days: Literal[14] = 14
    minimum_residuals: Literal[7] = 7
    scoring_delay_hours: Annotated[int, Field(ge=0, le=168)] = 24
    expected_policy: Literal["same_series_day_minus_7_known_before_window"] = (
        "same_series_day_minus_7_known_before_window"
    )
    scale_policy: Literal["max_1_pcs_1.4826_mad_prior_seasonal_residuals"] = (
        "max_1_pcs_1.4826_mad_prior_seasonal_residuals"
    )
    closed_policy: Literal["retain_without_residual_exclude_from_fit"] = (
        "retain_without_residual_exclude_from_fit"
    )
    promotion_policy: Literal["known_context_no_automatic_alert"] = (
        "known_context_no_automatic_alert"
    )
    dq_policy: Literal["canonical_source_quality_only_raw_completeness_not_qualified"] = (
        "canonical_source_quality_only_raw_completeness_not_qualified"
    )


DEFAULT_POLICY = Policy()


class Reference(Contract):
    table: Literal[
        "daily_demand_versions", "price_plans", "promotion_plans", "inventory_daily_snapshots"
    ]
    record_sha256: Sha256
    available_at: UtcTime
    business_date: date | None
    role: Literal["fit", "observed", "known_plan", "inventory_context"]


class Point(SellingKey):
    schema_version: Literal["1.0.0"] = "1.0.0"
    business_date: date
    fit_cutoff: UtcTime
    scoring_origin: UtcTime
    observation_status: Literal[
        "observed_positive", "observed_zero", "closed", "unavailable", "invalid"
    ]
    observed_units: Units | None
    expected_units: Units | None
    residual_units: int | None
    robust_scale_units: Annotated[float, Field(ge=1)] | None
    standardized_residual: float | None
    history_days: Annotated[int, Field(ge=0, le=28)]
    residual_count: Annotated[int, Field(ge=0, le=21)]
    insufficient_history: bool
    scale_floor_applied: bool
    status: Literal["ready_input", "insufficient_data", "closed", "invalid_input"]
    reason_codes: tuple[str, ...]
    planned_price: str | None
    currency: str | None
    promotion_offered: bool
    stock_location_id: str | None
    on_hand: Units | None
    inventory_status: Literal["available", "unavailable"]
    dq_status: Literal["canonical_source_valid", "invalid_input", "unavailable"]
    raw_dq_completeness: Literal["not_qualified"] = "not_qualified"
    references: tuple[Reference, ...] = Field(max_length=32)

    @model_validator(mode="after")
    def temporal_contract(self) -> Self:
        start = datetime.combine(self.business_date, datetime.min.time(), UTC)
        if self.fit_cutoff != start - timedelta(microseconds=1) or self.scoring_origin < start:
            raise ValueError("invalid_anomaly_cutoffs")
        for ref in self.references:
            if ref.available_at > self.scoring_origin:
                raise ValueError("anomaly_reference_after_scoring_origin")
            if ref.role in {"fit", "known_plan"} and ref.available_at > self.fit_cutoff:
                raise ValueError("anomaly_fit_reference_after_window_start")
            if ref.role == "fit" and (
                ref.business_date is None or ref.business_date >= self.business_date
            ):
                raise ValueError("anomaly_fit_contains_outcome")
        if self.insufficient_history != (
            self.history_days < 14 or self.residual_count < 7 or self.expected_units is None
        ):
            raise ValueError("anomaly_history_flag_mismatch")
        if self.status == "ready_input":
            if (
                self.insufficient_history
                or self.observation_status not in {"observed_zero", "observed_positive"}
                or self.observed_units is None
                or self.expected_units is None
                or self.robust_scale_units is None
                or self.residual_units != self.observed_units - self.expected_units
                or self.standardized_residual != self.residual_units / self.robust_scale_units
            ):
                raise ValueError("invalid_anomaly_ready_input")
        elif self.residual_units is not None or self.standardized_residual is not None:
            raise ValueError("unscoreable_anomaly_input_has_residual")
        if (self.on_hand is None) != (self.inventory_status == "unavailable"):
            raise ValueError("anomaly_inventory_status_mismatch")
        return self
