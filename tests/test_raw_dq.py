"""DQ counterexamples: unknown data is not zero and late facts cannot rewrite history."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from retailops_ai.raw_dq.contract import SOURCE, TOPIC, stamp
from retailops_ai.raw_dq.replay import Replay
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256


def event(sale="sale", quantity="2", occurred="2026-07-02T09:00:00+00:00"):
    return {
        "event_id": str(uuid4()),
        "event_type": "sale_completed",
        "schema_version": "1.0",
        "source": SOURCE,
        "topic": TOPIC,
        "correlation_id": "order",
        "occurred_at": occurred,
        "ingested_at": occurred,
        "payload": {
            "sale_id": sale,
            "product_id": "product",
            "store_id": "store",
            "channel": "store",
            "quantity": quantity,
            "total_amount": f"{int(quantity) * 10:.2f}",
            "unit_price": "10.00",
            "currency": "PLN",
            "sku": "optional",
        },
    }


def record(value, offset=0, received="2026-07-02T10:00:00+00:00"):
    payload = {
        "kind": "event",
        "topic": TOPIC,
        "partition": 0,
        "offset": offset,
        "received_at": received,
        "body_utf8": canonical_json(value).decode(),
    }
    return {"record_id": "raw-record-sha256-" + json_sha256(payload), **payload}


def progress(
    after=0, through="2026-07-02T23:59:59.999999+00:00", received="2026-07-03T00:00:00+00:00"
):
    payload = {
        "kind": "progress",
        "scope": "selected_sales_fixture",
        "after_offset": after,
        "received_at": received,
        "complete_through": through,
    }
    return {"record_id": "raw-record-sha256-" + json_sha256(payload), **payload}


def test_exact_and_business_duplicates_have_one_fact_and_idempotent_receipt():
    replay = Replay()
    sale = event()
    first = record(sale)
    assert replay.consume(first)["action"] == "accepted"
    before = replay.snapshot()
    assert replay.consume(first)["action"] == "accepted"
    assert replay.snapshot() == before
    assert replay.consume(record(sale, 1))["action"] == "duplicate_event"
    sale["event_id"] = str(uuid4())
    sale["payload"].pop("sku")
    assert replay.consume(record(sale, 2))["action"] == "duplicate_business"
    assert len(replay.facts) == len(replay.revisions) == 1
    assert replay.snapshot()["final_aggregates"][0]["total_amount"] == "20.00"


@pytest.mark.parametrize("same_id", [True, False])
def test_conflicting_identity_is_quarantined_without_replacing_prior_fact(same_id):
    replay = Replay()
    sale = event()
    replay.consume(record(sale))
    changed = event(quantity="3")
    if same_id:
        changed["event_id"] = sale["event_id"]
    result = replay.consume(record(changed, 1))
    assert result["action"] == "quarantined"
    assert result["reason"] == (
        "event_id_content_conflict" if same_id else "business_revision_requires_explicit_version"
    )
    assert len(replay.revisions) == 1 and replay.facts[0]["quantity"] == 2


def test_late_revision_has_its_own_clock_and_cannot_rewrite_old_as_of_view():
    replay = Replay()
    replay.consume(record(event()))
    replay.consume(progress())
    cutoff = "2026-07-03T00:00:00+00:00"
    before = replay.aggregates_as_of(cutoff)
    late = record(event(sale="late", quantity="3"), 1, "2026-07-03T01:00:00+00:00")
    assert replay.consume(late)["reason"] == "late"
    assert replay.aggregates_as_of(cutoff) == before
    at = replay.aggregates_as_of(late["received_at"])
    assert at[0]["quantity"] == 5
    assert at[0]["previous_revision_id"] == before[0]["revision_id"]
    assert replay.aggregates_as_of("2026-07-02T09:59:59.999999+00:00") == []
    at[0]["quantity"] = 1000
    assert replay.aggregates_as_of(late["received_at"])[0]["quantity"] == 5


def test_max_event_time_is_not_a_watermark_and_missing_grain_is_not_zero():
    replay = Replay()
    replay.consume(record(event(occurred="2026-07-02T09:30:00+00:00")))
    assert replay.consume(record(event(sale="earlier"), 1))["reason"] == "out_of_order"
    report = replay.snapshot()["report"]
    assert report["declared_source_watermark"] is None
    assert report["curated_completeness"] == "not_qualified"
    assert report["transport_durability_proven"] is False
    assert len(replay.snapshot()["final_aggregates"]) == 1


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("major", "unsupported_schema_version"),
        ("minor", "unsupported_schema_version"),
        ("additive", "invalid_sales_contract"),
        ("fractional", "invalid_sales_contract"),
        ("money", "invalid_sales_contract"),
        ("future_ingestion", "fact_unavailable_at_delivery"),
        ("route", "invalid_sales_contract"),
        ("nonfinite", "invalid_sales_contract"),
    ],
)
def test_bad_event_never_becomes_a_business_fact(mutation, reason):
    sale = event()
    if mutation in {"major", "minor"}:
        sale["schema_version"] = "2.0" if mutation == "major" else "1.1"
    elif mutation == "additive":
        sale["payload"]["anomaly_label"] = "spike"
    elif mutation == "fractional":
        sale["payload"]["quantity"] = "0.5"
    elif mutation == "money":
        sale["payload"]["total_amount"] = "19.999"
    elif mutation == "future_ingestion":
        sale["ingested_at"] = "2026-07-03T00:00:00+00:00"
    elif mutation == "route":
        sale["topic"] = "retailops.inventory.v1"
    else:
        sale["payload"]["quantity"] = "NaN"
    replay = Replay()
    assert replay.consume(record(sale))["reason"] == reason
    assert not replay.facts and not replay.revisions
    assert replay.snapshot()["dlq_fixture"][0]["status"] == "offline_only"


@pytest.mark.parametrize(
    "body", ['{"schema_version":"1.0","schema_version":"1.1"}', '{"value":NaN}', "[]"]
)
def test_bad_json_has_a_safe_reason_and_no_payload_in_quarantine(body):
    raw = record(event())
    raw["body_utf8"] = body
    raw["record_id"] = "raw-record-sha256-" + json_sha256(
        {k: v for k, v in raw.items() if k != "record_id"}
    )
    replay = Replay()
    assert replay.consume(raw)["reason"] == "invalid_json"
    assert "body_utf8" not in replay.quarantine[0]


def test_optional_sku_is_not_a_business_key():
    sale = event()
    sale["payload"].pop("sku")
    replay = Replay()
    assert replay.consume(record(sale))["action"] == "accepted"


@pytest.mark.parametrize("field", ["latent_demand", "observed_sales", "data_quality_status"])
def test_legacy_optional_fields_outside_operational_projection_are_rejected(field):
    sale = event()
    sale["payload"][field] = "valid" if field == "data_quality_status" else "10"
    replay = Replay()
    assert replay.consume(record(sale))["reason"] == "invalid_sales_contract"
    assert not replay.facts


def test_capture_rewrite_clock_and_progress_failure_leave_state_unchanged():
    replay = Replay()
    replay.consume(record(event()))
    before = replay.snapshot()
    for raw in (
        record(event(sale="other"), 0),
        progress(after=1),
        record(event(sale="other"), 1, "2026-07-02T09:00:00+00:00"),
    ):
        with pytest.raises(SnapshotError):
            replay.consume(raw)
        assert replay.snapshot() == before
    forged = deepcopy(record(event(sale="forged"), 1))
    forged["offset"] = 0
    with pytest.raises(SnapshotError, match="identity"):
        replay.consume(forged)
    raw = record(event(sale="truth"), 1)
    raw["simulation_truth"] = {}
    with pytest.raises(ValidationError):
        replay.consume(raw)


def test_source_mismatch_is_a_quality_fault_and_cannot_become_a_drop():
    replay = Replay(expected_facts={})
    assert replay.consume(record(event()))["reason"] == "canonical_source_fact_mismatch"
    assert replay.snapshot()["final_aggregates"] == []
    assert replay.snapshot()["report"]["curated_completeness"] == "not_qualified"


def test_progress_at_exact_frontier_and_utc_are_enforced():
    replay = Replay()
    replay.consume(record(event()))
    replay.consume(
        progress(through="2026-07-02T09:00:00+00:00", received="2026-07-02T10:00:00+00:00")
    )
    assert replay.consume(record(event(sale="equal"), 1))["reason"] == "late"
    assert stamp("2026-07-02T10:00:00Z") == stamp("2026-07-02T10:00:00+00:00")
    with pytest.raises(ValueError):
        stamp("2026-07-02T11:00:00+01:00")
