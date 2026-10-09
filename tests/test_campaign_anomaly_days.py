"""Whole public Source day projection, exact native semantics and private-state rejection."""

import hashlib
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest
from test_campaign_anomaly_parent import public_parent, reader  # noqa: F401

from retailops_ai.curated.builder import iter_rows
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.contract import GRAIN, TABLES
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.day_qualification.projection import declarations
from retailops_ai.evaluation_campaign.campaign_anomaly_days import (
    CampaignAnomalyDayPlan,
    CampaignAnomalyDayProjection,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_parent import CampaignAnomalyParentStateError
from retailops_ai.full_raw_dq.source import ParentFacts
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, decode_json


@pytest.fixture(scope="module")
def day_case(public_parent, tmp_path_factory):  # noqa: F811 - imported parametrized fixture
    scratch = tmp_path_factory.mktemp("ai09-day-parent")
    with reader(public_parent, scratch) as parent:
        manifest = public_parent[3]
        tables = {
            name: list(iter_rows(parent._replay.curated, spec["files"], 256))
            for name in TABLES
            for spec in manifest["tables"]
            if spec["table"] == name
        }
        expected = declarations(tables, manifest["descriptor"]["source_parameters"]["start_date"])
        yield parent, expected, manifest
    assert parent.receipt()["physical_source_and_curated_replay_passed"]
    assert not list(scratch.iterdir())


def project(case, scratch, **updates):
    parent, expected, _ = case
    plan = CampaignAnomalyDayPlan(
        **{
            "parent_plan_sha256": canonical_sha256(parent.plan.model_dump(mode="json")),
            "parent_events_sha256": parent.native_events_sha256,
            "expected_days": len(expected),
            "max_index_bytes": 128 * 1024**2,
            **updates,
        }
    )
    return CampaignAnomalyDayProjection(parent, plan, scratch)


def test_all_series_all_sales_and_complete_return_tail_equal_native(day_case, tmp_path):
    _, expected, manifest = day_case
    adapter = project(day_case, tmp_path)
    expected_by_grain = {tuple(getattr(day, k) for k in GRAIN): day for day in expected}
    with adapter:
        assert list(adapter.days) == list(expected_by_grain)
        assert len(adapter.days) == len(expected)
        assert list(adapter.days.values()) == expected
        assert adapter.stats["stored_source_rows"] == sum(
            spec["row_count"] for spec in manifest["tables"] if spec["table"] in TABLES
        )
        assert adapter.stats["projected_days"] == len(expected)
        assert adapter.stats["maximum_series_rows"] <= adapter.plan.max_series_rows
        assert adapter.stats["maximum_series_bytes"] <= adapter.plan.max_series_bytes
        assert adapter.stats["retained_index_bytes"] <= adapter.stats["maximum_index_bytes"]
        assert adapter._db().execute("PRAGMA freelist_count").fetchone()[0] == 0
        assert adapter._db().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall() == [("days",)]
        assert adapter.path.stat().st_mode & 0o777 == 0o600
        assert adapter.path.parent.stat().st_mode & 0o777 == 0o700
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        with pytest.raises(KeyError):
            adapter.days[("sale_completed",)]
        missing = ("sale_completed", "1900-01-01", *next(iter(expected_by_grain))[2:])
        assert adapter.days.get(missing) is None
    receipt = adapter.receipt()
    assert (
        receipt["native_days_sha256"]
        == hashlib.sha256(
            b"".join(canonical_json(day.model_dump(mode="json")) + b"\n" for day in expected)
        ).hexdigest()
    )
    assert receipt["complete_native_day_projection_passed"]
    assert receipt["public_parent_final_verification_required"]
    assert not receipt["producer_closure_policy_verified"]
    assert receipt["business_event_day_completeness"] == "not_qualified"
    assert not receipt["quality_qualified"] and not receipt["stage_ready"]
    with pytest.raises(CampaignAnomalyParentStateError, match="state_unavailable"):
        len(adapter.days)
    with pytest.raises(SnapshotError, match="single_use"):
        with adapter:
            pass
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"max_index_bytes": 4096}, "combined_index_budget"),
        ({"max_series_rows": 1}, "complete_series_budget"),
        ({"max_series_bytes": 4096}, "complete_series_budget"),
        ({"max_series": 1}, "series_population_budget"),
        ({"expected_days_delta": -1}, "full_population_binding"),
        ({"expected_days_delta": 1}, "full_population_binding"),
        ({"parent_plan_sha256": "0" * 64}, "parent_binding"),
        ({"parent_events_sha256": "0" * 64}, "parent_binding"),
    ],
)
def test_resource_extent_and_parent_failures_never_return_smaller_days(
    day_case, tmp_path, changes, reason
):
    changes = deepcopy(changes)
    if "expected_days_delta" in changes:
        changes["expected_days"] = len(day_case[1]) + changes.pop("expected_days_delta")
    adapter = project(day_case, tmp_path, **changes)
    with pytest.raises(SnapshotError, match=reason):
        with adapter:
            pass
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("tamper", ["row", "resealed_row", "grain", "delete"])
def test_private_tampering_is_rejected_before_completed_receipt(day_case, tmp_path, tamper):
    adapter = project(day_case, tmp_path)
    with pytest.raises(CampaignAnomalyParentStateError, match="private_"):
        with adapter:
            grain = next(iter(adapter.days))
            day = adapter.days[grain]
            if tamper == "delete":
                adapter._db().execute(
                    "DELETE FROM days WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?",
                    grain,
                )
            elif tamper == "grain":
                adapter._db().execute(
                    "UPDATE days SET business_date='1900-01-01' WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?",
                    grain,
                )
            else:
                value = day.model_dump(mode="json")
                value["known_at"] = (stamp(value["known_at"]) + timedelta(days=1)).isoformat()
                raw = canonical_json(value)
                if tamper == "resealed_row":
                    adapter._db().execute(
                        "UPDATE days SET payload=?,digest=? WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?",
                        (raw, hashlib.sha256(raw).hexdigest(), *grain),
                    )
                else:
                    adapter._db().execute(
                        "UPDATE days SET payload=? WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?",
                        (raw, *grain),
                    )
                    adapter.days[grain]
            adapter._db().commit()
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_body_failure_withholds_receipt_and_parent_stays_usable(day_case, tmp_path):
    adapter = project(day_case, tmp_path)
    with pytest.raises(OSError, match="control_body_failure"):
        with adapter:
            raise OSError("control_body_failure")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert day_case[0].parent.events[0]
    assert not list(tmp_path.iterdir())


def test_index_records_keep_native_canonical_day_format(day_case, tmp_path):
    with project(day_case, tmp_path) as adapter:
        for raw, digest in adapter._db().execute("SELECT payload,digest FROM days"):
            assert canonical_json(decode_json(raw)) == raw
            assert hashlib.sha256(raw).hexdigest() == digest


@pytest.mark.parametrize(
    "activity,complete,status",
    [
        ("open", True, "qualified"),
        ("closed", True, "location_closed"),
        ("missing", False, "source_incomplete"),
    ],
)
def test_native_zero_sale_series_has_sale_day_without_invented_purchase_return_cohort(
    activity, complete, status
):
    observation = {
        "business_date": date(2026, 7, 22),
        "product_id": "00000000-0000-0000-0000-000000000001",
        "selling_location_id": "00000000-0000-0000-0000-000000000002",
        "channel": "store",
        "currency": "PLN",
        "source_data_complete": complete,
        "quality_status": "valid" if complete else "missing",
        "observation_status": activity,
        "observed_units": 0,
        "available_at": datetime(2026, 7, 23, tzinfo=UTC),
    }
    tables = {name: [] for name in TABLES}
    tables["daily_demand_observations"] = [observation]
    days = declarations(tables, "2026-07-22")
    assert len(days) == 1 and days[0].event_type == "sale_completed"
    assert not days[0].expected_business_ids and not days[0].required_sale_ids
    gate = DayGate(days, {"accepted_facts": [], "quarantine": []}, b"", ParentFacts([], {}))
    grain = tuple(getattr(days[0], key) for key in GRAIN)
    assert gate.point(grain, "2026-07-22T23:59:59+00:00").status == "closure_unavailable"
    point = gate.point(grain, "2026-07-23T00:00:00+00:00")
    assert point.status == status
    assert point.observed_units == (0 if status == "qualified" else None)
    assert gate.point(("return_completed", *grain[1:]), point.as_of).status == "no_declaration"
