"""A sealed proposal exports portable mechanics, never an operational approval."""

from copy import deepcopy
from pathlib import Path

import pytest
from test_stockout_policy import development as development
from test_stockout_policy import parents as parents
from test_stockout_policy import sample as sample
from test_stockout_policy import setup
from test_stockout_policy import spec as spec

from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.bundle import build_proposal
from retailops_ai.stockout_runtime.export import scoring_artifacts


@pytest.fixture
def proposal(monkeypatch, sample, spec):
    _, qp, inputs = setup(monkeypatch, sample)
    return build_proposal(Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True)


def test_export_preserves_the_complete_pipeline_and_true_pins_without_approval(proposal):
    recipe, policy = scoring_artifacts(proposal)
    assert recipe.pin == policy.pin
    assert recipe.pipeline.model_dump(mode="json") == proposal["content"]["selected_pipeline"]
    assert policy.proposal_id == proposal["proposal_id"]
    assert policy.operational_approval == "requires_separate_lifecycle_approval"
    assert not proposal["content"]["serving_eligible"]
    assert scoring_artifacts(proposal) == (recipe, policy)


def test_modified_portable_coefficients_cannot_be_hidden_by_resealing_proposal(proposal):
    proposal = deepcopy(proposal)
    proposal["content"]["selected_pipeline"]["estimator"]["intercept"] += 0.1
    proposal["descriptor"]["content_sha256"] = digest(proposal["content"])
    proposal["proposal_id"] = "stockout-policy-proposal-sha256-" + digest(proposal["descriptor"])
    with pytest.raises(ValueError, match="model_calibrator"):
        scoring_artifacts(proposal)


@pytest.mark.parametrize(
    "field",
    ["thresholds_approved", "final_test_outcomes_evaluated", "model_promoted", "serving_eligible"],
)
def test_unapproved_export_cannot_approve_itself(proposal, field):
    proposal = deepcopy(proposal)
    proposal["content"][field] = True
    proposal["descriptor"]["content_sha256"] = digest(proposal["content"])
    proposal["proposal_id"] = "stockout-policy-proposal-sha256-" + digest(proposal["descriptor"])
    with pytest.raises(ValueError, match="approval_boundary"):
        scoring_artifacts(proposal)


def test_export_refuses_changed_implementation(proposal):
    proposal = deepcopy(proposal)
    proposal["descriptor"]["implementation"]["code_sha256"] = "0" * 64
    proposal["proposal_id"] = "stockout-policy-proposal-sha256-" + digest(proposal["descriptor"])
    with pytest.raises(ValueError, match="identity"):
        scoring_artifacts(proposal)
