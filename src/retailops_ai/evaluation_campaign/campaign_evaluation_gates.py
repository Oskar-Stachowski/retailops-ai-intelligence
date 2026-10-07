"""Campaign numerical gates, differential-tested against the unchanged legacy v2 evaluator."""

from typing import Any

from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastQualityPolicy,
)


def assess_frozen_metrics(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    *,
    total_rows: int,
    eligible_rows: int,
    dimension: str,
    retained_median_baseline: bool,
    policy: CampaignForecastQualityPolicy,
) -> dict[str, Any]:
    """Only numerical gates; callers prove keys, eligibility and any retained-baseline equality."""
    failed: list[str] = []
    unavailable: list[str] = []
    minimum = policy.minimum_global_rows if dimension == "global" else policy.minimum_segment_rows
    if eligible_rows < minimum:
        unavailable.append("insufficient_sample")
    eligibility = eligible_rows / total_rows if total_rows else None
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
        "eligible_rows": eligible_rows,
        "total_rows": total_rows,
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
