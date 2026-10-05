"""Compare owner-approved capacities on the exact frozen development role, without fitting."""

from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_campaign.contract import CampaignFreeze
from retailops_ai.stockout_lifecycle.card import selection_content
from retailops_ai.stockout_policy.assessment import assess
from retailops_ai.stockout_policy.contract import OperatorCapacity, PolicyRow
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline
from retailops_ai.stockout_selection.pipeline import predict_conditional
from retailops_ai.stockout_training.inputs import DevelopmentData, keys_sha256, labels_sha256


def compare_capacities(
    data: DevelopmentData,
    *,
    selection: dict[str, Any],
    freeze: CampaignFreeze,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
) -> dict[str, Any]:
    """20% is a reference; select 40% or 50% by cost, then workload, before final evaluation."""
    content = selection_content(selection, freeze, recipe)
    if (
        not isinstance(recipe.pipeline, ConditionalRiskPipeline)
        or recipe.pin != policy.pin
        or data.parents != selection["selection"]["descriptor"]["parents"]
        or set(data.rows) != {"train", "tune", "calibration"}
        or set(data.outcomes) != set(data.rows)
        or data.coverage["final_test"]["outcomes_evaluated"] is not False
    ):
        raise ValueError("stockout_capacity_exact_development_parents_required")
    for role, key in (
        ("train", "base_train"),
        ("tune", "calibration_fit"),
        ("calibration", "development_selection"),
    ):
        known = content["roles"][key]
        rows, outcomes = data.rows[role], data.outcomes[role]
        if (
            len(rows) != known["rows"]
            or sum(outcomes) != known["positives"]
            or keys_sha256(rows) != known["keys_sha256"]
            or labels_sha256(rows, outcomes) != known["outcomes_sha256"]
        ):
            raise ValueError("stockout_capacity_development_membership_changed")
    rows, outcomes = data.rows["calibration"], data.outcomes["calibration"]
    probabilities = predict_conditional(recipe.pipeline, rows)
    points = [
        PolicyRow.model_validate_json(
            canonical_bytes(
                dict(
                    product_id=row["product_id"],
                    stock_location_id=row["stock_location_id"],
                    as_of=row["as_of"],
                    category_id=row["category_id"],
                    probability=float(p),
                    incident_stockout=y,
                    historical_inventory_constraint=row["values"]["history_constrained_days"] > 0,
                )
            )
        )
        for row, y, p in zip(rows, outcomes, probabilities, strict=True)
    ]
    comparisons = {}
    specs = {}
    for fraction in (0.2, 0.4, 0.5):
        spec = policy.spec.model_copy(
            update={"capacity": OperatorCapacity(mode="top_fraction", fraction=fraction)}
        )
        comparisons[str(fraction)] = assess(points, spec)
        specs[str(fraction)] = spec
    selected = min(
        ("0.4", "0.5"),
        key=lambda value: (
            comparisons[value]["capacity"]["cost"],
            comparisons[value]["capacity"]["selected"],
            float(value),
        ),
    )
    report = dict(
        version="stockout-capacity-development-comparison-2.0.0",
        selection_id=freeze.selection_id,
        model_id=recipe.pin.model_id,
        calibrator_sha256=recipe.pin.calibrator_sha256,
        parents=data.parents,
        development_role="calibration",
        development_rows=len(rows),
        development_keys_sha256=keys_sha256(rows),
        development_labels_sha256=labels_sha256(rows, outcomes),
        comparisons=comparisons,
        selected_fraction=float(selected),
        selection_rule="minimum_FP1_FN5_cost_among_40_50_then_lower_workload_then_fraction",
        threshold_comparison_interpretation="probability_threshold_is_not_a_top_fraction_budget",
        model_refits=0,
        recalibration=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
    )
    descriptor = dict(
        version=report["version"],
        original_policy_id=policy.policy_id,
        selected_spec=specs[selected].model_dump(mode="json"),
        comparison_content_sha256=canonical_sha256(report),
    )
    raw = policy.model_dump(mode="json", exclude={"policy_id"})
    raw["proposal_id"] = "stockout-policy-proposal-sha256-" + canonical_sha256(descriptor)
    raw["spec"] = descriptor["selected_spec"]
    raw["policy_id"] = "stockout-scoring-policy-sha256-" + canonical_sha256(raw)
    chosen = ScoringPolicy.model_validate_json(canonical_bytes(raw))
    return dict(
        descriptor=descriptor, content=report, scoring_policy=chosen.model_dump(mode="json")
    )
