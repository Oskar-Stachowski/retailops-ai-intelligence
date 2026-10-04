"""Same development quality requirements, with selection bias stated explicitly."""

from typing import Any

from retailops_ai.stockout_qualification.fit import calibration_error
from retailops_ai.stockout_selection.contract import QualityRequirements


def quality_gates(
    metrics: dict[str, Any],
    *,
    expected_categories: set[str],
    expected_locations: set[str],
    policy: QualityRequirements,
) -> dict[str, Any]:
    required = {
        "all",
        *("category:" + v for v in expected_categories),
        *("stock_location:" + v for v in expected_locations),
        "historical_inventory_constraint:true",
    }
    gates: dict[str, Any] = {}
    for segment in sorted(required):
        result = metrics.get(segment)
        if result is None:
            gates[segment] = dict(status="not_evaluable", reason="required_segment_missing")
            continue
        enough = (
            result["rows"] >= policy.minimum_segment_rows
            and min(result["positives"], result["negatives"]) >= policy.minimum_segment_per_class
        )
        if not enough or result["status"] == "not_evaluable":
            gates[segment] = dict(
                status="not_evaluable",
                reason="insufficient_both_class_segment_support",
                rows=result["rows"],
                positives=result["positives"],
                negatives=result["negatives"],
            )
            continue
        ece = calibration_error(result)
        checks = dict(
            AP_above_no_skill=result["average_precision"] > result["prevalence"],
            brier_better_than_train_constant=result["brier"]
            < result["train_prevalence_constant_brier"],
            calibration_error_in_budget=ece <= policy.maximum_expected_calibration_error,
        )
        gates[segment] = dict(
            status="passed" if all(checks.values()) else "failed",
            checks=checks,
            expected_calibration_error=ece,
            rows=result["rows"],
            positives=result["positives"],
            negatives=result["negatives"],
        )
    universe_complete = (
        len(expected_categories) == policy.expected_category_count
        and len(expected_locations) == policy.expected_stock_location_count
    )
    return dict(
        status="passed"
        if universe_complete and all(g["status"] == "passed" for g in gates.values())
        else "not_ready",
        segments=gates,
        required_segment_universe_complete=universe_complete,
        policy_status=policy.quality_policy_status,
        independent_quality_accepted=False,
        final_test_qualified=False,
    )
