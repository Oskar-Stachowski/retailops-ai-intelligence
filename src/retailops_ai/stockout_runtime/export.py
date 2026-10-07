"""Export a replayed proposal for inference mechanics, without granting operational approval."""

from typing import Any

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.bundle import implementation
from retailops_ai.stockout_policy.contract import ModelPolicyPin, PolicySpec
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline, SelectionModelPin
from retailops_ai.stockout_training.contract import RiskPipeline


def scoring_artifacts(proposal: dict[str, Any]) -> tuple[ScoringRecipe, ScoringPolicy]:
    descriptor, content = proposal["descriptor"], proposal["content"]
    if (
        descriptor["schema_version"] not in {"1.0.0", "2.0.0"}
        or descriptor["role"] != "stockout_threshold_policy_proposal"
        or proposal["proposal_id"] != "stockout-policy-proposal-sha256-" + digest(descriptor)
        or descriptor["content_sha256"] != digest(content)
        or descriptor["implementation"] != implementation()
        or any(
            content[field] is not False
            for field in (
                "thresholds_approved",
                "final_test_outcomes_evaluated",
                "model_promoted",
                "serving_eligible",
            )
        )
    ):
        raise ValueError("stockout_runtime_proposal_identity_or_approval_boundary")
    conditional = descriptor["schema_version"] == "2.0.0"
    pin = (
        SelectionModelPin.model_validate_json(canonical_json(descriptor["model_pin"]))
        if conditional
        else ModelPolicyPin.model_validate_json(canonical_json(descriptor["model_pin"]))
    )
    recipe = ScoringRecipe(
        version="stockout-portable-scoring-2.0.0"
        if conditional
        else "stockout-portable-scoring-1.0.0",
        pin=pin,
        pipeline=(
            ConditionalRiskPipeline.model_validate_json(
                canonical_json(content["selected_pipeline"])
            )
            if conditional
            else RiskPipeline.model_validate_json(canonical_json(content["selected_pipeline"]))
        ),
    )
    body = dict(
        version="stockout-scoring-policy-2.0.0" if conditional else "stockout-scoring-policy-1.0.0",
        pin=pin.model_dump(mode="json"),
        proposal_id=proposal["proposal_id"],
        spec=PolicySpec.model_validate_json(canonical_json(descriptor["policy"])).model_dump(
            mode="json"
        ),
        operational_approval="requires_separate_lifecycle_approval",
    )
    policy = ScoringPolicy.model_validate_json(
        canonical_json(dict(policy_id="stockout-scoring-policy-sha256-" + digest(body), **body))
    )
    return recipe, policy
