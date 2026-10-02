"""Evaluation v2: explicit forecast functionals, no fitted or outcome-dependent thresholds."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract

Units = Annotated[float, Field(ge=0)]


class QualityPolicyV2(Contract):
    version: Literal["forecast-quality-2.0.0"] = "forecast-quality-2.0.0"
    point_contract: Literal["separate_conditional_median_and_mean"] = (
        "separate_conditional_median_and_mean"
    )
    median_objective: Literal["mae"] = "mae"
    mean_objective: Literal["mse_with_bias_guard"] = "mse_with_bias_guard"
    minimum_relative_mae_improvement: Annotated[float, Field(ge=0.05, le=0.05)] = 0.05
    maximum_segment_mae_regression: Annotated[float, Field(ge=0.10, le=0.10)] = 0.10
    maximum_absolute_normalized_mean_bias: Annotated[float, Field(ge=0.10, le=0.10)] = 0.10
    mean_mse_guard: Literal["no_regression_against_mean_baseline"] = (
        "no_regression_against_mean_baseline"
    )
    minimum_global_rows: Literal[100] = 100
    minimum_segment_rows: Literal[30] = 30
    minimum_eligibility_coverage: Annotated[float, Field(ge=0.80, le=0.80)] = 0.80
    minimum_prediction_coverage: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    minimum_calibration_rows: Literal[50] = 50
    nominal_coverage: Annotated[float, Field(ge=0.90, le=0.90)] = 0.90
    minimum_empirical_coverage: Annotated[float, Field(ge=0.80, le=0.80)] = 0.80
    interval_objective: Literal["central_interval_score_no_regression_against_baseline"] = (
        "central_interval_score_no_regression_against_baseline"
    )
    width_to_actual_ratio: Literal["diagnostic_only_no_denominator_floor"] = (
        "diagnostic_only_no_denominator_floor"
    )
    legacy_width_ratio_threshold: Annotated[float, Field(ge=2.0, le=2.0)] = 2.0
    zero_actuals: Literal["absolute_errors_and_false_positive_units_no_ratio_gate"] = (
        "absolute_errors_and_false_positive_units_no_ratio_gate"
    )
    perfect_baseline: Literal["zero_error_tie_passes_positive_error_fails"] = (
        "zero_error_tie_passes_positive_error_fails"
    )
    required_volume_bins: tuple[
        Literal["zero"], Literal["low"], Literal["medium"], Literal["high"]
    ] = ("zero", "low", "medium", "high")
    volume_basis: Literal["origin_known_rolling_28_mean_not_target_outcome"] = (
        "origin_known_rolling_28_mean_not_target_outcome"
    )
    baseline_selection: Literal["validation_only_separately_by_mae_mse_and_interval_score"] = (
        "validation_only_separately_by_mae_mse_and_interval_score"
    )
    compatibility: Literal["v1_reports_immutable_single_output_never_relabelled_as_two_targets"] = (
        "v1_reports_immutable_single_output_never_relabelled_as_two_targets"
    )
    evaluation_use: Literal["freeze_before_unseen_development_holdout_no_reselection"] = (
        "freeze_before_unseen_development_holdout_no_reselection"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"


class CentralInterval(Contract):
    lower: Units
    upper: Units

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.lower > self.upper:
            raise ValueError("interval_bounds_reversed")
        return self


class FunctionalForecast(Contract):
    median: Units | None
    mean: Units | None
    interval: CentralInterval | None

    @model_validator(mode="after")
    def quantiles(self) -> Self:
        if (
            self.interval is not None
            and self.median is not None
            and not (self.interval.lower <= self.median <= self.interval.upper)
        ):
            raise ValueError("median_outside_central_interval")
        # A skewed distribution's mean need not lie between its 5th and 95th quantiles.
        return self


class ProtocolObservation(Contract):
    key: Annotated[str, Field(min_length=1, max_length=1024)]
    actual: Annotated[int, Field(ge=0)] | None
    exclusion_reasons: tuple[str, ...] = ()
    candidate: FunctionalForecast
    baseline: FunctionalForecast

    @model_validator(mode="after")
    def observed(self) -> Self:
        if not self.exclusion_reasons and self.actual is None:
            raise ValueError("eligible_actual_missing_not_zero")
        return self
