"""Cross-product spillover, missing evidence and changed inventories fail closed."""

import copy
import json

import pytest

from retailops_ai.anomaly_evaluation.paired_source_comparison import (
    POLICY,
    SOURCE_TABLES,
    PairedSourceComparison,
    paired_clean_windows,
)


def tables():
    result = {name: [] for name in SOURCE_TABLES}
    result["products"] = [{"id": "product-a"}, {"id": "product-b"}]
    result["orders"] = [{"id": "basket", "ordered_at": "2026-05-25T08:00:00+00:00", "total": 10}]
    result["order_items"] = [
        {"id": "line-a", "order_id": "basket", "product_id": "product-a", "quantity": 1},
        {"id": "line-b", "order_id": "basket", "product_id": "product-b", "quantity": 1},
    ]
    result["daily_demand_observations"] = [
        {"product_id": product, "business_date": day, "observed_units": 7}
        for product in ("product-a", "product-b")
        for day in ("2026-05-24", "2026-05-25", "2026-05-26")
    ]
    return result


def compare(tmp_path, ordinary, planned, injections=()):
    with PairedSourceComparison(tmp_path / "index.sqlite") as index:
        index.add(ordinary, parent="ordinary")
        index.add(planned, parent="planned")
        return index.finish(injections)


def test_equal_complete_tables_are_order_independent(tmp_path):
    ordinary = tables()
    result = compare(tmp_path, ordinary, {k: list(reversed(v)) for k, v in ordinary.items()})
    assert len(result["parent_table_rows"]["ordinary"]) == 58
    assert result["first_difference_by_product"] == {}
    assert result["unknown_from_by_product"] == {}
    assert result["changed_table_memberships"] == {}
    assert not result["quality_qualified"] and not result["stage_ready"]
    assert (tmp_path / "index.sqlite").stat().st_mode & 0o777 == 0o600


def test_changed_shared_basket_is_unknown_for_every_member(tmp_path):
    ordinary, planned = tables(), tables()
    planned["orders"][0]["total"] = 11
    result = compare(
        tmp_path, ordinary, planned, [{"product_id": "product-a", "start_date": "2026-05-25"}]
    )
    assert result["unknown_from_by_product"] == {
        "product-a": "2026-05-25",
        "product-b": "2026-05-25",
    }
    assert result["changed_table_memberships"]["orders"] == {
        "ordinary_only_memberships": 2,
        "planned_only_memberships": 2,
    }


@pytest.mark.parametrize(
    ("table", "clock"),
    [
        ("daily_demand_observations", "business_date"),
        ("daily_price_observations", "business_date"),
        ("inventory_returns", "returned_at"),
        ("inventory_ledger", "occurred_at"),
        ("inventory_physical_daily_balances", "business_date"),
        ("daily_return_cohorts", "business_date"),
        ("stockout_episodes", "start_at"),
        ("inventory_window_diagnostics", "origin"),
    ],
)
def test_cross_sku_process_difference_is_not_clean_before_its_own_injection(tmp_path, table, clock):
    ordinary, planned = tables(), tables()
    for value in (ordinary, planned):
        value[table] = [
            {
                "product_id": "product-b",
                clock: "2026-05-25" if clock == "business_date" else "2026-05-25T00:00:00Z",
                "quantity": 119,
            }
        ]
    planned[table][0]["quantity"] = 116
    result = compare(
        tmp_path, ordinary, planned, [{"product_id": "product-b", "start_date": "2026-06-02"}]
    )
    assert result["unknown_from_by_product"]["product-b"] == "2026-05-25"


@pytest.mark.parametrize("change", ["removed", "added", "duplicate"])
def test_row_multiset_preserves_both_directions_and_multiplicity(tmp_path, change):
    ordinary, planned = tables(), tables()
    row = planned["daily_demand_observations"][0]
    if change == "removed":
        planned["daily_demand_observations"].pop(0)
    else:
        planned["daily_demand_observations"].append(
            row if change == "duplicate" else {**row, "observed_units": 9}
        )
    result = compare(tmp_path, ordinary, planned)
    assert result["unknown_from_by_product"] == {"product-a": "2026-05-24"}
    assert result["changed_table_memberships"]["daily_demand_observations"] == {
        "ordinary_only_memberships": int(change == "removed"),
        "planned_only_memberships": int(change != "removed"),
    }


def test_moved_event_marks_earlier_of_both_dates_not_just_new_date(tmp_path):
    ordinary, planned = tables(), tables()
    planned["orders"][0]["ordered_at"] = "2026-05-27T08:00:00Z"
    result = compare(tmp_path, ordinary, planned)
    assert set(result["unknown_from_by_product"].values()) == {"2026-05-25"}


def test_interval_start_is_not_replaced_by_later_snapshot_day(tmp_path):
    ordinary, planned = tables(), tables()
    for value in (ordinary, planned):
        value["inventory_daily_snapshots"] = [
            {
                "product_id": "product-b",
                "business_date": "2026-05-25",
                "period_from_at": "2026-05-24T22:00:00Z",
                "period_to_at": "2026-05-25T22:00:00Z",
                "quantity": 4,
            }
        ]
    planned["inventory_daily_snapshots"][0]["quantity"] = 5
    result = compare(tmp_path, ordinary, planned)
    assert result["unknown_from_by_product"] == {"product-b": "2026-05-24"}


def test_all_columns_participate_in_equality_not_just_quantities(tmp_path):
    ordinary, planned = tables(), tables()
    planned["daily_demand_observations"][0]["new_producer_field"] = "changed"
    result = compare(tmp_path, ordinary, planned)
    assert result["unknown_from_by_product"] == {"product-a": "2026-05-24"}


@pytest.mark.parametrize("field", ["quantity", "gross_revenue", "available_at"])
def test_realized_price_observations_are_product_outcomes_not_fixed_price_policy(tmp_path, field):
    ordinary, planned = tables(), tables()
    row = {
        "product_id": "product-b",
        "business_date": "2026-07-10",
        "available_at": "2026-07-11T02:00:00Z",
        "quantity": 3,
        "gross_revenue": "29.97",
        "realized_unit_price": "9.99",
    }
    ordinary["daily_price_observations"] = [row]
    planned["daily_price_observations"] = [
        {
            **row,
            field: {"quantity": 1, "gross_revenue": "9.99", "available_at": "2026-07-12T02:00:00Z"}[
                field
            ],
        }
    ]
    result = compare(tmp_path, ordinary, planned)
    assert result["unknown_from_by_product"] == {"product-b": "2026-07-10"}
    assert result["changed_table_memberships"]["daily_price_observations"] == {
        "ordinary_only_memberships": 1,
        "planned_only_memberships": 1,
    }


@pytest.mark.parametrize("table", ["delivery_plan_versions", "inventory_supplier_samples"])
def test_supply_rows_join_actual_product_and_earliest_order_knowledge(tmp_path, table):
    ordinary, planned = tables(), tables()
    key = "replenishment_order_id" if table == "delivery_plan_versions" else "order_id"
    for value in (ordinary, planned):
        value["replenishment_orders"] = [
            {
                "replenishment_order_id": "supply",
                "product_id": "product-b",
                "ordered_at": "2026-05-23T08:00:00Z",
            }
        ]
        value[table] = [{key: "supply", "lead_days": 3}]
    planned[table][0]["lead_days"] = 4
    result = compare(tmp_path, ordinary, planned)
    assert result["unknown_from_by_product"] == {"product-b": "2026-05-23"}


def test_injected_product_never_clean_just_because_outcomes_match(tmp_path):
    result = compare(
        tmp_path, tables(), tables(), [{"product_id": "product-a", "start_date": "2026-05-25"}]
    )
    assert result["first_difference_by_product"] == {}
    assert result["unknown_from_by_product"] == {"product-a": "2026-05-25"}


@pytest.mark.parametrize("table", ["products", "return_policies", "product_simulation_parameters"])
def test_dimension_or_private_policy_change_is_not_a_paired_scenario(tmp_path, table):
    ordinary, planned = tables(), tables()
    planned[table].append({"id": "different"})
    with pytest.raises(ValueError, match="product_population|invariant_tables_differ"):
        compare(tmp_path, ordinary, planned)


@pytest.mark.parametrize("kind", ["missing_table", "extra_table", "orphan", "unknown_product"])
def test_incomplete_or_unsupported_input_never_yields_clean_proof(tmp_path, kind):
    value = tables()
    if kind == "missing_table":
        del value["inventory_returns"]
    elif kind == "extra_table":
        value["future_source_table"] = []
    elif kind == "orphan":
        value["order_items"][0]["order_id"] = "missing"
    else:
        value["order_items"][0]["product_id"] = "unknown"
    with PairedSourceComparison(tmp_path / "index.sqlite") as index:
        with pytest.raises(ValueError, match="anomaly_paired_truth"):
            index.add(value, parent="ordinary")
        with pytest.raises(ValueError, match="parent_order"):
            index.add(tables(), parent="ordinary")
        with pytest.raises(ValueError, match="incomplete_comparison"):
            index.finish([])


@pytest.mark.parametrize("value", [None, "2026-05-25T12:00:00", "2026-05-25T12:00:00+02:00"])
def test_unknown_or_local_clock_is_not_a_clean_proof(tmp_path, value):
    ordinary = tables()
    ordinary["orders"][0]["ordered_at"] = value
    with pytest.raises(ValueError, match="undated_dynamic_row|non_utc_date"):
        compare(tmp_path, ordinary, tables())


def test_existing_index_cannot_be_replaced_or_reused(tmp_path):
    target = tmp_path / "index.sqlite"
    target.write_text("retained-failed-read")
    with pytest.raises(FileExistsError):
        PairedSourceComparison(target)
    assert target.read_text() == "retained-failed-read"


def test_half_comparison_and_over_budget_never_complete(tmp_path):
    with PairedSourceComparison(tmp_path / "index.sqlite", max_rows=1) as index:
        with pytest.raises(ValueError, match="row_budget"):
            index.add(tables(), parent="ordinary")
        with pytest.raises(ValueError, match="incomplete_comparison"):
            index.finish([])


def window(start="2026-05-24", end="2026-05-28", *, event="sale_completed", product="product-b"):
    return {
        "event_type": event,
        "product_id": product,
        "selling_location_id": "location",
        "channel": "store",
        "currency": "PLN",
        "window": {"start": start, "end": end},
        "available_at": "2026-05-30T00:00:00Z",
    }


def test_clean_intersection_retains_gaps_later_maturity_and_spillover_for_all_events(tmp_path):
    ordinary, planned = tables(), tables()
    planned["orders"][0]["total"] = 11
    proof = compare(tmp_path, ordinary, planned)
    baseline = [window(end="2026-05-24"), window(start="2026-05-26")]
    injected = [window()]
    injected[0]["available_at"] = "2026-06-01T00:00:00Z"
    result = paired_clean_windows(baseline, injected, proof)
    assert result == [{**injected[0], "window": {"start": "2026-05-24", "end": "2026-05-24"}}]
    for event in ("sale_completed", "return_completed"):
        later = [window(start="2026-05-26", event=event)]
        assert paired_clean_windows(later, later, proof) == []


def test_comparison_digest_and_windows_do_not_depend_on_row_order(tmp_path):
    ordinary, planned = tables(), tables()
    planned["orders"][0]["total"] = 11
    first = compare(tmp_path, ordinary, planned)
    (tmp_path / "other").mkdir()
    second = compare(
        tmp_path / "other",
        {k: list(reversed(v)) for k, v in ordinary.items()},
        {k: list(reversed(v)) for k, v in planned.items()},
    )
    assert first == second
    assert json.loads(json.dumps(first)) == first


def test_comparison_policy_cannot_be_weakened(tmp_path):
    proof = compare(tmp_path, tables(), tables())
    proof = copy.deepcopy(proof)
    proof["policy"] = {**POLICY, "recovery": "allow_equal_later_days"}
    with pytest.raises(ValueError, match="anomaly_paired_truth_policy"):
        paired_clean_windows([window()], [window()], proof)


def test_equal_future_days_never_fill_missing_complete_days(tmp_path):
    proof = compare(tmp_path, tables(), tables())
    baseline = [window(end="2026-05-24"), window(start="2026-05-26")]
    actual = paired_clean_windows(baseline, [window()], proof)
    assert [w["window"] for w in actual] == [w["window"] for w in baseline]


def test_caller_cannot_mutate_the_policy_through_a_completed_result(tmp_path):
    proof = compare(tmp_path, tables(), tables())
    proof["policy"]["recovery"] = "changed"
    assert POLICY["recovery"] == "never_inferred_from_later_equal_rows"


def test_pair_cannot_be_completed_twice(tmp_path):
    with PairedSourceComparison(tmp_path / "index.sqlite") as index:
        index.add(tables(), parent="ordinary")
        index.add(tables(), parent="planned")
        index.finish([])
        with pytest.raises(ValueError, match="incomplete_comparison"):
            index.finish([])
