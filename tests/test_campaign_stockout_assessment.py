"""Native global-queue parity and full populations, no Source/Project reads or fits."""

from datetime import UTC, datetime, timedelta

import pytest

from retailops_ai.evaluation_campaign.campaign_stockout_assessment import (
    CampaignStockoutAssessmentPolicy,
    assess_full_stockout,
)
from retailops_ai.stockout_policy.assessment import assess
from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    OperatorCapacity,
    PolicyRow,
    PolicySpec,
    RiskThresholds,
)


def row(i, *, origin=None, **changes):
    return PolicyRow(
        **{
            "product_id": f"p-{i:06d}",
            "stock_location_id": "stock",
            "as_of": origin or datetime(2026, 1, 1, tzinfo=UTC),
            "category_id": "category",
            "probability": (i % 11) / 10,
            "incident_stockout": i % 2,
            "historical_inventory_constraint": bool(i % 3),
            **changes,
        }
    )


def policy(**changes):
    return CampaignStockoutAssessmentPolicy(
        **{
            "thresholds": RiskThresholds(medium_from=0.25, high_from=0.5, critical_from=0.9),
            "capacity": OperatorCapacity(mode="top_n", top_n=3),
            "costs": AttentionCosts(false_attention=1.0, missed_incident=5.0),
            "expected_categories": ("category",),
            "expected_stock_locations": ("stock",),
            **changes,
        }
    )


@pytest.mark.parametrize(
    "capacity",
    [OperatorCapacity(mode="top_n", top_n=3), OperatorCapacity(mode="top_fraction", fraction=0.17)],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_every_native_value_matches_on_same_global_queue(capacity, reverse):
    points = [
        row(i, origin=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i // 11)) for i in range(39)
    ]
    if reverse:
        points.reverse()
    frozen = policy(capacity=capacity)
    native = assess(
        points,
        PolicySpec(thresholds=frozen.thresholds, capacity=frozen.capacity, costs=frozen.costs),
    )
    actual = assess_full_stockout(points, frozen)
    for key, value in native.items():
        if key != "final_test_outcomes_evaluated":
            assert actual[key] == value
    assert not actual["missing_required_groups"]
    assert not actual["journal_authorization_proven"] and not actual["stage_ready"]


def test_more_than_10000_rows_keep_one_global_queue_without_chunking():
    points = [row(i) for i in range(10001)]
    with pytest.raises(ValueError, match="row_limit"):
        frozen = policy()
        assess(
            points,
            PolicySpec(thresholds=frozen.thresholds, capacity=frozen.capacity, costs=frozen.costs),
        )
    actual = assess_full_stockout(iter(points), policy())
    assert actual["rows"] == 10001
    assert actual["capacity"]["selected"] == 3
    assert actual["capacity"]["origins"] == [
        {"as_of": "2026-01-01T00:00:00+00:00", "eligible": 10001, "selected": 3}
    ]
    assert sum(actual["risk_band_counts"].values()) == 10001
    assert actual["segments_at_same_global_capacity"]["category:category"] == {
        k: v for k, v in actual["capacity"].items() if k != "origins"
    }


def test_categories_share_capacity_and_missing_declared_group_is_visible():
    points = [row(1, probability=0.9, category_id="a"), row(2, probability=0.8, category_id="b")]
    actual = assess_full_stockout(
        points,
        policy(
            expected_categories=("a", "b", "missing"),
            capacity=OperatorCapacity(mode="top_n", top_n=1),
        ),
    )
    groups = actual["segments_at_same_global_capacity"]
    assert groups["category:a"]["selected"] == 1 and groups["category:b"]["selected"] == 0
    assert groups["category:missing"]["rows"] == 0
    assert groups["category:missing"]["status"] == "not_evaluable"
    assert "category:missing" in actual["missing_required_groups"]


@pytest.mark.parametrize("mode", ["max_rows", "max_input_bytes", "max_origins"])
def test_each_full_population_budget_refuses_without_truncation(mode):
    points = [
        row(i, origin=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)) for i in range(20)
    ]
    with pytest.raises(ValueError, match="budget"):
        assess_full_stockout(points, policy(**{mode: 1024 if mode == "max_input_bytes" else 1}))


@pytest.mark.parametrize("spelling", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.000000+00:00"])
def test_duplicate_UTC_physical_key_cannot_receive_two_slots(spelling):
    with pytest.raises(ValueError, match="duplicate_UTC"):
        assess_full_stockout([row(1), row(1, origin=spelling)], policy())


@pytest.mark.parametrize(
    "changes",
    [
        {"incident_stockout": True},
        {"incident_stockout": None},
        {"probability": float("nan")},
        {"category_id": "unfrozen"},
        {"stock_location_id": "unfrozen"},
    ],
)
def test_copied_invalid_or_out_of_scope_predictions_are_revalidated(changes):
    with pytest.raises(ValueError):
        assess_full_stockout([row(1).model_copy(update=changes)], policy())


def test_no_positives_remain_undefined_and_ties_are_lexical():
    points = [
        row(2, probability=0.8, incident_stockout=0),
        row(1, probability=0.8, incident_stockout=0),
    ]
    actual = assess_full_stockout(points, policy(capacity=OperatorCapacity(mode="top_n", top_n=1)))
    assert actual["capacity"]["recall"] is None
    assert actual["capacity"]["status"] == "not_evaluable"
    assert (
        actual["capacity"]
        == assess_full_stockout(
            list(reversed(points)), policy(capacity=OperatorCapacity(mode="top_n", top_n=1))
        )["capacity"]
    )


def test_empty_population_is_refused():
    with pytest.raises(ValueError, match="empty_population"):
        assess_full_stockout([], policy())


def test_explicit_unknown_category_remains_a_reported_group():
    actual = assess_full_stockout(
        [row(1, category_id="__unknown__")], policy(expected_categories=("__unknown__",))
    )
    assert actual["segments_at_same_global_capacity"]["category:__unknown__"]["rows"] == 1


@pytest.mark.parametrize("inventory", [("category", "category"), ("z", "a"), ()])
def test_incomplete_or_ambiguous_declared_scope_is_refused(inventory):
    with pytest.raises(ValueError):
        policy(expected_categories=inventory)


@pytest.mark.parametrize("field", ["max_rows", "capacity", "costs"])
def test_copied_boolean_resource_capacity_or_cost_is_not_serialized_to_one(field):
    frozen = policy()
    invalid = {
        "max_rows": True,
        "capacity": frozen.capacity.model_copy(update={"top_n": True}),
        "costs": frozen.costs.model_copy(update={"false_attention": True}),
    }[field]
    with pytest.raises(ValueError):
        assess_full_stockout([row(1)], frozen.model_copy(update={field: invalid}))
