"""AI 04.1: resolved task policy and one knowledge boundary per forecast origin."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DataLineage,
    DateWindow,
    FalseFlag,
    ForecastKey,
    SellingKey,
    Sha256,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_sha256

HORIZONS = tuple(range(1, 15))
MAX_ORIGINS = 366


class TaskConfig(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    task: Literal["daily_observed_sales_forecast"] = "daily_observed_sales_forecast"
    model_name: Literal["retailops-demand-forecast"] = "retailops-demand-forecast"
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    quantity_policy: Literal["saleable_units_no_pack_or_fx_conversion"] = (
        "saleable_units_no_pack_or_fx_conversion"
    )
    observation_grain: tuple[
        Literal["business_date"],
        Literal["product_id"],
        Literal["selling_location_id"],
        Literal["channel"],
    ] = ("business_date", "product_id", "selling_location_id", "channel")
    forecast_grain: tuple[
        Literal["product_id"],
        Literal["selling_location_id"],
        Literal["channel"],
        Literal["forecast_origin"],
        Literal["target_date"],
    ] = ("product_id", "selling_location_id", "channel", "forecast_origin", "target_date")
    business_timezone: Literal["UTC"] = "UTC"
    calendar_version: Literal["forecast-utc-daily-1.0.0"] = "forecast-utc-daily-1.0.0"
    day_step: Literal["calendar_day_including_closed_days"] = "calendar_day_including_closed_days"
    cutoff_policy: Literal["end_of_day_second_v1"] = "end_of_day_second_v1"
    cutoff_time_utc: Literal["23:59:59"] = "23:59:59"
    cutoff_precision: Literal["seconds"] = "seconds"
    source_timestamp_precision: Literal["microseconds_no_rounding"] = "microseconds_no_rounding"
    availability_rule: Literal["curated_available_at_lte_forecast_origin"] = (
        "curated_available_at_lte_forecast_origin"
    )
    availability_delay_seconds: Annotated[int, Field(ge=0, le=0)] = 0
    delayed_data_policy: Literal["exclude_until_a_new_origin_never_fill_missing_with_zero"] = (
        "exclude_until_a_new_origin_never_fill_missing_with_zero"
    )
    missing_availability_policy: Literal["exclude"] = "exclude"
    observation_history: Literal["daily_demand_versions_latest_known_version"] = (
        "daily_demand_versions_latest_known_version"
    )
    evaluation_protocol: Literal["fixed_origin_multi_horizon"] = "fixed_origin_multi_horizon"
    history_policy: Literal["one_boundary_for_all_horizons_and_models"] = (
        "one_boundary_for_all_horizons_and_models"
    )
    horizon_days: tuple[Annotated[int, Field(ge=1, le=14)], ...] = HORIZONS
    reporting_windows_days: tuple[Literal[7], Literal[14]] = (7, 14)
    inventory_features_enabled: FalseFlag = False
    simulation_truth_features_enabled: FalseFlag = False
    source_operational_outputs_as_targets: FalseFlag = False

    @model_validator(mode="after")
    def complete_horizons(self) -> Self:
        if self.horizon_days != HORIZONS:
            raise ValueError("forecast_requires_ordered_daily_horizons_1_to_14")
        return self

    def task_id(self) -> str:
        return "forecast-task-sha256-" + canonical_sha256(self.model_dump(mode="json"))


class Target(Contract):
    horizon_days: Annotated[int, Field(ge=1, le=14)]
    target_date: date


class ReportingWindow(DateWindow):
    days: Literal[7, 14]


class Origin(Contract):
    origin_date: date
    forecast_origin: UtcTime
    availability_cutoff: UtcTime
    observed_through_date: date
    targets: tuple[Target, ...] = Field(min_length=14, max_length=14)
    reporting_windows: tuple[ReportingWindow, ReportingWindow]

    @model_validator(mode="after")
    def fixed_boundary(self) -> Self:
        if self.forecast_origin != end_of_day(self.origin_date):
            raise ValueError("origin_must_close_utc_business_day")
        if (
            self.availability_cutoff != self.forecast_origin
            or self.observed_through_date != self.origin_date
        ):
            raise ValueError("origin_knowledge_boundary_mismatch")
        if tuple(t.horizon_days for t in self.targets) != HORIZONS or any(
            t.target_date != self.origin_date + timedelta(days=t.horizon_days) for t in self.targets
        ):
            raise ValueError("target_date_horizon_mismatch")
        if tuple(w.days for w in self.reporting_windows) != (7, 14) or any(
            w.start != self.origin_date + timedelta(days=1)
            or w.end != self.origin_date + timedelta(days=w.days)
            for w in self.reporting_windows
        ):
            raise ValueError("reporting_window_mismatch")
        return self

    def forecast_keys(self, selling_key: SellingKey) -> tuple[ForecastKey, ...]:
        """One daily key per target; overlapping reporting windows never duplicate keys."""
        return tuple(
            ForecastKey(
                **selling_key.model_dump(),
                forecast_origin=self.forecast_origin,
                business_timezone="UTC",
                cutoff_policy="end_of_day_second_v1",
                target_date=target.target_date,
                horizon_days=target.horizon_days,
            )
            for target in self.targets
        )


def make_origin(day: date) -> Origin:
    return Origin(
        origin_date=day,
        forecast_origin=end_of_day(day),
        availability_cutoff=end_of_day(day),
        observed_through_date=day,
        targets=tuple(
            Target(horizon_days=h, target_date=day + timedelta(days=h)) for h in HORIZONS
        ),
        reporting_windows=(
            ReportingWindow(days=7, start=day + timedelta(days=1), end=day + timedelta(days=7)),
            ReportingWindow(days=14, start=day + timedelta(days=1), end=day + timedelta(days=14)),
        ),
    )


class OriginWindow(DateWindow):
    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (self.end - self.start).days >= MAX_ORIGINS or self.end > date.max - timedelta(days=14):
            raise ValueError("forecast_calendar_origin_limit")
        return self


class Parent(DataLineage):
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    curated_descriptor_sha256: Sha256
    business_timezone: Literal["UTC"]
    forecast_source_status: Literal["passed"]


class Implementation(Contract):
    version: Literal["forecast-calendar-1.0.0"]
    code_files: dict[str, Sha256] = Field(min_length=1)
    code_sha256: Sha256

    @model_validator(mode="after")
    def code_hash(self) -> Self:
        if self.code_sha256 != canonical_sha256(self.code_files):
            raise ValueError("forecast_implementation_hash_mismatch")
        return self


class CalendarDescriptor(Contract):
    schema_version: Literal["1.0.0"]
    task: TaskConfig
    task_id: Annotated[str, Field(pattern=r"^forecast-task-sha256-[0-9a-f]{64}$")]
    parent: Parent
    origin_window: OriginWindow
    implementation: Implementation
    calendar_content_sha256: Sha256

    @model_validator(mode="after")
    def task_identity(self) -> Self:
        if self.task_id != self.task.task_id():
            raise ValueError("forecast_task_identity_mismatch")
        return self


class CalendarManifest(Contract):
    schema_version: Literal["1.0.0"]
    calendar_id: Annotated[str, Field(pattern=r"^forecast-calendar-sha256-[0-9a-f]{64}$")]
    descriptor: CalendarDescriptor
    origins: tuple[Origin, ...] = Field(min_length=1, max_length=MAX_ORIGINS)
    forecast_model_status: Literal["not_ready"]
    generated_at: UtcTime

    @model_validator(mode="after")
    def calendar_identity(self) -> Self:
        window = self.descriptor.origin_window
        expected = tuple(
            window.start + timedelta(days=i) for i in range((window.end - window.start).days + 1)
        )
        if tuple(o.origin_date for o in self.origins) != expected:
            raise ValueError("forecast_calendar_origin_coverage_mismatch")
        if self.descriptor.calendar_content_sha256 != canonical_sha256(
            [o.model_dump(mode="json") for o in self.origins]
        ):
            raise ValueError("forecast_calendar_content_mismatch")
        if self.calendar_id != "forecast-calendar-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("forecast_calendar_identity_mismatch")
        return self
