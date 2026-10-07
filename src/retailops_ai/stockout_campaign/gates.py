"""Final-only prospective small-category warnings; all other gates remain hard."""

from typing import Any

from retailops_ai.stockout_campaign.contract import BalancedQualityRequirements
from retailops_ai.stockout_selection.contract import QualityRequirements
from retailops_ai.stockout_selection.gates import quality_gates as strict_quality_gates


def quality_gates(
    metrics: dict[str, Any],
    *,
    expected_categories: set[str],
    expected_locations: set[str],
    policy: QualityRequirements,
) -> dict[str, Any]:
    result = strict_quality_gates(
        metrics,
        expected_categories=expected_categories,
        expected_locations=expected_locations,
        policy=policy,
    )
    if not isinstance(policy, BalancedQualityRequirements):
        return result
    for name, gate in result["segments"].items():
        if (
            name.startswith("category:")
            and gate["status"] == "failed"
            and gate["rows"] < policy.small_category_rows_lt
            and gate["checks"]["AP_above_no_skill"] is True
            and gate["checks"]["brier_better_than_train_constant"] is True
            and policy.maximum_expected_calibration_error
            < gate["expected_calibration_error"]
            <= policy.small_category_warning_maximum_ece
        ):
            gate.update(
                status="warning",
                reason="owner_approved_small_category_calibration_warning",
                warning_policy=policy.version,
                calibration_warning_budget=policy.small_category_warning_maximum_ece,
            )
    result["status"] = (
        "passed"
        if result["required_segment_universe_complete"]
        and all(g["status"] in {"passed", "warning"} for g in result["segments"].values())
        else "not_ready"
    )
    return result
