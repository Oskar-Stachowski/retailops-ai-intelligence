"""A new fitting protocol; earlier development recipes and identities stay immutable."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, UtcTime


class QualificationPolicy(Contract):
    version: Literal["stockout-independent-development-1.0.0"] = (
        "stockout-independent-development-1.0.0"
    )
    base_fit_known_at: UtcTime
    calibration_start_at: UtcTime
    calibration_fit_known_at: UtcTime
    calibration_source_role: Literal["train"] = "train"
    validation_role: Literal["calibration"] = "calibration"
    calibration_protocol: Literal["later_out_of_sample_predictions_with_exact_label_purge"] = (
        "later_out_of_sample_predictions_with_exact_label_purge"
    )
    model_selection: Literal["raw_tune_AP_then_Brier_then_LR_without_upstream"] = (
        "raw_tune_AP_then_Brier_then_LR_without_upstream"
    )
    minimum_segment_rows: Annotated[int, Field(ge=20, le=10000)] = 20
    minimum_segment_per_class: Annotated[int, Field(ge=5, le=1000)] = 5
    expected_category_count: Annotated[int, Field(ge=1, le=32)] = 8
    expected_stock_location_count: Annotated[int, Field(ge=1, le=32)] = 2
    maximum_expected_calibration_error: Annotated[float, Field(gt=0, le=0.15)] = 0.15
    quality_policy_status: Literal["proposal_before_final_test"] = "proposal_before_final_test"
    final_test: Literal["unopened_separate_approved_campaign_required"] = (
        "unopened_separate_approved_campaign_required"
    )

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if not (
            self.base_fit_known_at <= self.calibration_start_at < self.calibration_fit_known_at
        ):
            raise ValueError("stockout_qualification_calibration_chronology")
        return self
