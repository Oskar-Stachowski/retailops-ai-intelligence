"""Extended statistics around the shared MAE/WAPE evaluator, with explicit missingness."""

import math
from collections import Counter
from typing import Any

from retailops_ai.forecasting.evaluation import MetricAccumulator
from retailops_ai.forecasting.quality_contract import IntervalMetric, QualityPolicy, SegmentMetric


def volume_bin(value: float | int | None, policy: QualityPolicy) -> str:
    if value is None:
        return "unknown"
    if not math.isfinite(value) or value < 0:
        raise ValueError("quality_invalid_historical_volume")
    return (
        "zero"
        if value == 0
        else "low"
        if value < policy.low_volume_upper_exclusive
        else "medium"
        if value < policy.medium_volume_upper_exclusive
        else "high"
    )


def residual_rank(n: int, policy: QualityPolicy) -> int | None:
    rank = math.ceil((n + 1) * policy.nominal_coverage)
    return rank if n >= policy.minimum_calibration_rows and 1 <= rank <= n else None


def interval_bounds(
    predicted: float, residual_quantile: float | None
) -> tuple[float, float] | None:
    if residual_quantile is None:
        return None
    if not all(math.isfinite(x) and x >= 0 for x in (predicted, residual_quantile)):
        raise ValueError("quality_invalid_interval_quantity")
    return max(0.0, predicted - residual_quantile), predicted + residual_quantile


class SegmentAccumulator:
    """Count every membership; never report partial metrics for missing predictions/bands."""

    def __init__(self, nominal: float) -> None:
        self.point = MetricAccumulator()
        self.nominal = nominal
        self.total = self.excluded = 0
        self.reasons: Counter[str] = Counter()
        self.squared = self.signed = self.under = self.over = self.mape = self.zero_excess = 0.0
        self.under_n = self.over_n = self.exact_n = self.positive_n = self.zero_n = 0
        self.interval_n = self.covered = 0
        self.width = 0.0

    def add(
        self,
        actual: int | None,
        predicted: float | None,
        reasons: tuple[str, ...],
        band: tuple[float, float] | None,
    ) -> None:
        self.total += 1
        if reasons:
            self.excluded += 1
            self.reasons.update(reasons)
            return
        if actual is None:
            raise ValueError("quality_eligible_label_missing")
        self.point.add(actual, predicted)
        if predicted is None:
            return
        error = predicted - actual
        self.squared += error * error
        self.signed += error
        self.under += max(0.0, -error)
        self.over += max(0.0, error)
        self.under_n += int(error < 0)
        self.over_n += int(error > 0)
        self.exact_n += int(error == 0)
        self.positive_n += int(actual > 0)
        self.zero_n += int(actual == 0)
        if actual > 0:
            self.mape += abs(error) / actual
        else:
            self.zero_excess += predicted
        if band is not None:
            lo, hi = band
            if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= predicted <= hi):
                raise ValueError("quality_interval_invalid_bounds")
            self.interval_n += 1
            self.covered += int(lo <= actual <= hi)
            self.width += hi - lo

    def result(
        self, fold: str, role: str, method: str, dimension: str, value: str
    ) -> SegmentMetric:
        point = self.point.result()
        n = point.eligible_rows
        complete = point.status == "passed"
        intervals = n > 0 and self.interval_n == n
        return SegmentMetric.model_validate(
            {
                "fold": fold,
                "role": role,
                "method": method,
                "dimension": dimension,
                "value": value,
                "total_rows": self.total,
                "excluded_rows": self.excluded,
                "exclusion_counts": dict(sorted(self.reasons.items())),
                "eligibility_coverage": n / self.total if self.total else None,
                "prediction_coverage": point.predicted_rows / n if n else None,
                "point": point.model_dump(mode="json"),
                "rmse": math.sqrt(self.squared / n) if complete else None,
                "bias": self.signed / n if complete else None,
                "normalized_bias": self.signed / self.point.actuals
                if complete and self.point.actuals
                else None,
                "underforecast_units": self.under if complete else None,
                "overforecast_units": self.over if complete else None,
                "underforecast_rows": self.under_n,
                "overforecast_rows": self.over_n,
                "exact_rows": self.exact_n,
                "positive_actual_rows": self.positive_n,
                "mape_positive_actuals": self.mape / self.positive_n
                if complete and self.positive_n
                else None,
                "mape_coverage": self.positive_n / n if complete else None,
                "zero_actual_rows": self.zero_n,
                "zero_actual_excess_forecast_units": self.zero_excess if complete else None,
                "interval": IntervalMetric(
                    eligible_rows=n,
                    interval_rows=self.interval_n,
                    status="passed" if intervals else "incomplete" if n else "not_evaluable",
                    nominal_coverage=self.nominal,
                    empirical_coverage=self.covered / n if intervals else None,
                    mean_width=self.width / n if intervals else None,
                ).model_dump(mode="json"),
            }
        )


def assess_segment(
    selected: SegmentMetric,
    baseline: SegmentMetric,
    policy: QualityPolicy,
    *,
    retained: bool,
) -> dict[str, Any]:
    """Evaluate a frozen choice; never select a replacement from holdout results."""
    if (
        any(
            getattr(selected, name) != getattr(baseline, name)
            for name in (
                "fold",
                "role",
                "dimension",
                "value",
                "total_rows",
                "excluded_rows",
                "exclusion_counts",
            )
        )
        or selected.point.eligible_rows != baseline.point.eligible_rows
    ):
        raise ValueError("quality_gate_common_coverage_mismatch")
    minimum = (
        policy.minimum_global_rows
        if selected.dimension == "global"
        else policy.minimum_segment_rows
    )
    unavailable: list[str] = []
    failed: list[str] = []
    n = selected.point.eligible_rows
    if n < minimum:
        unavailable.append("insufficient_sample")
    if selected.point.status != "passed" or baseline.point.status != "passed":
        unavailable.append("point_metrics_incomplete_or_empty")
    if selected.point.wape is None or baseline.point.wape is None:
        unavailable.append("zero_or_missing_actual_denominator")
    if selected.normalized_bias is None:
        unavailable.append("bias_not_evaluable")
    elif abs(selected.normalized_bias) > policy.maximum_absolute_normalized_bias:
        failed.append("absolute_normalized_bias_exceeded")
    if selected.prediction_coverage != policy.minimum_prediction_coverage:
        unavailable.append("prediction_coverage_incomplete")
    if selected.eligibility_coverage is None:
        unavailable.append("eligibility_coverage_not_evaluable")
    elif selected.eligibility_coverage < policy.minimum_eligibility_coverage:
        failed.append("eligibility_coverage_below_minimum")
    relative = None
    if selected.point.mae is not None and baseline.point.mae is not None:
        candidate_mae, baseline_mae = selected.point.mae, baseline.point.mae
        relative = (candidate_mae - baseline_mae) / baseline_mae if baseline_mae else None
        if selected.dimension == "global" and not retained:
            if baseline_mae == 0 or candidate_mae >= baseline_mae * (
                1 - policy.minimum_relative_improvement
            ):
                failed.append("global_mae_improvement_below_minimum")
        elif candidate_mae > baseline_mae * (
            1 + policy.maximum_critical_segment_relative_regression
        ):
            failed.append("critical_segment_mae_regression")
    band = selected.interval
    if band.status != "passed":
        unavailable.append("intervals_incomplete_or_uncalibrated")
    if (
        band.empirical_coverage is not None
        and band.empirical_coverage < policy.minimum_empirical_coverage
    ):
        failed.append("empirical_interval_coverage_below_minimum")
    width_ratio = None
    actual_sum = selected.point.absolute_actual_sum
    if band.mean_width is not None and n and actual_sum:
        width_ratio = band.mean_width / (actual_sum / n)
        if width_ratio > policy.maximum_mean_width_to_mean_actual:
            failed.append("interval_width_exceeded")
    else:
        unavailable.append("interval_width_ratio_not_evaluable")
    return {
        "fold": selected.fold,
        "role": selected.role,
        "dimension": selected.dimension,
        "value": selected.value,
        "selected_strategy": selected.method,
        "comparison_strategy": baseline.method,
        "baseline_retained": retained,
        "eligible_rows": n,
        "minimum_rows": minimum,
        "relative_mae_change": relative,
        "normalized_bias": selected.normalized_bias,
        "empirical_interval_coverage": band.empirical_coverage,
        "mean_interval_width_to_mean_actual": width_ratio,
        "status": "not_ready" if unavailable else "failed" if failed else "passed",
        "not_ready_reasons": unavailable,
        "failed_reasons": failed,
    }
