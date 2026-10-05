"""A final card combines the frozen development choice and all independent world receipts."""

from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_explanation.diagnostics import coefficients
from retailops_ai.stockout_lifecycle.evidence import verify_execution_evidence
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline


def selection_content(
    selection: dict[str, Any], freeze: CampaignFreeze, recipe: ScoringRecipe
) -> dict[str, Any]:
    """Bind previously replayed development bytes; never fit or select using final outcomes."""
    document = selection["selection"]
    descriptor, content = document["descriptor"], document["content"]
    if (
        selection["schema_version"] != "stockout-selection-capsule-2.0.0"
        or selection["parents_full_replay"] is not True
        or any(
            selection[name] is not False
            for name in (
                "independent_quality_accepted",
                "final_test_outcomes_evaluated",
                "model_promoted",
                "ai08_ready",
            )
        )
        or document["selection_id"] != freeze.selection_id
        or document["selection_id"] != "stockout-selection-sha256-" + canonical_sha256(descriptor)
        or descriptor["content_sha256"] != canonical_sha256(content)
        or descriptor["content_sha256"] != freeze.selection_content_sha256
        or content["selected_pipeline"] != recipe.pipeline.model_dump(mode="json")
        or content["selected_model_id"] != recipe.pin.model_id
        or content["roles_disjoint"] is not True
        or any(
            content[name] is not False
            for name in (
                "independent_quality_accepted",
                "final_test_outcomes_evaluated",
                "model_promoted",
                "model_ready",
            )
        )
    ):
        raise ValueError("stockout_final_development_selection_changed")
    return dict(content)


def final_card(
    *,
    selection: dict[str, Any],
    freeze: CampaignFreeze,
    permission: CampaignPermission,
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    quality: dict[str, Any],
    execution: dict[str, Any],
    inputs: PreparedStockoutInputs,
) -> dict[str, Any]:
    """Failed quality remains an honest final card; only a separate qualifier can accept it."""
    bound_recipes(freeze, recipe, policy)
    verify_execution_evidence(execution, quality, freeze=freeze, permission=permission)
    content = selection_content(selection, freeze, recipe)
    if not isinstance(recipe.pipeline, ConditionalRiskPipeline):
        raise ValueError("stockout_final_card_requires_frozen_conditional_pipeline")
    lineage = inputs.lineage
    if (
        not any(
            (
                lineage.source_dataset_id,
                lineage.curated_dataset_id,
                lineage.feature_set_id,
                lineage.upstream_bundle_id,
            )
            == (
                s.source_dataset_id,
                s.curated_dataset_id,
                s.feature_bundle_id,
                s.upstream_bundle_id,
            )
            for s in freeze.sources
        )
        or inputs.as_of < recipe.pin.selection_known_at
    ):
        raise ValueError("stockout_final_card_smoke_source_or_chronology_changed")
    passed = quality["content"]["status"] == "passed"
    comparisons = {
        name: dict(
            base_model=row["base_model"],
            C=row["C"],
            status=row["development_selection_gates"]["status"],
            metrics=row["conditional"]["all"],
        )
        for name, row in sorted(content["comparison"].items())
    }
    card = dict(
        version="stockout-serving-model-card-2.0.0",
        recipe_content_sha256=freeze.recipe_content_sha256,
        policy_content_sha256=freeze.policy_content_sha256,
        quality_status="passed_independent_final_campaign"
        if passed
        else "not_ready_independent_final_campaign",
        final_campaign_id=freeze.campaign_id,
        final_quality_id=quality["quality_id"],
        execution_evidence_id=execution["evidence_id"],
        physical_key=["product_id", "stock_location_id", "as_of"],
        target="new_incident_stockout_in_(origin,origin+7_calendar_days]",
        stock_measure="available_qty_from_inventory_ledger",
        current_stockout="separate_state_probability_null",
        selection_id=freeze.selection_id,
        development_selection_content_sha256=freeze.selection_content_sha256,
        development_parents=selection["selection"]["descriptor"]["parents"],
        roles=content["roles"],
        fit_known_at=recipe.pipeline.base.model_dump(mode="json")["fit_known_at"],
        calibrator_known_at=recipe.pipeline.calibrator.model_dump(mode="json")["fit_known_at"],
        selection_known_at=recipe.pin.model_dump(mode="json")["selection_known_at"],
        selected=content["selected"],
        development_comparison=comparisons,
        development_metrics_interpretation="selection_diagnostics_not_independent_quality",
        rejected_candidates=content["rejected_candidates"],
        base_coefficients=coefficients(recipe.pipeline.base),
        complete_calibrator=recipe.pipeline.calibrator.model_dump(mode="json"),
        policy=policy.model_dump(mode="json"),
        worlds=[
            dict(
                world=r["world"],
                seed=r["seed"],
                source=r["source"],
                coverage=r["coverage"],
                metrics=r["metrics"],
                raw_uncalibrated_comparison=r["raw_uncalibrated_comparison"],
                segment_gates=r["segment_gates"],
                scenario_gates=r["scenario_gates"],
                scenario_overlap_counts=r["scenario_overlap_counts"],
                decision=r["decision"],
                status=r["status"],
            )
            for r in quality["content"]["worlds"]
        ],
        blockers=quality["content"]["blockers"],
        warnings=quality["content"]["warnings"],
        quality_requirements=freeze.quality_requirements.model_dump(mode="json"),
        small_category_warnings_approved=permission.small_category_warnings_approved,
        smoke_inputs_id=inputs.inputs_id,
        smoke_lineage=inputs.lineage.model_dump(mode="json"),
        model_refits=0,
        recalibration=False,
        thresholds_changed=False,
        model_promoted=False,
        ai08_ready=False,
        limitations=[
            "Synthetic source worlds do not establish generalization to a real retailer.",
            "Overlapping seven-day windows are not independent stockout episodes.",
            "Worlds are reported separately; repeated physical keys are never pooled.",
            "Coefficients and factual reason codes do not claim causal effects.",
            "Costs are illustrative units, not validated money savings.",
            "Capacity applies to one complete registered physical input profile at one origin.",
            "No global source watermark is supplied; serving freshness remains explicit.",
            "The final campaign permission does not authorize production promotion.",
        ],
    )
    card["card_id"] = "stockout-final-card-sha256-" + canonical_sha256(card)
    return card
