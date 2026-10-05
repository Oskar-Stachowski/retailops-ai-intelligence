"""Complete conditional sigmoid state and explicit roles for development selection."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, UtcTime
from retailops_ai.stockout_training.contract import MAX_ROWS, RiskPipeline


class QualityRequirements(Contract):
    minimum_segment_rows: Annotated[int, Field(ge=20, le=MAX_ROWS)] = 20
    minimum_segment_per_class: Annotated[int, Field(ge=5, le=1000)] = 5
    expected_category_count: Annotated[int, Field(ge=1, le=32)] = 8
    expected_stock_location_count: Annotated[int, Field(ge=1, le=32)] = 2
    maximum_expected_calibration_error: Annotated[float, Field(gt=0, le=0.15)] = 0.15
    quality_policy_status: Literal["proposal_before_final_test"] = "proposal_before_final_test"


class SelectionPolicy(Contract):
    version: Literal["stockout-later-calibration-selection-2.0.0"] = (
        "stockout-later-calibration-selection-2.0.0"
    )
    development_seed: Literal[42] = 42
    base_source_role: Literal["train"] = "train"
    calibrator_source_role: Literal["tune"] = "tune"
    selection_source_role: Literal["calibration"] = "calibration"
    base_configuration: Literal["stockout-development-models-1.0.0"] = (
        "stockout-development-models-1.0.0"
    )
    calibrator_C_grid: tuple[Annotated[float, Field(ge=0.01, le=10)], ...] = (0.01, 0.1, 1.0, 10.0)
    selection: Literal["failed_segments_then_Brier_then_AP_then_model_name_then_C"] = (
        "failed_segments_then_Brier_then_AP_then_model_name_then_C"
    )
    requirements: QualityRequirements = Field(default_factory=QualityRequirements)
    selection_metrics: Literal["development_selection_not_independent_quality"] = (
        "development_selection_not_independent_quality"
    )
    final_test: Literal["unopened_separate_approved_campaign_required"] = (
        "unopened_separate_approved_campaign_required"
    )

    @model_validator(mode="after")
    def exact_grid(self) -> Self:
        if self.calibrator_C_grid != (0.01, 0.1, 1.0, 10.0):
            raise ValueError("stockout_selection_frozen_calibrator_grid")
        return self


class ConditionalSigmoid(Contract):
    method: Literal["regularized_logistic_raw_score_PIT_category_stock_history_constraint"] = (
        "regularized_logistic_raw_score_PIT_category_stock_history_constraint"
    )
    fit_known_at: UtcTime
    calibration_rows: Annotated[int, Field(ge=20, le=MAX_ROWS)]
    calibration_keys_sha256: Sha256
    calibration_labels_sha256: Sha256
    C: Annotated[float, Field(ge=0.01, le=10)]
    raw_score_slope: Annotated[float, Field(gt=0)]
    intercept: float
    categories: tuple[Symbol, ...] = Field(min_length=1, max_length=32)
    stock_locations: tuple[Symbol, ...] = Field(min_length=1, max_length=32)
    offset_weights: tuple[float, ...] = Field(min_length=3, max_length=65)
    unknown_vocabulary: Literal["all_zero_offsets_no_future_category_fit"] = (
        "all_zero_offsets_no_future_category_fit"
    )
    sampling: Literal["natural_prevalence_no_class_weights"] = "natural_prevalence_no_class_weights"

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if (
            self.C not in (0.01, 0.1, 1.0, 10.0)
            or tuple(sorted(set(self.categories))) != self.categories
            or tuple(sorted(set(self.stock_locations))) != self.stock_locations
            or len(self.offset_weights) != len(self.categories) + len(self.stock_locations) + 1
        ):
            raise ValueError("stockout_conditional_sigmoid_vocabulary_or_dimensions")
        return self


class ConditionalRiskPipeline(Contract):
    version: Literal["stockout-conditional-risk-pipeline-2.0.0"] = (
        "stockout-conditional-risk-pipeline-2.0.0"
    )
    base: RiskPipeline
    calibrator: ConditionalSigmoid

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if self.base.sigmoid is not None or self.calibrator.fit_known_at <= self.base.fit_known_at:
            raise ValueError("stockout_conditional_pipeline_chronology_or_double_calibration")
        return self


class SelectionModelPin(Contract):
    selection_id: Annotated[str, Field(pattern=r"^stockout-selection-sha256-[0-9a-f]{64}$")]
    model_id: Annotated[str, Field(pattern=r"^risk-model-sha256-[0-9a-f]{64}$")]
    calibrator_sha256: Sha256
    selection_known_at: UtcTime
    prediction_mode: Literal["conditional_sigmoid"] = "conditional_sigmoid"
