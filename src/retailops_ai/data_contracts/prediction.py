"""Daily observed-sales forecasts, pinned lineage, explicit interval absence."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DataLineage,
    FeatureID,
    ForecastKey,
    Freshness,
    PredictionDatasetID,
    PredictionID,
    RunID,
    Symbol,
    Units,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.model import ModelRecord


class PredictionInterval(Contract):
    lower: Units
    upper: Units
    nominal_coverage: Annotated[float, Field(gt=0, lt=1)]
    method: Literal["validation_residual_quantiles"]
    calibration_version: Symbol

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("prediction_interval_unordered")
        return self


class PredictionRecord(Versioned):
    contract_type: Literal["prediction"]
    prediction_id: PredictionID
    prediction_dataset_id: PredictionDatasetID
    key: ForecastKey
    target_type: Literal["observed_sales_units"]
    unit_of_measure: Literal["unit"]
    predicted_units: Units
    interval: PredictionInterval | None
    interval_reason: Literal["not_calibrated", "insufficient_calibration"] | None
    lineage: DataLineage
    feature_set_id: FeatureID
    inference_run_id: RunID
    model: ModelRecord
    release_id: Symbol
    generated_at: UtcTime
    freshness_status: Freshness
    quality_status: Literal["passed", "warning", "not_evaluable"]

    @model_validator(mode="after")
    def valid_forecast(self) -> Self:
        if (self.interval is None) != (self.interval_reason is not None):
            raise ValueError("interval_absence_requires_reason")
        if (
            self.interval is not None
            and not self.interval.lower <= self.predicted_units <= self.interval.upper
        ):
            raise ValueError("estimate_outside_prediction_interval")
        if self.model.selection_cutoff > self.key.forecast_origin:
            raise ValueError("model_selected_after_prediction_origin")
        if self.generated_at < self.key.forecast_origin:
            raise ValueError("prediction_generated_before_origin")
        if self.prediction_id != "prediction-sha256-" + canonical_sha256(self.identity_payload()):
            raise ValueError("prediction_identity_mismatch")
        return self

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={
                "prediction_id",
                "prediction_dataset_id",
                "generated_at",
                "freshness_status",
            },
        )
