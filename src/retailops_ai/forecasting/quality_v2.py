"""Small pure evaluator for protocol v2; never refits, selects, or rewrites v1 reports."""

import math
from collections.abc import Sequence
from typing import Any, Literal, cast

from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    ProtocolObservation,
    QualityPolicyV2,
)


def interval_score(actual: int, band: CentralInterval, nominal: float = 0.9) -> float:
    """Central interval score: width plus proper penalties for both missed tails."""
    if actual < 0 or not 0 < nominal < 1:
        raise ValueError("invalid_interval_score_input")
    return (
        band.upper
        - band.lower
        + 2 / (1 - nominal) * (max(band.lower - actual, 0.0) + max(actual - band.upper, 0.0))
    )


def _metrics(rows: Sequence[ProtocolObservation], side: str, nominal: float) -> dict[str, Any]:
    n = len(rows)
    forecasts = [getattr(row, side) for row in rows]
    actuals = [cast(int, row.actual) for row in rows]
    total = sum(actuals)
    result: dict[str, Any] = {"rows": n, "actual_sum": total}
    for name in ("median", "mean"):
        complete = n > 0 and all(getattr(f, name) is not None for f in forecasts)
        errors = (
            [getattr(f, name) - y for f, y in zip(forecasts, actuals, strict=True)]
            if complete
            else []
        )
        signed = math.fsum(errors)
        absolute = math.fsum(abs(e) for e in errors)
        result[name] = {
            "complete": complete,
            "mae": absolute / n if complete else None,
            "mse": math.fsum(e * e for e in errors) / n if complete else None,
            "bias_units": signed / n if complete else None,
            "normalized_bias": signed / total if complete and total else None,
            "wape": absolute / total if complete and total else None,
            "zero_actual_excess_units": math.fsum(
                getattr(f, name) for f, y in zip(forecasts, actuals, strict=True) if y == 0
            )
            if complete
            else None,
        }
    bands = [f.interval for f in forecasts]
    complete = n > 0 and all(band is not None for band in bands)
    width = math.fsum(b.upper - b.lower for b in bands) / n if complete else None
    result["interval"] = {
        "complete": complete,
        "mean_width": width,
        "width_to_mean_actual": width / (total / n) if width is not None and total else None,
        "mean_score": math.fsum(
            interval_score(y, b, nominal) for y, b in zip(actuals, bands, strict=True)
        )
        / n
        if complete
        else None,
        "coverage": sum(b.lower <= y <= b.upper for y, b in zip(actuals, bands, strict=True)) / n
        if complete
        else None,
    }
    return result


def assess_segment_v2(
    observations: Sequence[ProtocolObservation],
    *,
    dimension: Literal["global", "horizon", "category", "channel", "volume"],
    retained_median_baseline: bool = False,
    policy: QualityPolicyV2 | None = None,
) -> dict[str, Any]:
    """Evaluate one already frozen group on identical keys, preserving all measurable failures.

    The campaign must separately verify group inventory, as-of cutoffs, calibration sample,
    and the frozen baseline/forecast receipts before calling this component.
    """
    policy = QualityPolicyV2() if policy is None else policy
    if dimension not in ("global", "horizon", "category", "channel", "volume"):
        raise ValueError("unknown_quality_dimension")
    if len(observations) > 1000000:
        raise ValueError("quality_v2_segment_row_budget")
    if len({row.key for row in observations}) != len(observations):
        raise ValueError("duplicate_quality_key")
    rows = [row for row in observations if not row.exclusion_reasons]
    if retained_median_baseline and any(
        row.candidate.median != row.baseline.median for row in rows
    ):
        raise ValueError("retained_baseline_predictions_differ")
    candidate = _metrics(rows, "candidate", policy.nominal_coverage)
    baseline = _metrics(rows, "baseline", policy.nominal_coverage)
    failed: list[str] = []
    unavailable: list[str] = []
    minimum = policy.minimum_global_rows if dimension == "global" else policy.minimum_segment_rows
    if len(rows) < minimum:
        unavailable.append("insufficient_sample")
    eligibility = len(rows) / len(observations) if observations else None
    if eligibility is None:
        unavailable.append("eligibility_coverage_not_evaluable")
    elif eligibility < policy.minimum_eligibility_coverage:
        failed.append("eligibility_coverage_below_minimum")
    for component in ("median", "mean", "interval"):
        if not candidate[component]["complete"] or not baseline[component]["complete"]:
            unavailable.append(component + "_predictions_incomplete_or_empty")

    mae, reference = candidate["median"]["mae"], baseline["median"]["mae"]
    relative = (mae - reference) / reference if mae is not None and reference else None
    if mae is not None and reference is not None:
        if reference == 0:
            if mae > 0:
                failed.append("median_mae_regression_from_perfect_baseline")
        elif dimension == "global" and not retained_median_baseline:
            if mae >= reference * (1 - policy.minimum_relative_mae_improvement):
                failed.append("global_median_mae_improvement_below_minimum")
        elif mae > reference * (1 + policy.maximum_segment_mae_regression):
            failed.append("critical_segment_median_mae_regression")

    mse, reference_mse = candidate["mean"]["mse"], baseline["mean"]["mse"]
    if mse is not None and reference_mse is not None and mse > reference_mse:
        failed.append("mean_mse_regression")
    bias = candidate["mean"]["normalized_bias"]
    if bias is not None:
        if abs(bias) > policy.maximum_absolute_normalized_mean_bias:
            failed.append("absolute_normalized_mean_bias_exceeded")
    elif candidate["mean"]["complete"] and candidate["mean"]["zero_actual_excess_units"] > 0:
        failed.append("positive_mean_forecast_on_all_zero_actuals")

    band, reference_band = candidate["interval"], baseline["interval"]
    if band["coverage"] is not None and band["coverage"] < policy.minimum_empirical_coverage:
        failed.append("empirical_interval_coverage_below_minimum")
    if (
        band["mean_score"] is not None
        and reference_band["mean_score"] is not None
        and band["mean_score"] > reference_band["mean_score"]
    ):
        failed.append("interval_score_regression")
    ratio = band["width_to_mean_actual"]
    mean_mae, baseline_mean_mae = candidate["mean"]["mae"], baseline["mean"]["mae"]
    median_bias = candidate["median"]["normalized_bias"]
    return {
        "protocol_version": policy.version,
        "scope": "segment_component_not_campaign_qualification",
        "dimension": dimension,
        "eligible_rows": len(rows),
        "total_rows": len(observations),
        "eligibility_coverage": eligibility,
        "candidate": candidate,
        "baseline": baseline,
        "relative_median_mae_change": relative,
        "perfect_median_baseline_tied": reference == 0 and mae == 0,
        "diagnostics": {
            "mean_mae_above_legacy_regression_limit": mean_mae
            > baseline_mean_mae * (1 + policy.maximum_segment_mae_regression)
            if mean_mae is not None and baseline_mean_mae is not None
            else None,
            "median_bias_above_legacy_mean_limit": abs(median_bias)
            > policy.maximum_absolute_normalized_mean_bias
            if median_bias is not None
            else None,
        },
        "legacy_width_ratio_exceeded": ratio > policy.legacy_width_ratio_threshold
        if ratio is not None
        else None,
        "status": "not_ready" if unavailable else "failed" if failed else "passed",
        "has_measurable_failures": bool(failed),
        "not_ready_reasons": unavailable,
        "failed_reasons": failed,
    }
