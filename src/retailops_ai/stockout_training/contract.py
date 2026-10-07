"""Frozen, bounded training recipe and portable non-executable model state."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, UtcTime
from retailops_ai.forecasting.model_contract import LearnedEstimator
from retailops_ai.stockout.feature_contract import FeatureValues

Family = Literal["logistic_regression", "hist_gradient_boosting"]
Variant = Literal["without_upstream", "with_upstream", "raw_sales_only"]
CATEGORIES = ("stock_location_id", "category_id")
UPSTREAM_COLUMNS = ("forecast_units_7d", "forecast_days_of_supply", "forecast_unavailable")


def columns(variant: Variant) -> tuple[str, ...]:
    base = tuple(FeatureValues.model_fields)
    if variant == "without_upstream":
        return base
    if variant == "with_upstream":
        return (*base, *UPSTREAM_COLUMNS)
    if variant == "raw_sales_only":
        return tuple(c for c in base if c not in {"in_stock_sales_mean", "days_of_supply_in_stock"})
    raise ValueError("stockout_training_variant_not_allowlisted")


class TrainingPolicy(Contract):
    version: Literal["stockout-development-models-1.0.0"] = "stockout-development-models-1.0.0"
    configurations: Literal["one_frozen_configuration_per_family"] = (
        "one_frozen_configuration_per_family"
    )
    sampling: Literal["all_eligible_rows_natural_prevalence_no_class_weights"] = (
        "all_eligible_rows_natural_prevalence_no_class_weights"
    )
    imputation: Literal["train_median_empty_zero_all_column_missing_indicators"] = (
        "train_median_empty_zero_all_column_missing_indicators"
    )
    categories: Literal["train_only_stock_location_and_PIT_category_unknown_all_zero"] = (
        "train_only_stock_location_and_PIT_category_unknown_all_zero"
    )
    selection: Literal["tune_AP_then_Brier_then_LR_without_upstream"] = (
        "tune_AP_then_Brier_then_LR_without_upstream"
    )
    calibration: Literal["fixed_regularized_sigmoid_separate_calibration_period"] = (
        "fixed_regularized_sigmoid_separate_calibration_period"
    )
    calibration_reporting: Literal["in_sample_fit_diagnostic_not_generalization"] = (
        "in_sample_fit_diagnostic_not_generalization"
    )
    lr_C: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    lr_max_iter: Literal[1000] = 1000
    lr_tol: Annotated[float, Field(ge=1e-8, le=1e-8)] = 1e-8
    hgb_max_iter: Literal[80] = 80
    hgb_max_leaf_nodes: Literal[15] = 15
    hgb_min_samples_leaf: Literal[20] = 20
    hgb_l2_regularization: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    hgb_learning_rate: Annotated[float, Field(ge=0.1, le=0.1)] = 0.1
    hgb_early_stopping: Literal[False] = False
    random_state: Literal[42] = 42
    cpu_threads: Literal[1] = 1
    minimum_calibration_per_class: Literal[10] = 10
    capacity_fraction: Annotated[float, Field(ge=0.2, le=0.2)] = 0.2
    capacity_unit: Literal["ceil_fraction_per_origin_physical_keys_lexical_ties"] = (
        "ceil_fraction_per_origin_physical_keys_lexical_ties"
    )
    false_attention_cost: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    missed_incident_cost: Annotated[float, Field(ge=5.0, le=5.0)] = 5.0
    cost_interpretation: Literal["fixed_exploratory_units_not_validated_business_savings"] = (
        "fixed_exploratory_units_not_validated_business_savings"
    )
    final_test: Literal["no_outcome_scoring_selection_calibration_or_threshold_access"] = (
        "no_outcome_scoring_selection_calibration_or_threshold_access"
    )


DEFAULT_POLICY = TrainingPolicy()
MAX_BYTES = 16 * 1024**2
MAX_ROWS = 10000


class Preprocessing(Contract):
    numeric_columns: tuple[str, ...] = Field(min_length=1, max_length=21)
    medians: tuple[float, ...]
    means: tuple[float, ...]
    scales: tuple[Annotated[float, Field(gt=0)], ...]
    category_values: dict[str, tuple[str, ...]]
    scaled: bool
    output_columns: tuple[str, ...]
    train_rows: Annotated[int, Field(ge=2, le=MAX_ROWS)]
    train_keys_sha256: str

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        n = len(self.numeric_columns)
        if (
            len(set(self.numeric_columns)) != n
            or len(self.medians) != n
            or len(self.means) != 2 * n
            or len(self.scales) != 2 * n
            or set(self.category_values) != set(CATEGORIES)
            or any(
                not values or len(values) > 32 or tuple(sorted(set(values))) != values
                for values in self.category_values.values()
            )
            or self.output_columns
            != (
                *self.numeric_columns,
                *("missing:" + c for c in self.numeric_columns),
                *(f"{c}={v}" for c in CATEGORIES for v in self.category_values[c]),
            )
        ):
            raise ValueError("stockout_preprocessing_dimensions_or_vocabulary_invalid")
        return self


class LinearEstimator(Contract):
    weights: tuple[float, ...] = Field(min_length=1, max_length=106)
    intercept: float


class Sigmoid(Contract):
    slope: float
    intercept: float
    fit_known_at: UtcTime
    calibration_rows: Annotated[int, Field(ge=20, le=MAX_ROWS)]
    calibration_keys_sha256: str
    calibration_labels_sha256: str
    method: Literal["regularized_logistic_on_raw_log_odds_C1"] = (
        "regularized_logistic_on_raw_log_odds_C1"
    )


class RiskPipeline(Contract):
    family: Family
    variant: Variant
    fit_known_at: UtcTime
    preprocessing: Preprocessing
    estimator: LinearEstimator | LearnedEstimator
    train_labels_sha256: str
    sigmoid: Sigmoid | None = None

    @model_validator(mode="after")
    def recipe(self) -> Self:
        size = len(self.preprocessing.output_columns)
        if self.preprocessing.numeric_columns != columns(self.variant):
            raise ValueError("stockout_pipeline_feature_allowlist_mismatch")
        if self.preprocessing.scaled != (self.family == "logistic_regression"):
            raise ValueError("stockout_pipeline_scaling_recipe_mismatch")
        if self.family == "logistic_regression":
            if (
                not isinstance(self.estimator, LinearEstimator)
                or len(self.estimator.weights) != size
            ):
                raise ValueError("stockout_linear_dimensions_invalid")
        elif (
            not isinstance(self.estimator, LearnedEstimator)
            or self.estimator.family != "hist_gradient_boosting"
            or self.estimator.feature_count != size
            or self.estimator.aggregation != "baseline_plus_sum"
        ):
            raise ValueError("stockout_hgb_dimensions_invalid")
        if self.sigmoid is not None and self.sigmoid.fit_known_at <= self.fit_known_at:
            raise ValueError("stockout_calibrator_must_be_later_than_training")
        return self
