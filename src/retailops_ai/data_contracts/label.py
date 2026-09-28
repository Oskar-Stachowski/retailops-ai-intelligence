"""Mature observed-sales outcomes, kept separate from feature values."""

from typing import Literal, Self

from pydantic import model_validator

from retailops_ai.data_contracts.common import (
    DataLineage,
    ForecastKey,
    LabelID,
    NonNegativeInt,
    UtcTime,
    Versioned,
    end_of_day,
)


class LabelRecord(Versioned):
    contract_type: Literal["label"]
    label_dataset_id: LabelID
    lineage: DataLineage
    key: ForecastKey
    target_type: Literal["observed_sales_units"]
    status: Literal["eligible", "censored"]
    observed_sales_units: NonNegativeInt | None
    label_available_at: UtcTime | None
    reason: Literal["incomplete_window", "missing_source"] | None

    @model_validator(mode="after")
    def maturity(self) -> Self:
        if self.status == "eligible":
            if (
                self.observed_sales_units is None
                or self.label_available_at is None
                or self.reason is not None
            ):
                raise ValueError("eligible_label_incomplete")
            if self.label_available_at < end_of_day(self.key.target_date):
                raise ValueError("label_outcome_not_mature")
        elif (
            self.observed_sales_units is not None
            or self.label_available_at is not None
            or self.reason is None
        ):
            raise ValueError("censored_label_must_not_be_zero")
        return self
