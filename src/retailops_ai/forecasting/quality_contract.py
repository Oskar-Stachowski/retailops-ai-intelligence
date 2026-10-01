"""Frozen development quality protocol; no final-test access or model promotion."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FeatureID, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.evaluation_contract import MetricResult
from retailops_ai.forecasting.manifest_contract import FileReceipt

Count = Annotated[int, Field(ge=0)]
Positive = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
QualityStatus = Literal["passed", "failed", "not_ready"]


class QualityPolicy(Contract):
    version: Literal["forecast-development-quality-1.0.0"] = "forecast-development-quality-1.0.0"
    use: Literal["development_diagnostics_on_previously_observed_data"] = (
        "development_diagnostics_on_previously_observed_data"
    )
    primary_metric: Literal["mae"] = "mae"
    minimum_relative_improvement: Annotated[float, Field(ge=0, lt=1)] = 0.05
    baseline_fallback: Literal["retain_validation_baseline_without_improvement_requirement"] = (
        "retain_validation_baseline_without_improvement_requirement"
    )
    maximum_absolute_normalized_bias: Fraction = 0.10
    maximum_critical_segment_relative_regression: Positive = 0.10
    minimum_global_rows: Annotated[int, Field(ge=1, le=100000)] = 100
    minimum_segment_rows: Annotated[int, Field(ge=1, le=100000)] = 30
    minimum_prediction_coverage: Annotated[float, Field(ge=1, le=1)] = 1.0
    minimum_eligibility_coverage: Fraction = 0.80
    critical_dimensions: tuple[
        Literal["horizon"], Literal["category"], Literal["channel"], Literal["volume"]
    ] = ("horizon", "category", "channel", "volume")
    volume_basis: Literal["origin_known_rolling_28_mean_not_target_outcome"] = (
        "origin_known_rolling_28_mean_not_target_outcome"
    )
    low_volume_upper_exclusive: Annotated[float, Field(gt=0)] = 5.0
    medium_volume_upper_exclusive: Annotated[float, Field(gt=0)] = 20.0
    required_volume_bins: tuple[
        Literal["zero"], Literal["low"], Literal["medium"], Literal["high"]
    ] = ("zero", "low", "medium", "high")
    zero_denominator: Literal["null_not_evaluable_blocks_quality_gate"] = (
        "null_not_evaluable_blocks_quality_gate"
    )
    mape: Literal["positive_actuals_only_diagnostic_with_coverage"] = (
        "positive_actuals_only_diagnostic_with_coverage"
    )
    intervals: Literal["per_fold_method_horizon_absolute_validation_residual_quantile"] = (
        "per_fold_method_horizon_absolute_validation_residual_quantile"
    )
    nominal_coverage: Annotated[float, Field(gt=0, lt=1)] = 0.90
    minimum_calibration_rows: Annotated[int, Field(ge=1, le=100000)] = 50
    quantile: Literal["ceil_n_plus_1_times_nominal_unattainable_rank_not_ready"] = (
        "ceil_n_plus_1_times_nominal_unattainable_rank_not_ready"
    )
    interval_lower_bound: Literal["clip_at_zero_observed_sales_nonnegative"] = (
        "clip_at_zero_observed_sales_nonnegative"
    )
    minimum_empirical_coverage: Fraction = 0.80
    maximum_mean_width_to_mean_actual: Annotated[float, Field(gt=0)] = 2.0
    inventory_segments: Literal["deferred_until_verified_inventory_after_ai06"] = (
        "deferred_until_verified_inventory_after_ai06"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    max_groups: Annotated[int, Field(ge=1, le=10000)] = 4000

    @model_validator(mode="after")
    def limits(self) -> Self:
        if self.low_volume_upper_exclusive >= self.medium_volume_upper_exclusive:
            raise ValueError("quality_volume_threshold_order")
        if self.minimum_empirical_coverage > self.nominal_coverage:
            raise ValueError("quality_empirical_minimum_exceeds_nominal")
        return self


class IntervalMetric(Contract):
    eligible_rows: Count
    interval_rows: Count
    status: Literal["passed", "incomplete", "not_evaluable"]
    nominal_coverage: Fraction
    empirical_coverage: Fraction | None
    mean_width: Positive | None

    @model_validator(mode="after")
    def counts(self) -> Self:
        expected = (
            "not_evaluable"
            if not self.eligible_rows
            else "incomplete"
            if self.interval_rows < self.eligible_rows
            else "passed"
        )
        if self.interval_rows > self.eligible_rows or self.status != expected:
            raise ValueError("quality_interval_count_status_mismatch")
        if (
            (self.status == "passed")
            != (self.empirical_coverage is not None and self.mean_width is not None)
            or self.status != "passed"
            and (self.empirical_coverage is not None or self.mean_width is not None)
        ):
            raise ValueError("quality_partial_interval_metrics_forbidden")
        return self


class SegmentMetric(Contract):
    fold: str
    role: Literal["validation", "development_holdout"]
    method: str
    dimension: Literal["global", "horizon", "category", "channel", "volume"]
    value: str
    total_rows: Count
    excluded_rows: Count
    exclusion_counts: dict[str, Count]
    eligibility_coverage: Fraction | None
    prediction_coverage: Fraction | None
    point: MetricResult
    rmse: Positive | None
    bias: float | None
    normalized_bias: float | None
    underforecast_units: Positive | None
    overforecast_units: Positive | None
    underforecast_rows: Count
    overforecast_rows: Count
    exact_rows: Count
    positive_actual_rows: Count
    mape_positive_actuals: Positive | None
    mape_coverage: Fraction | None
    zero_actual_rows: Count
    zero_actual_excess_forecast_units: Positive | None
    interval: IntervalMetric

    @model_validator(mode="after")
    def coverage(self) -> Self:
        n = self.point.eligible_rows
        if self.total_rows != n + self.excluded_rows or self.interval.eligible_rows != n:
            raise ValueError("quality_segment_count_mismatch")
        if self.eligibility_coverage != (n / self.total_rows if self.total_rows else None):
            raise ValueError("quality_eligibility_coverage_mismatch")
        if self.prediction_coverage != (self.point.predicted_rows / n if n else None):
            raise ValueError("quality_prediction_coverage_mismatch")
        if self.point.status == "passed":
            if (
                self.underforecast_rows + self.overforecast_rows + self.exact_rows != n
                or self.positive_actual_rows + self.zero_actual_rows != n
                or self.mape_coverage != self.positive_actual_rows / n
                or self.rmse is None
                or self.bias is None
                or self.underforecast_units is None
                or self.overforecast_units is None
                or self.zero_actual_excess_forecast_units is None
                or (self.mape_positive_actuals is not None) != bool(self.positive_actual_rows)
            ):
                raise ValueError("quality_complete_segment_required")
        elif any(
            v is not None
            for v in (
                self.rmse,
                self.bias,
                self.normalized_bias,
                self.underforecast_units,
                self.overforecast_units,
                self.mape_positive_actuals,
                self.mape_coverage,
                self.zero_actual_excess_forecast_units,
            )
        ):
            raise ValueError("quality_partial_point_metrics_forbidden")
        return self


class QualityDescriptor(Contract):
    role: Literal["development_quality_report"] = "development_quality_report"
    backtest_id: Annotated[str, Field(pattern=r"^forecast-backtest-sha256-[0-9a-f]{64}$")]
    backtest_descriptor_sha256: Sha256
    feature_set_id: FeatureID
    policy: QualityPolicy
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    python_version: str
    quality_status: QualityStatus
    gate_counts: dict[QualityStatus, Count]
    calibration_status_counts: dict[str, Count]
    segment_count: Count
    interval_rows: Count
    report_sha256: dict[str, Sha256]

    @model_validator(mode="after")
    def digest(self) -> Self:
        if not self.code_files or canonical_sha256(self.code_files) != self.code_sha256:
            raise ValueError("quality_code_hash_mismatch")
        if set(self.gate_counts) != {"passed", "failed", "not_ready"}:
            raise ValueError("quality_gate_count_inventory")
        expected = (
            "not_ready"
            if self.gate_counts["not_ready"]
            else "failed"
            if self.gate_counts["failed"]
            else "passed"
        )
        if self.quality_status != expected or not sum(self.gate_counts.values()):
            raise ValueError("quality_gate_status_mismatch")
        return self


class QualityManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    quality_id: Annotated[str, Field(pattern=r"^forecast-quality-sha256-[0-9a-f]{64}$")]
    descriptor: QualityDescriptor
    receipts: dict[str, FileReceipt]
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.quality_id != "forecast-quality-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ) or set(self.receipts) != set(self.descriptor.report_sha256):
            raise ValueError("quality_identity_or_inventory_mismatch")
        if any(
            receipt.path != name or receipt.sha256 != self.descriptor.report_sha256[name]
            for name, receipt in self.receipts.items()
        ):
            raise ValueError("quality_receipt_binding_mismatch")
        return self
