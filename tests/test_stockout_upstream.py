"""Rolling origins, channel pooling, unavailable calendars and immutable full replay."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, ORIGIN, fact
from test_forecast_features import tables as tables
from test_stockout_features import native as native

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout.feature_dataset import MAX_FEATURE_BYTES
from retailops_ai.stockout.upstream import upstream_point
from retailops_ai.stockout.upstream_contract import UpstreamPoint
from retailops_ai.stockout.upstream_dataset import (
    build_comparison,
    build_upstream,
    digest,
    verify_upstream,
)

AS_OF = ORIGIN.forecast_origin + timedelta(microseconds=999999)


@pytest.fixture
def routed(tables):
    tables["fulfillment_routes"] = [
        fact(
            id="route",
            route_key="route",
            version=1,
            selling_location_id="s-1",
            channel="store",
            stock_location_id="stock",
            effective_from=DAY - timedelta(days=40),
            effective_to=DAY + timedelta(days=15),
        )
    ]
    return tables


def point(routed):
    return upstream_point(routed, product="p-1", stock="stock", as_of=AS_OF)


def test_ma28_is_rolling_origin_and_keeps_inventory_microsecond_origin(routed):
    p = point(routed)
    assert p.status == "available" and p.forecast_units_7d == 26.0 * 7
    assert p.series[0].daily_units == (26.0,) * 7
    assert p.training_cutoff == p.selection_cutoff == ORIGIN.forecast_origin < AS_OF
    assert p.source_available_at <= p.forecast_origin


@pytest.mark.parametrize(
    "table", ["daily_demand_versions", "fulfillment_routes", "business_calendar", "assortment"]
)
def test_later_revisions_do_not_rewrite_historical_forecast(routed, table):
    before = point(routed)
    late = {
        **routed[table][0],
        "version": 900,
        "curated_available_at": AS_OF + timedelta(microseconds=1),
    }
    if table == "daily_demand_versions":
        late["observed_units"] = 999999
    elif table == "fulfillment_routes":
        late["stock_location_id"] = "elsewhere"
    elif table == "business_calendar":
        late["location_open"] = False
    routed[table].append(late)
    assert point(routed) == before


def test_subsecond_data_is_not_rounded_forward_into_ai04_origin(routed):
    before = point(routed)
    observation = next(
        r for r in routed["daily_demand_versions"] if r["business_date"] == DAY - timedelta(days=1)
    )
    routed["daily_demand_versions"].append(
        {
            **observation,
            "version": 2,
            "observed_units": 999999,
            "curated_available_at": ORIGIN.forecast_origin + timedelta(microseconds=1),
        }
    )
    assert point(routed) == before


def test_known_revision_changes_estimate_but_future_actuals_do_not(routed):
    observation = next(
        r for r in routed["daily_demand_versions"] if r["business_date"] == DAY - timedelta(days=1)
    )
    routed["daily_demand_versions"].append(
        {
            **observation,
            "version": 2,
            "observed_units": 66,
            "curated_available_at": ORIGIN.forecast_origin,
        }
    )
    assert point(routed).forecast_units_7d == 27.0 * 7
    before = point(routed)
    for row in routed["daily_demand_versions"]:
        if row["business_date"] >= DAY:
            row["observed_units"] = 999999
    assert point(routed) == before


def test_unknown_calendar_is_missing_and_known_closed_day_is_zero(routed):
    target = DAY + timedelta(days=1)
    calendar = next(r for r in routed["business_calendar"] if r["business_date"] == target)
    calendar["location_open"] = False
    p = point(routed)
    assert p.forecast_units_7d == 26.0 * 6 and p.series[0].daily_units[0] == 0.0
    routed["business_calendar"].remove(calendar)
    p = point(routed)
    assert p.status == "insufficient_data" and p.forecast_units_7d is None
    assert p.reason == "target_calendar_unknown" and p.series[0].daily_units[0] is None


def test_missing_history_is_not_zero_and_minimum_seven_known_days_is_exact(routed):
    original = deepcopy(routed["daily_demand_versions"])
    for n, status in [(7, "available"), (6, "insufficient_data")]:
        routed["daily_demand_versions"] = [
            r for r in original if DAY - timedelta(days=n) <= r["business_date"] < DAY
        ]
        p = point(routed)
        assert p.status == status
        if status == "insufficient_data":
            assert p.reason == "insufficient_calendar_window" and p.forecast_units_7d is None


def add_channel(routed, stock="stock"):
    for name in [
        "channel_assignments",
        "assortment",
        "business_calendar",
        "daily_demand_versions",
        "fulfillment_routes",
    ]:
        for row in list(routed[name]):
            extra = {
                **row,
                "id": "web-" + row["id"],
                "selling_location_id": "web",
                "channel": "online",
            }
            for field in ["assignment_key", "assortment_key", "route_key"]:
                if field in extra:
                    extra[field] = "web-" + extra[field]
            if name == "fulfillment_routes":
                extra["stock_location_id"] = stock
            routed[name].append(extra)


def test_two_channels_are_summed_once_and_other_warehouse_remains_separate(routed):
    add_channel(routed)
    p = point(routed)
    assert len(p.series) == 2 and p.forecast_units_7d == 26.0 * 14
    routed["fulfillment_routes"][-1]["stock_location_id"] = "other"
    assert point(routed).forecast_units_7d == 26.0 * 7
    other = upstream_point(routed, product="p-1", stock="other", as_of=AS_OF)
    assert len(other.series) == 1 and other.forecast_units_7d == 26.0 * 7


def test_ambiguous_physical_routing_and_truth_table_are_rejected(routed):
    routed["fulfillment_routes"].append(
        {**routed["fulfillment_routes"][0], "route_key": "duplicate", "stock_location_id": "other"}
    )
    with pytest.raises(SnapshotError, match="ambiguous_physical_routing"):
        point(routed)
    routed["fulfillment_routes"].pop()
    routed["inventory_demand_outcomes"] = []
    with pytest.raises(SnapshotError, match="tables_not_allowlisted"):
        point(routed)


@pytest.mark.parametrize("field", ["training_cutoff", "selection_cutoff", "source_available_at"])
def test_future_fitted_lineage_or_future_source_is_rejected(routed, field):
    raw = point(routed).model_dump(mode="json")
    raw[field] = (AS_OF + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError, match="cutoff_mismatch|future_information"):
        UpstreamPoint.model_validate_json(json.dumps(raw))


def test_daily_inventory_origin_cannot_use_a_later_end_of_day_forecast(routed):
    with pytest.raises(SnapshotError, match="would_use_future_information"):
        upstream_point(routed, product="p-1", stock="stock", as_of=AS_OF - timedelta(hours=1))


def test_native_full_replay_rejects_resealed_forecasts_and_equal_grain_matrix(native, tmp_path):
    _, curated, features, _ = native
    upstream = build_upstream(curated.directory, features)
    target = tmp_path / "upstream.json"
    assert write_artifact(upstream, target, max_bytes=MAX_FEATURE_BYTES) == "published"
    assert write_artifact(upstream, target, max_bytes=MAX_FEATURE_BYTES) == "reused"
    assert verify_upstream(target, curated.directory, features) == upstream
    comparison = build_comparison(features, upstream)
    assert len(comparison["rows"]) == 60
    assert comparison["report"]["common_keys_identical"] is True
    assert comparison["report"]["model_specific_drops"] == 0
    assert comparison["report"]["ablation_model_results"] == "pending"
    assert comparison["report"]["model_ready"] is False
    assert (
        comparison["descriptor"]["variants"]["with_upstream"][:-3]
        == comparison["descriptor"]["variants"]["without_upstream"]
    )
    assert all("incident_stockout" not in row for row in comparison["rows"])
    corrupted = deepcopy(upstream)
    row = corrupted["points"][0]
    row["training_cutoff"] = (AS_OF + timedelta(days=1)).isoformat()
    corrupted["descriptor"]["points_sha256"] = digest(corrupted["points"])
    corrupted["upstream_id"] = "upstream-sha256-" + digest(corrupted["descriptor"])
    bad = tmp_path / "resealed.json"
    bad.write_bytes(canonical_json(corrupted))
    with pytest.raises(SnapshotError, match="full_replay"):
        verify_upstream(bad, curated.directory, features)
    foreign = deepcopy(upstream)
    foreign["descriptor"]["feature_dataset_id"] = "foreign"
    with pytest.raises(SnapshotError, match="pin_mismatch"):
        build_comparison(features, foreign)
    missing = deepcopy(upstream)
    missing["points"].pop()
    with pytest.raises(SnapshotError, match="physical_grain_mismatch"):
        build_comparison(features, missing)
