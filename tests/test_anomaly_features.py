"""Temporal counterexamples: future corrections, outcome fitting and missingness."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from retailops_ai.anomalies.contract import TABLES, Point, Policy
from retailops_ai.anomalies.features import Features
from retailops_ai.source_snapshot.files import SnapshotError

SERIES = ("product", "location", "store")
DAY = date(2026, 7, 29)


def tables():
    rows = []
    for offset in range(29):
        day = DAY - timedelta(days=28 - offset)
        rows.append(
            {
                "product_id": SERIES[0],
                "selling_location_id": SERIES[1],
                "channel": SERIES[2],
                "business_date": day,
                "version": 1,
                "curated_available_at": datetime.combine(
                    day + timedelta(days=1), datetime.min.time(), UTC
                ),
                "observed_units": 10,
                "observation_status": "observed_positive",
                "source_data_complete": True,
                "quality_status": "valid",
                "source_record_sha256": f"{offset:064x}",
                "mapped_stock_location_id": "stock",
            }
        )
    return {name: rows if name == "daily_demand_versions" else [] for name in TABLES}


def test_outcome_cannot_change_expected_or_scale_and_zero_mad_has_safe_floor():
    base = tables()
    original = Features(base).point(SERIES, DAY)
    changed = deepcopy(base)
    changed["daily_demand_versions"][-1]["observed_units"] = 500
    point = Features(changed).point(SERIES, DAY)
    assert point.expected_units == original.expected_units == 10
    assert point.robust_scale_units == original.robust_scale_units == 1.0
    assert point.scale_floor_applied and point.standardized_residual == 490.0
    assert point.status == "ready_input"
    assert all(
        r.business_date < DAY and r.available_at <= point.fit_cutoff
        for r in point.references
        if r.role == "fit"
    )


def test_revision_one_microsecond_after_fit_does_not_rewrite_history():
    base = tables()
    original = Features(base).point(SERIES, DAY)
    correction = {
        **base["daily_demand_versions"][-8],
        "version": 2,
        "observed_units": 999,
        "curated_available_at": original.fit_cutoff + timedelta(microseconds=1),
        "source_record_sha256": "f" * 64,
    }
    base["daily_demand_versions"].append(correction)
    assert Features(base).point(SERIES, DAY) == original
    correction["curated_available_at"] = original.fit_cutoff
    at_boundary = Features(base).point(SERIES, DAY)
    assert at_boundary.expected_units == 999


def test_observation_correction_obeys_scoring_cutoff():
    base = tables()
    original = Features(base).point(SERIES, DAY)
    correction = {
        **base["daily_demand_versions"][-1],
        "version": 2,
        "observed_units": 100,
        "curated_available_at": original.scoring_origin + timedelta(microseconds=1),
        "source_record_sha256": "f" * 64,
    }
    base["daily_demand_versions"].append(correction)
    assert Features(base).point(SERIES, DAY) == original
    correction["curated_available_at"] = original.scoring_origin
    assert Features(base).point(SERIES, DAY).observed_units == 100


@pytest.mark.parametrize("case", ["cold_start", "unavailable", "closed", "invalid", "zero"])
def test_insufficient_invalid_and_closed_are_not_business_drops(case):
    base = tables()
    if case == "cold_start":
        base["daily_demand_versions"] = base["daily_demand_versions"][-7:]
    row = base["daily_demand_versions"][-1]
    if case == "unavailable":
        row["curated_available_at"] += timedelta(days=10)
    elif case == "closed":
        row.update(observation_status="closed", observed_units=0)
    elif case == "invalid":
        row.update(source_data_complete=False, observed_units=0)
    elif case == "zero":
        row.update(observation_status="observed_zero", observed_units=0)
    point = Features(base).point(SERIES, DAY)
    if case == "zero":
        assert point.status == "ready_input" and point.residual_units == -10
    else:
        assert point.status != "ready_input" and point.standardized_residual is None
        assert point.residual_units is None
    if case == "unavailable":
        assert point.observed_units is None and point.dq_status == "unavailable"
    if case == "invalid":
        assert point.dq_status == "invalid_input" and point.observed_units is None
    assert point.raw_dq_completeness == "not_qualified"


def test_plans_and_inventory_use_their_distinct_availability_cutoffs():
    base = tables()
    original = Features(base).point(SERIES, DAY)
    plan = {
        "plan_key": "p",
        "product_id": SERIES[0],
        "selling_location_id": None,
        "channel": "all",
        "scope": "global",
        "effective_from": DAY,
        "effective_to": DAY + timedelta(days=1),
        "version": 1,
        "curated_available_at": original.fit_cutoff + timedelta(microseconds=1),
        "source_record_sha256": "e" * 64,
        "price": Decimal("12.34"),
        "currency": "PLN",
    }
    base["price_plans"] = [plan]
    inventory = {
        "product_id": SERIES[0],
        "stock_location_id": "stock",
        "business_date": DAY,
        "snapshot_at": original.scoring_origin,
        "curated_available_at": original.scoring_origin,
        "is_full_business_day": True,
        "status": "known",
        "on_hand": 0,
        "source_record_sha256": "d" * 64,
    }
    base["inventory_daily_snapshots"] = [inventory]
    point = Features(base).point(SERIES, DAY)
    assert point.planned_price is None and point.on_hand == 0
    inventory["curated_available_at"] += timedelta(microseconds=1)
    plan["curated_available_at"] = original.fit_cutoff
    point = Features(base).point(SERIES, DAY)
    assert point.planned_price == "12.34" and point.on_hand is None


def test_unknown_tables_ambiguous_history_and_hidden_fields_fail_closed():
    base = tables()
    with pytest.raises(SnapshotError, match="allowlist"):
        Features({**base, "anomaly_injections": []})
    point = Features(base).point(SERIES, DAY)
    with pytest.raises(ValidationError):
        Point.model_validate({**point.model_dump(), "injection_magnitude": 10})
    base["daily_demand_versions"].append(dict(base["daily_demand_versions"][-8]))
    with pytest.raises(SnapshotError, match="ambiguous"):
        Features(base).point(SERIES, DAY)
    with pytest.raises(ValidationError):
        Policy(scoring_delay_hours=-1)
