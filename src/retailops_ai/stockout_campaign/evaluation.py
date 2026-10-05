"""Frozen predictions on authorized final rows; no fitting or post-outcome selection."""

from collections import defaultdict
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_campaign.assembly import FinalData, guard
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission, SourceRef
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_campaign.scenarios import REQUIRED, memberships
from retailops_ai.stockout_policy.assessment import assess, capacity_selection, confusion
from retailops_ai.stockout_policy.contract import PolicyRow
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline, SelectionModelPin
from retailops_ai.stockout_selection.gates import quality_gates
from retailops_ai.stockout_selection.pipeline import predict_conditional
from retailops_ai.stockout_training.contract import DEFAULT_POLICY
from retailops_ai.stockout_training.evaluation import metrics
from retailops_ai.stockout_training.pipeline import predict


def bound_recipes(freeze: CampaignFreeze, recipe: ScoringRecipe, policy: ScoringPolicy) -> None:
    recipe = ScoringRecipe.model_validate_json(recipe.model_dump_json())
    policy = ScoringPolicy.model_validate_json(policy.model_dump_json())
    if (
        not isinstance(recipe.pipeline, ConditionalRiskPipeline)
        or not isinstance(recipe.pin, SelectionModelPin)
        or recipe.pin != policy.pin
        or recipe.pin.selection_id != freeze.selection_id
        or canonical_sha256(recipe.model_dump(mode="json")) != freeze.recipe_content_sha256
        or canonical_sha256(policy.model_dump(mode="json")) != freeze.policy_content_sha256
        or recipe.pipeline.base.preprocessing.train_rows != freeze.train_rows
        or recipe.pipeline.calibrator.categories != freeze.expected_categories
        or recipe.pipeline.calibrator.stock_locations != freeze.expected_stock_locations
        or any(
            s.split_policy.calibration_until != recipe.pin.selection_known_at
            for s in freeze.sources
        )
    ):
        raise ValueError("stockout_final_recipe_policy_or_train_binding")


def evaluate_final(
    data: FinalData,
    *,
    freeze: CampaignFreeze,
    permission: CampaignPermission | None,
    source: SourceRef,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
) -> dict[str, Any]:
    guard(freeze, permission, source)
    bound_recipes(freeze, recipe, policy)
    if len(data.rows) != source.eligible_test_membership or len(data.rows) != len(data.outcomes):
        raise ValueError("stockout_final_complete_rows_required")
    if not isinstance(recipe.pipeline, ConditionalRiskPipeline) or not isinstance(
        recipe.pin, SelectionModelPin
    ):
        raise ValueError("stockout_final_conditional_recipe_required")
    probabilities = [float(p) for p in predict_conditional(recipe.pipeline, data.rows)]
    raw_probabilities = [
        float(p) for p in predict(recipe.pipeline.base, data.rows, calibrated=False)
    ]
    policy_rows = [
        PolicyRow.model_validate_json(
            canonical_bytes(
                dict(
                    product_id=r["product_id"],
                    stock_location_id=r["stock_location_id"],
                    as_of=r["as_of"],
                    category_id=r["category_id"],
                    probability=p,
                    incident_stockout=y,
                    historical_inventory_constraint=r["values"]["history_constrained_days"] > 0,
                )
            )
        )
        for r, y, p in zip(data.rows, data.outcomes, probabilities, strict=True)
    ]
    selected, origins = capacity_selection(policy_rows, policy.spec.capacity)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(data.rows):
        groups["all"].append(i)
        groups["category:" + row["category_id"]].append(i)
        groups["stock_location:" + row["stock_location_id"]].append(i)
        groups[
            "historical_inventory_constraint:"
            + str(row["values"]["history_constrained_days"] > 0).lower()
        ].append(i)
    scenarios, overlaps = memberships(data.rows, world=source.world, tables=data.tables)
    groups.update({"scenario:" + n: indices for n, indices in scenarios.items()})
    measurements: dict[str, dict[str, Any]] = {}
    raw_measurements: dict[str, dict[str, Any]] = {}
    prevalence = freeze.train_positives / freeze.train_rows
    for name, indices in sorted(groups.items()):
        if not indices:
            measurements[name] = dict(
                rows=0,
                positives=0,
                negatives=0,
                status="not_evaluable",
                reason="no_eligible_scenario_rows",
            )
            continue
        rows = [data.rows[i] for i in indices]
        targets = [data.outcomes[i] for i in indices]
        for output, predictions in (
            (measurements, probabilities),
            (raw_measurements, raw_probabilities),
        ):
            result = metrics(
                rows,
                targets,
                [predictions[i] for i in indices],
                DEFAULT_POLICY,
                train_prevalence=prevalence,
            )
            result.pop("capacity")
            result.pop("threshold_costs")
            output[name] = result
        measurements[name]["capacity_from_same_global_queue"] = confusion(
            [policy_rows[i] for i in indices], selected, policy.spec.costs
        )
    categories = set(recipe.pipeline.calibrator.categories)
    stocks = set(recipe.pipeline.calibrator.stock_locations)
    segment_gates = quality_gates(
        measurements,
        expected_categories=categories,
        expected_locations=stocks,
        policy=freeze.quality_requirements,
    )
    scenario_gates: dict[str, Any] = {}
    if source.world == "future_stress":
        from retailops_ai.stockout_qualification.fit import calibration_error

        for name in REQUIRED:
            result = measurements["scenario:" + name]
            enough = result["rows"] >= 20 and min(result["positives"], result["negatives"]) >= 5
            checks = dict(support=enough)
            if enough:
                checks.update(
                    AP_above_no_skill=result["average_precision"] > result["prevalence"],
                    brier_better_than_train_constant=result["brier"]
                    < result["train_prevalence_constant_brier"],
                    calibration_error_in_budget=calibration_error(result) <= 0.15,
                )
            scenario_gates[name] = dict(
                status="not_evaluable"
                if not enough
                else "passed"
                if all(checks.values())
                else "failed",
                checks=checks,
            )
        for name in ("promotion", "demand_shock", "inventory_constraint"):
            result = measurements["scenario:control:" + name]
            scenario_gates["control:" + name] = dict(
                status="passed" if result["rows"] >= 20 else "not_evaluable",
                rows=result["rows"],
                interpretation="coverage_control_no_causal_effect_claim",
            )
    # These are final-specific receipts of the same equations; the development
    # helper's historical flags are not presented as final receipts.
    segment_gates = {
        k: segment_gates[k] for k in ("status", "segments", "required_segment_universe_complete")
    }
    segment_gates["data_role"] = "authorized_final_test"
    accepted = segment_gates["status"] == "passed" and all(
        g["status"] == "passed" for g in scenario_gates.values()
    )
    decision = assess(policy_rows, policy.spec)
    decision.update(thresholds_approved=True, final_test_outcomes_evaluated=True)
    return dict(
        schema_version="stockout-final-world-report-1.0.0",
        campaign_id=freeze.campaign_id,
        world=source.world,
        seed=source.seed,
        source=source.model_dump(mode="json"),
        execution_code_sha256=code_digest(),
        dependency_lock_sha256=lock_digest(),
        permission_sha256=canonical_sha256(permission.model_dump(mode="json"))
        if permission
        else None,
        prediction_sha256=canonical_sha256(
            [
                [r["product_id"], r["stock_location_id"], r["as_of"], p]
                for r, p in zip(data.rows, probabilities, strict=True)
            ]
        ),
        model_id=recipe.pin.model_id,
        calibrator_sha256=recipe.pin.calibrator_sha256,
        selection_id=recipe.pin.selection_id,
        recipe_content_sha256=freeze.recipe_content_sha256,
        policy_content_sha256=freeze.policy_content_sha256,
        coverage=data.coverage,
        targets_lineage_sha256=data.lineage_sha256,
        parent_seals_sha256=data.parent_seals_sha256,
        metrics=measurements,
        raw_uncalibrated_comparison=raw_measurements,
        scenario_overlap_counts=overlaps,
        scenario_gates=scenario_gates,
        segment_gates=segment_gates,
        decision=decision,
        global_capacity_origins=origins,
        status="passed" if accepted else "not_ready",
        independent_quality_accepted=accepted,
        final_test_outcomes_evaluated=True,
        model_refits=0,
        recalibration=False,
        thresholds_changed=False,
        source_generation=False,
        model_promoted=False,
        ai08_ready=False,
        limits=["synthetic_source_worlds", "no_causal_effect_or_real_money_savings_claim"],
    )
