"""Retrospective rolling-origin baseline, never a future-fitted predictor."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictFloat, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, UtcTime, end_of_day
from retailops_ai.stockout.contract import PhysicalKey


class UpstreamPolicy(Contract):
    version: Literal["stockout-upstream-ma28-1.0.0"] = "stockout-upstream-ma28-1.0.0"
    model: Literal["moving_average"] = "moving_average"
    history_calendar_days: Literal[28] = 28
    minimum_known_days: Literal[7] = 7
    horizon_days: Literal[7] = 7
    origin_adapter: Literal["same_date_end_of_day_second_never_after_inventory_origin"] = (
        "same_date_end_of_day_second_never_after_inventory_origin"
    )
    parameters: Literal["fixed_algorithm_no_fitted_parameters"] = (
        "fixed_algorithm_no_fitted_parameters"
    )
    selection: Literal["fixed_recipe_no_outcome_selection"] = "fixed_recipe_no_outcome_selection"
    reconstruction: Literal["retrospective_rolling_origin"] = "retrospective_rolling_origin"
    mapping: Literal["sum_selling_series_once_to_physical_route_known_at_origin"] = (
        "sum_selling_series_once_to_physical_route_known_at_origin"
    )
    closed_target: Literal["known_closed_zero_unknown_calendar_not_zero"] = (
        "known_closed_zero_unknown_calendar_not_zero"
    )
    history_eligibility: Literal["seven_known_days_short_active_history_allowed"] = (
        "seven_known_days_short_active_history_allowed"
    )
    sales_measure: Literal["observed_sales_units_not_latent_demand"] = (
        "observed_sales_units_not_latent_demand"
    )


DEFAULT_UPSTREAM_POLICY = UpstreamPolicy()


class SeriesForecast(Contract):
    selling_location_id: Symbol
    channel: Literal["store", "online"]
    route_record_sha256: Sha256
    history_context_sha256: Sha256
    input_rows_sha256: Sha256
    source_available_at: UtcTime | None
    daily_units: tuple[Annotated[StrictFloat, Field(ge=0)] | None, ...] = Field(
        min_length=7, max_length=7
    )
    reason: str | None

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (self.reason is None) != all(v is not None for v in self.daily_units):
            raise ValueError("stockout_upstream_series_coverage_mismatch")
        return self


class UpstreamPoint(PhysicalKey):
    as_of: UtcTime
    forecast_origin: UtcTime
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    source_available_at: UtcTime | None
    upstream_model_version: Annotated[str, Field(pattern=r"^baseline-sha256-[0-9a-f]{64}$")]
    status: Literal["available", "insufficient_data"]
    reason: str | None
    forecast_units_7d: Annotated[StrictFloat, Field(ge=0)] | None
    series: tuple[SeriesForecast, ...]

    @model_validator(mode="after")
    def historical(self) -> Self:
        if (
            self.forecast_origin != end_of_day(self.as_of.date())
            or self.forecast_origin > self.as_of
        ):
            raise ValueError("stockout_upstream_origin_after_inventory_or_wrong_date")
        if (
            self.training_cutoff != self.forecast_origin
            or self.selection_cutoff != self.forecast_origin
        ):
            raise ValueError("stockout_upstream_training_or_selection_cutoff_mismatch")
        if (
            self.source_available_at is not None and self.source_available_at > self.forecast_origin
        ) or any(
            s.source_available_at is not None and s.source_available_at > self.forecast_origin
            for s in self.series
        ):
            raise ValueError("stockout_upstream_future_information")
        if len({(s.selling_location_id, s.channel) for s in self.series}) != len(self.series):
            raise ValueError("stockout_upstream_duplicate_selling_series")
        if (self.status == "available") != (
            self.reason is None and self.forecast_units_7d is not None
        ):
            raise ValueError("stockout_upstream_status_mismatch")
        if self.status == "available":
            from math import fsum

            if (
                not self.series
                or any(s.reason is not None for s in self.series)
                or self.forecast_units_7d
                != fsum(v for s in self.series for v in s.daily_units if v is not None)
            ):
                raise ValueError("stockout_upstream_physical_sum_mismatch")
        elif self.reason is None or self.forecast_units_7d is not None:
            raise ValueError("stockout_upstream_missing_is_not_zero")
        return self
