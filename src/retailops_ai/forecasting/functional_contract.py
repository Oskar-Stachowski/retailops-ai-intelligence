"""Versioned model and selection recipe for the frozen two-target quality protocol."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, ModelID, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.model_contract import LearnedEstimator, ModelPolicy
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_v2_contract import QualityPolicyV2

Head = Literal["rf_mean", "hgb_mean", "hgb_median", "hgb_lower", "hgb_upper"]
HEADS: tuple[Head, ...] = ("rf_mean", "hgb_mean", "hgb_median", "hgb_lower", "hgb_upper")
BASELINES: tuple[Literal["history7"], Literal["history28"], Literal["weekday28"]] = (
    "history7",
    "history28",
    "weekday28",
)


class FunctionalPolicy(Contract):
    version: Literal["forecast-functional-recipe-1.0.0"] = "forecast-functional-recipe-1.0.0"
    model: ModelPolicy = ModelPolicy()
    reused_model_policy_scope: Literal[
        "estimator_hyperparameters_resources_only_selection_is_functional_recipe"
    ] = "estimator_hyperparameters_resources_only_selection_is_functional_recipe"
    quality: QualityPolicyV2 = QualityPolicyV2()
    heads: tuple[Head, ...] = HEADS
    baseline_candidates: tuple[Literal["history7"], Literal["history28"], Literal["weekday28"]] = (
        BASELINES
    )
    baseline_statistics: Literal[
        "empirical_median_mean_and_inverted_cdf_05_95_of_as_of_history"
    ] = "empirical_median_mean_and_inverted_cdf_05_95_of_as_of_history"
    validation_partition: Literal["first_half_calibration_second_half_selection"] = (
        "first_half_calibration_second_half_selection"
    )
    selection_groups: Literal["origin_known_volume_category"] = "origin_known_volume_category"
    minimum_group_rows: Annotated[int, Field(ge=30, le=30)] = 30
    minimum_correction_ratio: Annotated[float, Field(ge=0.5, le=0.5)] = 0.5
    maximum_correction_ratio: Annotated[float, Field(ge=2.0, le=2.0)] = 2.0
    point_correction: Literal[
        "first_half_median_residual_or_mean_ratio_candidates_identity_retained"
    ] = "first_half_median_residual_or_mean_ratio_candidates_identity_retained"
    interval_calibration: Literal["first_half_endpoint_residual_quantiles_05_95"] = (
        "first_half_endpoint_residual_quantiles_05_95"
    )
    interval_fallback: tuple[Literal["volume_category"], Literal["volume"], Literal["global"]] = (
        "volume_category",
        "volume",
        "global",
    )
    median_gate: Literal["both_blocks_mae_guard_second_block_global_5pct_or_exact_baseline"] = (
        "both_blocks_mae_guard_second_block_global_5pct_or_exact_baseline"
    )
    mean_gate: Literal["both_blocks_bias_guard_and_mse_no_regression_or_exact_baseline"] = (
        "both_blocks_bias_guard_and_mse_no_regression_or_exact_baseline"
    )
    interval_gate: Literal["second_block_score_no_regression_and_coverage_or_baseline"] = (
        "second_block_score_no_regression_and_coverage_or_baseline"
    )
    tie_break: Literal["baseline_candidate_order_then_identity_then_head_order"] = (
        "baseline_candidate_order_then_identity_then_head_order"
    )
    max_index_bytes: Annotated[int, Field(ge=1024**2, le=2 * 1024**3)] = 2 * 1024**3
    max_prediction_bytes: Annotated[int, Field(ge=1024**2, le=2 * 1024**3)] = 2 * 1024**3
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"

    @model_validator(mode="after")
    def head_inventory(self) -> Self:
        if self.heads != HEADS:
            raise ValueError("functional_requires_all_five_heads")
        return self


class FunctionalPipeline(Contract):
    schema_version: Literal["2.0.0"] = "2.0.0"
    model_id: ModelID
    head: Head
    policy: FunctionalPolicy
    preprocessing: FittedState
    train_labels_sha256: Sha256
    estimator: LearnedEstimator
    code_sha256: Sha256
    generated_at: UtcTime

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"model_id", "generated_at"}) | {
            "preprocessing": self.preprocessing.descriptor.model_dump(mode="json")
        }

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.model_id != "model-sha256-" + canonical_sha256(self.identity_payload()):
            raise ValueError("functional_pipeline_identity_mismatch")
        expected_family = "random_forest" if self.head == "rf_mean" else "hist_gradient_boosting"
        if (
            self.estimator.family != expected_family
            or self.estimator.feature_count != len(self.preprocessing.descriptor.output_columns) + 1
        ):
            raise ValueError("functional_pipeline_head_or_dimension_mismatch")
        return self
