"""Forecast feature records with an explicit allowlist and knowledge boundary."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DataLineage,
    FeatureID,
    ForecastKey,
    SourceID,
    Symbol,
    UtcTime,
    Versioned,
)

FeatureName = Literal[
    "lag_1_units",
    "rolling_mean_7_units",
    "target_weekday",
    "planned_price_minor_units",
    "planned_promotion",
]
KINDS = {
    "lag_1_units": "observed",
    "rolling_mean_7_units": "observed",
    "target_weekday": "calendar",
    "planned_price_minor_units": "known_plan",
    "planned_promotion": "known_plan",
}


class FeatureValue(Contract):
    name: FeatureName
    kind: Literal["observed", "known_plan", "calendar"]
    source_class: Literal["facts", "known_plan", "calendar"]
    source_dataset_id: SourceID
    status: Literal["available", "missing"]
    value: StrictFloat | StrictInt | StrictBool | None
    source_available_at: UtcTime | None
    observed_through_date: date | None
    effective_date: date | None
    currency: Literal["PLN", "EUR"] | None
    reason: Symbol | None

    @model_validator(mode="after")
    def value_semantics(self) -> Self:
        if self.kind != KINDS[self.name]:
            raise ValueError("feature_kind_not_allowlisted")
        expected_class = "facts" if self.kind == "observed" else self.kind
        if self.source_class != expected_class:
            raise ValueError("feature_source_class_mismatch")
        if self.status == "missing":
            if (
                self.value is not None
                or self.source_available_at is not None
                or self.reason is None
            ):
                raise ValueError("missing_feature_must_not_be_zero")
        elif self.value is None or self.source_available_at is None or self.reason is not None:
            raise ValueError("available_feature_incomplete")
        if self.kind == "observed":
            if self.observed_through_date is None or self.effective_date is not None:
                raise ValueError("observed_feature_requires_observation_date")
        elif self.effective_date is None or self.observed_through_date is not None:
            raise ValueError("plan_calendar_requires_effective_date")
        if (self.name == "planned_price_minor_units") != (self.currency is not None):
            raise ValueError("feature_currency_mismatch")
        if self.value is not None:
            if self.name == "planned_promotion":
                if type(self.value) is not bool:
                    raise ValueError("promotion_feature_requires_boolean")
            elif type(self.value) not in {int, float} or self.value < 0:
                raise ValueError("feature_requires_nonnegative_number")
            elif self.name in {"target_weekday", "planned_price_minor_units"}:
                if type(self.value) is not int:
                    raise ValueError("feature_requires_integer")
                if self.name == "target_weekday" and self.value > 6:
                    raise ValueError("invalid_weekday")
        return self


class FeatureRecord(Versioned):
    contract_type: Literal["feature"]
    feature_set_id: FeatureID
    lineage: DataLineage
    key: ForecastKey
    allowlist_version: Literal["forecast-observed-sales-v1"]
    values: list[FeatureValue] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def point_in_time(self) -> Self:
        if len({v.name for v in self.values}) != len(self.values):
            raise ValueError("duplicate_feature")
        for value in self.values:
            if value.source_dataset_id != self.lineage.source_dataset_id:
                raise ValueError("feature_source_lineage_mismatch")
            if (
                value.source_available_at is not None
                and value.source_available_at > self.key.forecast_origin
            ):
                raise ValueError("feature_available_after_origin")
            if (
                value.observed_through_date is not None
                and value.observed_through_date > self.key.forecast_origin.date()
            ):
                raise ValueError("future_observation_is_not_a_plan")
            if value.effective_date is not None and value.effective_date != self.key.target_date:
                raise ValueError("feature_effective_date_mismatch")
            if (
                value.name == "target_weekday"
                and value.status == "available"
                and value.value != self.key.target_date.weekday()
            ):
                raise ValueError("calendar_feature_mismatch")
        return self
