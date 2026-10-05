"""Prepare a reviewable six-world freeze from unopened, checksummed public receipts."""

from pathlib import Path
from typing import Any, Literal

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import file_hash, read_json
from retailops_ai.stockout.split import SplitPolicy
from retailops_ai.stockout_campaign.archive import parents
from retailops_ai.stockout_campaign.contract import (
    BalancedQualityRequirements,
    CampaignFreeze,
    SourceRef,
)
from retailops_ai.stockout_campaign.evaluation import bound_recipes
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline, QualityRequirements


def source_ref(public: Path, profile: Path, *, world: str, artifact: dict[str, Any]) -> SourceRef:
    checkpoint = read_json(public, "checkpoint.json")
    resource = read_json(public, "resource.json")
    plan = read_json(public, "plan.json")
    configured = read_json(profile.parent, profile.name)
    p = parents(checkpoint)
    size, checksum = file_hash(public, "resource.json")
    expected = checkpoint["public_files"]["resource.json"]
    if (
        expected != dict(bytes=size, sha256=checksum)
        or checksum != checkpoint["resource_receipt_sha256"]
        or plan != checkpoint["plan"]
        or file_hash(profile.parent, profile.name)[1] != plan["profile_sha256"]
        or resource["status"] != "passed"
        or resource["failure"] is not None
        or any(
            resource[k] is not False
            for k in (
                "final_test_outcomes_evaluated",
                "independent_quality_accepted",
                "model_promoted",
                "ai08_ready",
            )
        )
        or resource["producer"]["effective_config"]["seed"] != configured["generation"]["seed"]
        or plan["producer_commit"] != configured["producer_commit"]
        or plan["consumer_commit"] != configured["consumer_baseline_commit"]
    ):
        raise ValueError("stockout_final_public_receipt_or_profile_changed")
    if world == "future_stress" and resource["source_preparation_passed"] is not True:
        raise ValueError("stockout_final_source_preparation_not_passed")
    if world == "matching" and resource["whole_pilot_budget_passed"] is not True:
        raise ValueError("stockout_final_source_preparation_not_passed")
    consumer, producer = resource["consumer"], resource["producer"]
    curated_id = Path(p["parent_roots"]["curated"]).name
    ref = dict(
        world=world,
        seed=plan["seed"],
        workflow_run_id=artifact["workflow_run_id"],
        artifact_id=artifact["artifact_id"],
        artifact_bytes=artifact["artifact_bytes"],
        artifact_sha256=artifact["artifact_sha256"],
        checkpoint_sha256=file_hash(public, "checkpoint.json")[1],
        resource_sha256=checksum,
        producer_commit=plan["producer_commit"],
        consumer_commit=plan["consumer_commit"],
        source_dataset_id=producer["source_dataset_id"],
        qualification_id=producer["qualification_id"],
        curated_dataset_id=curated_id,
        eligible_test_membership=consumer["final_test_membership"],
        split_policy=SplitPolicy.model_validate(configured["split_policy"]).model_dump(mode="json"),
        **{
            k: consumer["ids"][k]
            for k in (
                "feature_bundle_id",
                "upstream_bundle_id",
                "label_bundle_id",
                "temporal_bundle_id",
            )
        },
    )
    return SourceRef.model_validate_json(canonical_bytes(ref))


def prepare_freeze(
    sources: tuple[SourceRef, ...],
    *,
    selection: dict[str, Any],
    recipe: ScoringRecipe,
    policy: ScoringPolicy,
    prepared_at: UtcTime,
    version: Literal["stockout-final-campaign-1.0.0", "stockout-final-campaign-2.0.0"] = (
        "stockout-final-campaign-1.0.0"
    ),
) -> CampaignFreeze:
    if not isinstance(recipe.pipeline, ConditionalRiskPipeline):
        raise ValueError("stockout_final_conditional_pipeline_required")
    selected = selection["selection"]
    descriptor, content = selected["descriptor"], selected["content"]
    if (
        canonical_sha256(content) != descriptor["content_sha256"]
        or selected["selection_id"] != "stockout-selection-sha256-" + canonical_sha256(descriptor)
        or content["selected_pipeline"] != recipe.pipeline.model_dump(mode="json")
        or content["selected_model_id"] != recipe.pin.model_id
        or content["selected_calibrator_sha256"] != recipe.pin.calibrator_sha256
        or any(
            selection[k] is not False
            for k in (
                "final_test_outcomes_evaluated",
                "independent_quality_accepted",
                "model_promoted",
                "ai08_ready",
            )
        )
        or content["roles"]["base_train"]["rows"] != 1305
        or content["roles"]["base_train"]["positives"] != 550
    ):
        raise ValueError("stockout_final_selection_capsule_changed")
    raw = dict(
        version=version,
        quality_requirements=BalancedQualityRequirements()
        if version == "stockout-final-campaign-2.0.0"
        else QualityRequirements(),
        prepared_at=prepared_at.isoformat(),
        sources=[s.model_dump(mode="json") for s in sources],
        selection_id=selected["selection_id"],
        selection_content_sha256=descriptor["content_sha256"],
        recipe_content_sha256=canonical_sha256(recipe.model_dump(mode="json")),
        policy_content_sha256=canonical_sha256(policy.model_dump(mode="json")),
        expected_categories=recipe.pipeline.calibrator.categories,
        expected_stock_locations=recipe.pipeline.calibrator.stock_locations,
        evaluator_code_sha256=code_digest(),
        dependency_lock_sha256=lock_digest(),
    )
    # Defaults are part of the identity; create then strictly validate the complete payload.
    raw["campaign_id"] = "stockout-final-campaign-sha256-" + "0" * 64
    # model_construct does not convert nested source refs; construct the typed values here.
    proposed = CampaignFreeze.model_construct(
        **{**raw, "prepared_at": prepared_at, "sources": sources}
    )
    complete = proposed.model_dump(mode="json")
    complete["campaign_id"] = "stockout-final-campaign-sha256-" + canonical_sha256(
        {k: v for k, v in complete.items() if k != "campaign_id"}
    )
    freeze = CampaignFreeze.model_validate_json(canonical_bytes(complete))
    bound_recipes(freeze, recipe, policy)
    return freeze
