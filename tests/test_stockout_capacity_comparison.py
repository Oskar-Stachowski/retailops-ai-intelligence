"""Capacity choice uses bound development outcomes and cannot refit or include TEST."""

from copy import deepcopy

import pytest
import test_stockout_final_campaign as campaign_fixtures
from test_stockout_final_campaign import seal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout.feature_contract import FeatureValues
from retailops_ai.stockout_policy.comparison import compare_capacities
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_training.inputs import DevelopmentData, keys_sha256, labels_sha256

recipes = campaign_fixtures.recipes
base_frozen = campaign_fixtures.frozen


@pytest.fixture
def example(recipes, base_frozen):
    recipe, policy = recipes
    rows, outcomes, roles = {}, {}, {}
    for role, name, day in (
        ("train", "base_train", "01"),
        ("tune", "calibration_fit", "02"),
        ("calibration", "development_selection", "03"),
    ):
        points = []
        for i in range(10):
            values = {k: None for k in FeatureValues.model_fields}
            values.update(
                history_constrained_days=i % 2,
                available_qty=10 + i,
                forecast_units_7d=8.0,
                forecast_days_of_supply=1.0,
                forecast_unavailable=0,
            )
            points.append(
                dict(
                    product_id="fixture-sku-" + str(i),
                    stock_location_id=recipe.pipeline.calibrator.stock_locations[0],
                    category_id=recipe.pipeline.calibrator.categories[0],
                    as_of="2026-07-" + day + "T23:59:59Z",
                    values=values,
                )
            )
        rows[role], outcomes[role] = points, [i % 2 for i in range(10)]
        roles[name] = dict(
            rows=10,
            positives=5,
            keys_sha256=keys_sha256(points),
            outcomes_sha256=labels_sha256(points, outcomes[role]),
        )
    content = dict(
        selected_pipeline=recipe.pipeline.model_dump(mode="json"),
        selected_model_id=recipe.pin.model_id,
        roles=roles,
        roles_disjoint=True,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        model_ready=False,
    )
    descriptor = dict(content_sha256=canonical_sha256(content), parents={})
    selection = dict(
        schema_version="stockout-selection-capsule-2.0.0",
        parents_full_replay=True,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
        selection=dict(
            selection_id="stockout-selection-sha256-" + canonical_sha256(descriptor),
            descriptor=descriptor,
            content=content,
        ),
    )
    raw = recipe.model_dump(mode="json")
    raw["pin"]["selection_id"] = selection["selection"]["selection_id"]
    recipe = ScoringRecipe.model_validate_json(canonical_bytes(raw))
    raw = policy.model_dump(mode="json", exclude={"policy_id"})
    raw["pin"] = recipe.pin.model_dump(mode="json")
    raw["policy_id"] = "stockout-scoring-policy-sha256-" + canonical_sha256(raw)
    policy = ScoringPolicy.model_validate_json(canonical_bytes(raw))
    freeze = seal(
        base_frozen[0].model_copy(
            update={
                "selection_id": recipe.pin.selection_id,
                "selection_content_sha256": descriptor["content_sha256"],
                "recipe_content_sha256": canonical_sha256(recipe.model_dump(mode="json")),
                "policy_content_sha256": canonical_sha256(policy.model_dump(mode="json")),
            }
        )
    )
    data = DevelopmentData(
        rows,
        outcomes,
        {},
        freeze.sources[0].split_policy,
        {"final_test": {"outcomes_evaluated": False}},
        "0" * 64,
    )
    return dict(data=data, selection=selection, freeze=freeze, recipe=recipe, policy=policy)


def test_selection_minimizes_approved_cost_without_fitting(example, monkeypatch):
    from retailops_ai.stockout_selection import pipeline as conditional
    from retailops_ai.stockout_training import pipeline as base

    monkeypatch.setattr(base, "fit_model", lambda *a, **k: pytest.fail("no refit"))
    monkeypatch.setattr(conditional, "fit_conditional", lambda *a, **k: pytest.fail("no refit"))
    result = compare_capacities(**example)
    report = result["content"]
    comparisons = report["comparisons"]
    chosen = min(
        ("0.4", "0.5"),
        key=lambda k: (
            comparisons[k]["capacity"]["cost"],
            comparisons[k]["capacity"]["selected"],
            float(k),
        ),
    )
    assert report["selected_fraction"] == float(chosen)
    assert comparisons["0.2"]["capacity"]["selected"] == 2
    assert report["model_refits"] == 0 and not report["final_test_outcomes_evaluated"]
    policy = ScoringPolicy.model_validate_json(canonical_bytes(result["scoring_policy"]))
    assert policy.pin == example["recipe"].pin
    assert policy.spec.thresholds == example["policy"].spec.thresholds
    assert policy.spec.costs == example["policy"].spec.costs


@pytest.mark.parametrize("change", ["test_role", "outcome", "parent", "opened_final"])
def test_unbound_or_final_data_is_refused(example, change):
    values = deepcopy(example)
    data = values["data"]
    if change == "test_role":
        data.rows["test"] = data.rows["calibration"]
    elif change == "outcome":
        data.outcomes["calibration"][0] = 1
    elif change == "parent":
        data.parents["source_dataset_id"] = "changed"
    else:
        data.coverage["final_test"]["outcomes_evaluated"] = True
    with pytest.raises(ValueError):
        compare_capacities(**values)
