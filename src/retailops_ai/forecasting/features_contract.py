"""Calendar panel and observed-sales input schema; formal feature/split manifests follow in 04.3."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    ForecastKey,
    SellingKey,
    Sha256,
    TrueFlag,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_sha256

TABLES = (
    "product_catalog",
    "channel_assignments",
    "assortment",
    "business_calendar",
    "category_calendar",
    "daily_demand_versions",
    "price_plans",
    "promotion_plans",
)
Kind = Literal["observed", "calendar", "categorical", "known_plan"]
ObservationStatus = Literal["observed_positive", "observed_zero", "closed", "missing"]
# Order and types are shared by validation and the typed Parquet columns.
FEATURE_TYPES = {
    **{f"origin_lag_{n}_units": "int" for n in (1, 7, 14, 28)},
    **{
        f"rolling_{stat}_{n}": "int" if stat == "count" else "float"
        for n in (7, 14, 28)
        for stat in ("mean", "std", "count")
    },
    "target_weekday": "int",
    "target_week_of_year": "int",
    "target_month": "int",
    "target_quarter": "int",
    "target_is_weekend": "bool",
    "target_is_public_holiday": "bool",
    "target_is_easter": "bool",
    "target_is_christmas": "bool",
    "target_is_black_friday": "bool",
    "target_is_cyber_monday": "bool",
    "target_location_open": "bool",
    "target_is_category_season": "bool",
    "category_id": "str",
    "brand": "str",
    "country_code": "str",
    "calendar_jurisdiction": "str",
    "channel": "str",
    "planned_regular_price_minor_units": "int",
    "currency": "str",
    "planned_promotion_offered": "bool",
    "planned_promotion_type": "str",
    "planned_promotion_discount_basis_points": "int",
    "planned_promotion_minimum_quantity": "int",
}


class PanelPolicy(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    history_days: Literal[28] = 28
    minimum_active_history_days: Literal[28] = 28
    minimum_known_history_days: Literal[7] = 7
    lag_days: tuple[Literal[1], Literal[7], Literal[14], Literal[28]] = (1, 7, 14, 28)
    rolling_days: tuple[Literal[7], Literal[14], Literal[28]] = (7, 14, 28)
    lag_definition: Literal["sales_at_origin_date_plus_1_minus_k"] = (
        "sales_at_origin_date_plus_1_minus_k"
    )
    rolling_definition: Literal["origin_date_plus_1_minus_window_through_origin_date"] = (
        "origin_date_plus_1_minus_window_through_origin_date"
    )
    rolling_missing_policy: Literal["available_observations_only_with_count"] = (
        "available_observations_only_with_count"
    )
    rolling_std_ddof: Annotated[int, Field(ge=0, le=0)] = 0
    closed_history_policy: Literal["confirmed_closed_zero_is_known_history"] = (
        "confirmed_closed_zero_is_known_history"
    )
    target_closed_policy: Literal["retain_row_not_scoring_eligible"] = (
        "retain_row_not_scoring_eligible"
    )
    promotion_policy: Literal["highest_priority_known_offer_without_future_basket_quantity"] = (
        "highest_priority_known_offer_without_future_basket_quantity"
    )
    preprocessing: Literal["none_no_fitted_encoding_or_imputation"] = (
        "none_no_fitted_encoding_or_imputation"
    )


class Reference(Contract):
    table: str
    record_id: str
    record_sha256: Sha256
    available_at: UtcTime

    @model_validator(mode="after")
    def allowlisted(self) -> Self:
        if self.table not in TABLES:
            raise ValueError("forecast_reference_table_not_allowlisted")
        return self


class PanelPoint(SellingKey):
    forecast_origin: UtcTime
    business_date: date
    is_active_assortment: TrueFlag = True
    status: ObservationStatus
    observed_units: Annotated[int, Field(ge=0)] | None
    source_data_complete: StrictBool
    location_open: StrictBool | None
    source_available_at: UtcTime | None
    references: tuple[Reference, ...]

    @model_validator(mode="after")
    def valid_observation(self) -> Self:
        if self.business_date > self.forecast_origin.date():
            raise ValueError("future_sales_in_forecast_panel")
        if self.status == "missing":
            if (
                self.observed_units is not None
                or self.source_data_complete
                or self.source_available_at is not None
            ):
                raise ValueError("missing_history_is_not_zero")
        else:
            if (
                self.observed_units is None
                or not self.source_data_complete
                or self.source_available_at is None
            ):
                raise ValueError("known_history_requires_complete_observation")
            if (self.status == "observed_positive") != (self.observed_units > 0):
                raise ValueError("forecast_panel_status_quantity_mismatch")
        if any(r.available_at > self.forecast_origin for r in self.references) or (
            self.source_available_at is not None and self.source_available_at > self.forecast_origin
        ):
            raise ValueError("forecast_panel_available_after_origin")
        return self


class HistoryContext(SellingKey):
    forecast_origin: UtcTime
    points: tuple[PanelPoint, ...] = Field(max_length=28)

    @model_validator(mode="after")
    def ordered_history(self) -> Self:
        dates = tuple(p.business_date for p in self.points)
        if dates != tuple(sorted(set(dates))) or any(
            (p.product_id, p.selling_location_id, p.channel, p.forecast_origin)
            != (self.product_id, self.selling_location_id, self.channel, self.forecast_origin)
            for p in self.points
        ):
            raise ValueError("forecast_history_context_key_mismatch")
        if self.forecast_origin != end_of_day(self.forecast_origin.date()) or any(
            p.business_date < self.forecast_origin.date() - timedelta(days=27) for p in self.points
        ):
            raise ValueError("forecast_history_context_calendar_boundary_mismatch")
        return self

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class InputValue(Contract):
    name: Annotated[str, Field(pattern="^(" + "|".join(FEATURE_TYPES) + ")$")]
    kind: Kind
    value: StrictInt | StrictFloat | StrictBool | StrictStr | None
    status: Literal["available", "missing", "not_applicable"]
    source_available_at: UtcTime | None
    reason: str | None
    references: tuple[Reference, ...] = ()
    observed_through_date: date | None = None
    effective_date: date | None = None

    @model_validator(mode="after")
    def typed_value(self) -> Self:
        if self.name not in FEATURE_TYPES:
            raise ValueError("forecast_feature_not_allowlisted")
        expected_kind = (
            "observed"
            if self.name.startswith(("origin_lag_", "rolling_"))
            else "calendar"
            if self.name.startswith("target_")
            else "categorical"
            if self.name
            in {"category_id", "brand", "country_code", "calendar_jurisdiction", "channel"}
            else "known_plan"
        )
        if self.kind != expected_kind:
            raise ValueError("forecast_feature_kind_mismatch")
        expected = {"int": int, "float": float, "bool": bool, "str": str}[FEATURE_TYPES[self.name]]
        if self.status == "available":
            if (
                type(self.value) is not expected
                or self.source_available_at is None
                or self.reason is not None
            ):
                raise ValueError("available_forecast_feature_invalid_type_or_availability")
        elif self.value is not None or self.source_available_at is not None or self.reason is None:
            raise ValueError("missing_forecast_feature_is_not_zero")
        return self


class InputRow(ForecastKey):
    schema_version: Literal["1.0.0"] = "1.0.0"
    history_context_sha256: Sha256
    history_active_days: Annotated[int, Field(ge=0, le=28)]
    history_known_days: Annotated[int, Field(ge=0, le=28)]
    history_missing_days: Annotated[int, Field(ge=0, le=28)]
    history_closed_days: Annotated[int, Field(ge=0, le=28)]
    is_active_assortment: TrueFlag = True
    insufficient_history: StrictBool
    target_calendar_eligible: StrictBool
    values: tuple[InputValue, ...]

    @model_validator(mode="after")
    def point_in_time(self) -> Self:
        if tuple(v.name for v in self.values) != tuple(FEATURE_TYPES):
            raise ValueError("forecast_feature_schema_order_or_coverage_mismatch")
        if (
            self.history_known_days + self.history_missing_days != self.history_active_days
            or self.history_closed_days > self.history_known_days
        ):
            raise ValueError("forecast_history_counts_mismatch")
        if any(
            (v.source_available_at is not None and v.source_available_at > self.forecast_origin)
            or any(r.available_at > self.forecast_origin for r in v.references)
            for v in self.values
        ):
            raise ValueError("forecast_feature_available_after_origin")
        values = {v.name: v.value for v in self.values}
        if values["channel"] != self.channel:
            raise ValueError("forecast_feature_channel_key_mismatch")
        derived_calendar = {
            "target_weekday": self.target_date.weekday(),
            "target_week_of_year": self.target_date.isocalendar().week,
            "target_month": self.target_date.month,
            "target_quarter": (self.target_date.month - 1) // 3 + 1,
            "target_is_weekend": self.target_date.weekday() >= 5,
        }
        if any(values[k] != expected for k, expected in derived_calendar.items()):
            raise ValueError("forecast_derived_calendar_mismatch")
        if self.insufficient_history != (
            self.history_active_days < 28 or self.history_known_days < 7
        ):
            raise ValueError("forecast_minimum_history_policy_mismatch")
        if any(
            (
                v.kind == "observed"
                and (
                    v.observed_through_date is None
                    or v.observed_through_date > self.forecast_origin.date()
                    or v.effective_date is not None
                )
            )
            or (
                v.kind != "observed"
                and (v.effective_date != self.target_date or v.observed_through_date is not None)
            )
            for v in self.values
        ):
            raise ValueError("forecast_feature_date_boundary_mismatch")
        if any(
            v.kind == "observed"
            and v.observed_through_date
            != (
                self.forecast_origin.date() + timedelta(days=1 - int(v.name.split("_")[2]))
                if v.name.startswith("origin_lag_")
                else self.forecast_origin.date()
            )
            for v in self.values
        ):
            raise ValueError("forecast_observed_feature_calendar_shift_mismatch")
        if self.target_calendar_eligible != (values["target_location_open"] is True):
            raise ValueError("target_scoring_requires_known_open_calendar")
        return self
