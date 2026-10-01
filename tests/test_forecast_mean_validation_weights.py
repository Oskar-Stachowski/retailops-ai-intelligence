"""Development selection cannot replace independent qualification."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_forecast_functional_v12_recipe import fixture

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.functional_v12_recipe import (
    FunctionalV12Policy,
    fit_recipe_v12,
    predict_pair_v12,
)
from retailops_ai.forecasting.mean_validation_weights import (
    FrozenMeanWeights,
    MeanWeight,
    select_weight,
    validation_payload,
)


def weights(weight=0.5, **changes):
    return FrozenMeanWeights(
        development_campaign_id="functional-v12-campaign-sha256-" + "a" * 64,
        selector_receipt_sha256="b" * 64,
        standard_validation_receipt_sha256="c" * 64,
        weights=(MeanWeight(fold="test", volume="low", category="one", weight=weight),),
        **changes,
    )


def test_smaller_correction_avoids_later_validation_overshoot():
    bins = {10.0: [100, 1100, 12100]}
    decision = select_weight(bins, bins, 2.0, 1.0)
    assert decision["selected_weight"] == 0.5
    selected = next(row for row in decision["candidates"] if row["weight"] == 0.5)
    assert selected["selection"]["mse"] == 0.0
    assert selected["full_validation"]["mse"] < decision["baseline_full_validation"]["mse"]


def test_zero_sales_do_not_get_a_fictitious_ratio_or_positive_false_alarm():
    bins = {0.0: [100, 0, 0]}
    decision = select_weight(bins, bins, 1.0, 1.0)
    assert decision["selected_weight"] == 0.0
    assert decision["baseline_selection"]["normalized_bias"] is None
    assert all(not row["feasible"] for row in decision["candidates"] if row["weight"] > 0)


def test_mse_nonregression_alone_does_not_satisfy_bias():
    bins = {5.0: [100, 1000, 10000]}
    decision = select_weight(bins, bins, 10.0, 10.0)
    assert decision["selected_weight"] == 0.5
    full = next(row for row in decision["candidates"] if row["weight"] == 1.0)
    assert full["selection"]["mse"] == decision["baseline_selection"]["mse"]
    assert full["feasible"] is False


def test_clipping_ties_choose_the_smallest_adjustment():
    bins = {1.0: [100, 0, 0]}
    assert select_weight(bins, bins, -2.0, -2.0)["selected_weight"] == 0.5


def test_missing_validation_selection_does_not_qualify():
    bins = {10.0: [100, 1100, 12100]}
    assert select_weight({}, bins, 2.0, 1.0)["selected_weight"] is None


def test_role_filter_does_not_access_held_payload():
    class PoisonHeldRecord(dict):
        def __getitem__(self, key):
            if key != "role":
                raise AssertionError("held payload accessed")
            return "development_holdout"

    assert validation_payload(PoisonHeldRecord()) is None


def test_v12_recipe_and_prediction_bytes_are_unchanged():
    fold, rows = fixture()
    rows = [replace(row, actual=row.actual + 2) for row in rows]
    recipe = fit_recipe_v12(rows, fold)
    assert recipe["recipe_id"] == (
        "functional-v12-recipe-sha256-"
        "677b23453452b75bb100be38093f0374f83aaa6817a36d3ff2225b702fae00d6"
    )
    assert "mean_weights" not in recipe["policy"]
    prediction = predict_pair_v12(rows[0], recipe)
    serialized = [
        value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        for value in prediction
    ]
    assert canonical_sha256(serialized) == (
        "0db010989d1f1e1b5aafeb40dce03634e3c7c05696a20379428204c72576a9b8"
    )


def test_frozen_weight_changes_only_mean_and_ignores_prediction_labels():
    fold, rows = fixture()
    rows = [replace(row, actual=row.actual + 2) for row in rows]
    policy = FunctionalV12Policy(version="forecast-functional-recipe-3.0.0", mean_weights=weights())
    recipe = fit_recipe_v12(rows, fold, policy)
    candidate, reference, _ = predict_pair_v12(
        replace(rows[0], actual=None, available=None), recipe
    )
    assert reference.mean == 2.0
    assert candidate.mean == 3.0
    assert candidate.median == reference.median
    assert candidate.interval == reference.interval
    unknown, unknown_reference, _ = predict_pair_v12(replace(rows[0], category="new"), recipe)
    assert unknown.mean == unknown_reference.mean


def test_zero_estimator_is_unchanged_by_ordinary_mean_weights():
    fold, rows = fixture("zero")
    old = fit_recipe_v12(rows, fold)
    new = fit_recipe_v12(
        rows,
        fold,
        FunctionalV12Policy(version="forecast-functional-recipe-3.0.0", mean_weights=weights(0.0)),
    )
    assert predict_pair_v12(rows[0], old)[:2] == predict_pair_v12(rows[0], new)[:2]


def test_version_provenance_and_grid_are_enforced():
    with pytest.raises(ValidationError, match="version_requires"):
        FunctionalV12Policy(mean_weights=weights())
    with pytest.raises(ValidationError, match="version_requires"):
        FunctionalV12Policy(version="forecast-functional-recipe-3.0.0")
    with pytest.raises(ValidationError, match="require_additive"):
        FunctionalV12Policy(
            version="forecast-functional-recipe-3.0.0",
            mean_weights=weights(),
            mean_variant="hgb_blend",
        )
    with pytest.raises(ValidationError):
        weights(holdout_labels_used=True)
    with pytest.raises(ValidationError, match="invalid_validation_mean_weight"):
        weights(0.6)
    with pytest.raises(ValidationError, match="reference_fallback"):
        MeanWeight(
            fold="test",
            volume="low",
            category="one",
            weight=0.5,
            selection_status="no_admissible_validation_weight",
        )


def test_checked_in_weights_preserve_all_groups_and_failed_inner_constraints():
    path = Path(__file__).resolve().parents[1] / (
        "contracts/forecast/v3/mean-weights.from-v12-validation.json"
    )
    policy = FrozenMeanWeights.model_validate_json(path.read_bytes())
    assert len(policy.weights) == 72
    assert len(policy.lookup()) == 72
    fallback = [row for row in policy.weights if row.selection_status != "selected"]
    assert len(fallback) == 2
    assert all(row.weight == 0.0 for row in fallback)
    assert policy.holdout_labels_used is False


def test_duplicate_weight_groups_are_rejected():
    row = MeanWeight(fold="test", volume="low", category="one", weight=0.0)
    with pytest.raises(ValidationError, match="unique_sorted_groups"):
        FrozenMeanWeights(
            development_campaign_id="functional-v12-campaign-sha256-" + "a" * 64,
            selector_receipt_sha256="b" * 64,
            standard_validation_receipt_sha256="c" * 64,
            weights=(row, row),
        )
