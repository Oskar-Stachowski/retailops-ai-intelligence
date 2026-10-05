"""The later-calibrated model has distinct development policy and portable pins."""

import json
from copy import deepcopy

import pytest
from test_stockout_qualification import development as development
from test_stockout_qualification import parents as parents
from test_stockout_qualification import sample as sample

from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy import selection
from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    OperatorCapacity,
    PolicySpec,
    RiskThresholds,
)
from retailops_ai.stockout_runtime.contracts import ScoringRecipe
from retailops_ai.stockout_runtime.export import scoring_artifacts
from retailops_ai.stockout_selection.contract import (
    ConditionalRiskPipeline,
    QualityRequirements,
    SelectionPolicy,
)
from retailops_ai.stockout_selection.pipeline import predict_conditional
from retailops_ai.stockout_temporal_storage.store import PartitionInputs


def spec():
    return PolicySpec(
        version="stockout-threshold-proposal-2.0.0",
        evaluation_role="calibration",
        thresholds=RiskThresholds(medium_from=0.25, high_from=0.5, critical_from=0.9),
        capacity=OperatorCapacity(mode="top_fraction", fraction=0.2),
        costs=AttentionCosts(false_attention=1.0, missed_incident=5.0),
    )


@pytest.fixture
def proposed(sample, tmp_path, monkeypatch):
    data, members, _ = sample
    monkeypatch.setattr(selection, "capture", lambda *a: {"seal": "constant"})
    monkeypatch.setattr(selection, "assemble_partitioned_development", lambda *a, **kw: data)
    monkeypatch.setattr(
        selection, "read_json", lambda *a: dict(temporal_bundle_id="temporal-parent")
    )
    monkeypatch.setattr(selection, "membership", lambda *a: members)
    monkeypatch.setattr(selection, "development_source_parameters", lambda *a: dict(seed=42))
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    proposal = selection.build_selected_proposal(
        tmp_path / "temporal",
        inputs,
        SelectionPolicy(requirements=QualityRequirements(expected_category_count=2)),
        spec(),
        allow_evaluation_truth=True,
    )
    return proposal, data


def test_selected_proposal_uses_CAL_and_export_preserves_all_fitted_state(proposed):
    proposal, data = proposed
    c = proposal["content"]
    assert c["development_rows"] == len(data.rows["calibration"])
    assert c["metrics_interpretation"] == "development_selection_not_independent_quality"
    assert not c["independent_quality_accepted"] and not c["thresholds_approved"]
    recipe, policy = scoring_artifacts(proposal)
    assert isinstance(recipe.pipeline, ConditionalRiskPipeline)
    assert recipe.version.endswith("2.0.0") and policy.version.endswith("2.0.0")
    assert recipe.pin == policy.pin
    assert recipe.pipeline.model_dump(mode="json") == c["selected_pipeline"]
    assert predict_conditional(recipe.pipeline, data.rows["calibration"]).tolist()


@pytest.mark.parametrize(
    "change", ["model", "calibrator", "selection", "approve", "implementation", "protocol"]
)
def test_resealed_model_policy_changes_are_rejected(proposed, change):
    proposal, _ = proposed
    altered = deepcopy(proposal)
    descriptor, content = altered["descriptor"], altered["content"]
    if change == "model":
        content["selected_pipeline"]["base"]["estimator"]["intercept"] += 1
    elif change == "calibrator":
        content["selected_pipeline"]["calibrator"]["offset_weights"][0] += 1
    elif change == "selection":
        descriptor["model_pin"]["selection_known_at"] = "2026-06-01T00:00:00Z"
    elif change == "approve":
        content["thresholds_approved"] = True
    elif change == "implementation":
        descriptor["implementation"]["code_sha256"] = "1" * 64
    else:
        descriptor["schema_version"] = "1.0.0"
    descriptor["content_sha256"] = digest(content)
    altered["proposal_id"] = "stockout-policy-proposal-sha256-" + digest(descriptor)
    with pytest.raises(ValueError):
        scoring_artifacts(altered)


def test_portable_scoring_cannot_relabel_new_protocol_as_legacy(proposed):
    proposal, _ = proposed
    recipe, _ = scoring_artifacts(proposal)
    raw = recipe.model_dump(mode="json")
    raw["version"] = "stockout-portable-scoring-1.0.0"
    with pytest.raises(ValueError):
        ScoringRecipe.model_validate_json(json.dumps(raw))


def test_wrong_development_role_and_implicit_legacy_policy_are_rejected():
    raw = spec().model_dump(mode="json")
    raw["evaluation_role"] = "tune"
    with pytest.raises(ValueError):
        PolicySpec.model_validate_json(json.dumps(raw))


def test_opt_in_required_before_reading_selected_policy_parents(monkeypatch, tmp_path):
    monkeypatch.setattr(selection, "capture", lambda *a: pytest.fail("read before opt-in"))
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    with pytest.raises(ValueError, match="opt_in_required"):
        selection.build_selected_proposal(tmp_path, inputs, SelectionPolicy(), spec())


def test_selected_policy_cli_refuses_before_reading_policy_or_private_data(
    monkeypatch, tmp_path, capsys
):
    import sys

    from retailops_ai.stockout_policy import selection_cli

    args = ["selected-policy", "build"]
    for name in (
        "curated",
        "private",
        "features",
        "upstream",
        "labels",
        "temporal",
        "selection-policy",
        "operator-proposal",
        "output",
    ):
        args.extend(["--" + name, str(tmp_path / name)])
    monkeypatch.setattr(sys, "argv", args)
    monkeypatch.setattr(selection_cli, "read_bounded", lambda *a: pytest.fail("read before opt-in"))
    assert selection_cli.main() == 2
    assert "invalid_stockout_policy" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()
