"""Interpretation is development-only, reproducible and cannot bypass model replay."""

import json
import sys
from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError
from test_stockout_training import development as development
from test_stockout_training import parents as parents

from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_explanation import cli, diagnostics
from retailops_ai.stockout_explanation.card import build_card
from retailops_ai.stockout_explanation.diagnostics import (
    ExplanationPolicy,
    coefficients,
    factual_context,
    permutation_importance,
)
from retailops_ai.stockout_training.contract import DEFAULT_POLICY, RiskPipeline
from retailops_ai.stockout_training.development import build_development
from retailops_ai.stockout_training.inputs import assemble_development
from retailops_ai.stockout_training.pipeline import fit_model


def fitted(data, family="logistic_regression"):
    return fit_model(
        data.rows["train"],
        data.outcomes["train"],
        family=family,
        variant="without_upstream",
        fit_known_at=data.split_policy.train_until,
        policy=DEFAULT_POLICY,
    )


def test_coefficients_expose_transformed_scale_and_do_not_claim_probability_effect(development):
    model = fitted(development)
    table = coefficients(model)
    column = table["columns"][0]
    assert column["coefficient"] == model.estimator.weights[0]
    assert column["train_center"] == model.preprocessing.means[0]
    assert column["train_scale"] == model.preprocessing.scales[0]
    assert {c["representation"] for c in table["columns"]} == {
        "numeric",
        "missing_indicator",
        "category_indicator",
    }
    assert "not_causal" in table["interpretation"]
    with pytest.raises(ValueError, match="require_linear"):
        coefficients(fitted(development, "hist_gradient_boosting"))


def test_permutation_detects_signal_keeps_zero_importance_and_is_repeatable(development):
    model = fitted(development)
    before = deepcopy(development.rows)
    policy = ExplanationPolicy()
    result = permutation_importance(
        model, development.rows["tune"], development.outcomes["tune"], policy
    )
    assert result == permutation_importance(
        model, development.rows["tune"], development.outcomes["tune"], policy
    )
    assert result["baseline_AP"] == 1.0
    groups = {g["feature_group"]: g for g in result["groups"]}
    assert groups["available_qty"]["AP_drop_mean"] > 0.2
    assert groups["observed_sales_mean"]["AP_drop_mean"] == 0.0
    assert len(groups["available_qty"]["AP_drop_repeats"]) == 5
    assert development.rows == before


def test_permutation_moves_value_and_missing_or_whole_category_together(development, monkeypatch):
    train = deepcopy(development.rows["train"])
    tune = deepcopy(development.rows["tune"])
    for rows in (train, tune):
        for i, row in enumerate(rows):
            row["values"]["available_qty"] = None if i % 3 == 0 else 10 + i
            row["category_id"] = "a" if i % 2 else "b"
    model = fit_model(
        train,
        development.outcomes["train"],
        family="hist_gradient_boosting",
        variant="without_upstream",
        fit_known_at=development.split_policy.train_until,
        policy=DEFAULT_POLICY,
    )
    captured = []
    original = diagnostics.signed_scores

    def capture(estimator, matrix):
        captured.append(matrix.copy())
        return original(estimator, matrix)

    monkeypatch.setattr(diagnostics, "signed_scores", capture)
    permutation_importance(model, tune, development.outcomes["tune"], ExplanationPolicy())
    n = len(model.preprocessing.numeric_columns)
    for matrix in captured:
        assert np.all(matrix[matrix[:, n] == 1, 0] == model.preprocessing.medians[0])
        assert np.all(matrix[:, -2:].sum(axis=1) == 1)


@pytest.mark.parametrize("ys", [[0] * 22, [1] * 22, [0, 1]])
def test_permutation_rejects_one_class_or_mismatched_lengths(development, ys):
    with pytest.raises(ValueError, match="both_classes"):
        permutation_importance(
            fitted(development), development.rows["tune"], ys, ExplanationPolicy()
        )


def test_later_sigmoid_does_not_change_tune_importance(development):
    parent = build_development(development)
    name = parent["descriptor"]["selection"]["name"]
    model = RiskPipeline.model_validate_json(json.dumps(parent["pipelines"][name]))
    changed = RiskPipeline.model_validate({**model.model_dump(), "sigmoid": None})
    policy = ExplanationPolicy()
    assert permutation_importance(
        model, development.rows["tune"], development.outcomes["tune"], policy
    ) == permutation_importance(
        changed, development.rows["tune"], development.outcomes["tune"], policy
    )


def test_factual_context_is_origin_facts_without_outcomes_or_probability(parents):
    point = FeaturePoint.model_validate_json(json.dumps(parents[0]["points"][22]))
    result = factual_context(point)
    assert result["interpretation"] == "verified_PIT_context_not_local_model_attribution"
    assert not {"probability", "incident_stockout", "risk_band"} & set(result)
    assert not any(f["field"] == "next_expected_delivery_hours" for f in result["facts"])
    assert result["facts"][0]["value"] == point.values.available_qty


@pytest.mark.parametrize(
    "status,age", [("already_stockout", 0.0), ("insufficient_data", 0.0), ("eligible", 25.0)]
)
def test_context_rejects_existing_stockout_insufficient_or_stale(parents, status, age):
    raw = deepcopy(parents[0]["points"][22])
    raw.update(status=status, reason="unknown" if status == "insufficient_data" else None)
    if status == "already_stockout":
        raw["values"]["available_qty"] = 0
    raw["values"]["snapshot_age_hours"] = age
    with pytest.raises(ValueError, match="eligible_fresh"):
        factual_context(FeaturePoint.model_validate_json(json.dumps(raw)))


def test_card_exposes_honest_scope_and_only_tune_context(development, parents):
    parent = build_development(development)
    card = build_card(development, parent, parents[0])
    content = card["content"]
    assert len(content["coefficient_tables"]) == 3
    assert len(content["local_factual_context"]) == len(development.rows["tune"])
    assert content["coverage"]["final_test"] == {
        "eligible_membership_only": 2,
        "outcomes_evaluated": False,
    }
    assert not content["readiness"]["model_ready"]
    assert not content["readiness"]["final_test_outcomes_evaluated"]
    assert card["descriptor"]["parents"]["development_id"] == parent["development_id"]
    assert card == build_card(development, parent, parents[0])


def test_resealed_model_weights_do_not_bypass_card_parent_replay(development, parents):
    parent = build_development(development)
    name = "logistic_regression:without_upstream"
    parent["pipelines"][name]["estimator"]["weights"][0] += 1
    parent["descriptor"]["model_ids"][name] = "risk-model-sha256-" + digest(
        parent["pipelines"][name]
    )
    parent["descriptor"]["pipelines_sha256"] = digest(parent["pipelines"])
    parent["development_id"] = "development-sha256-" + digest(parent["descriptor"])
    with pytest.raises(ValueError, match="development_full_replay_mismatch"):
        build_card(development, parent, parents[0])


def test_final_outcomes_cannot_change_card(parents):
    before = assemble_development(*parents)
    original = build_card(before, build_development(before), parents[0])
    changed = deepcopy(parents)
    for point in changed[3]["points"][-2:]:
        point["incident_stockout"] = 1 - point["incident_stockout"]
        point["first_incident_at"] = point["window_end_at"] if point["incident_stockout"] else None
        point["first_incident_event_id"] = "test" if point["incident_stockout"] else None
    after = assemble_development(*changed)
    # Fake fixture parent IDs are fixed; real source IDs change with content, not with scoring.
    assert build_card(after, build_development(after), changed[0]) == original


@pytest.mark.parametrize(
    "change", [{"repeats": 10}, {"random_state": 137}, {"scoring": "final_test"}]
)
def test_explanation_recipe_is_frozen(change):
    with pytest.raises(ValidationError):
        ExplanationPolicy.model_validate(change)


def test_cli_requires_truth_opt_in_before_reading_inputs(monkeypatch, tmp_path, capsys):
    args = ["card", "build"]
    for name in (
        "curated",
        "features",
        "upstream",
        "comparison",
        "labels",
        "split",
        "source",
        "development",
        "output",
    ):
        args += ["--" + name, str(tmp_path / name)]
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    assert list(tmp_path.iterdir()) == []
