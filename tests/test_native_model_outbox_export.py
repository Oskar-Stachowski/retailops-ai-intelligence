"""Acceptance exports must retain the full original committed result set and honest receipts."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.intelligence_events.acceptance_export import event_document, seal_census


@pytest.fixture(params=["anomaly_detected", "stockout_risk_scored"])
def census(request):
    root = Path(__file__).resolve().parents[1] / "contracts/events/v2"
    event = event_document(json.loads((root / (request.param + ".fixture.json")).read_bytes()))
    item = event.payload.model_dump(mode="json")
    row = dict(
        event_id=str(event.event_id),
        event_type=event.event_type,
        result_id=item.get("anomaly_id", item.get("risk_id")),
        environment="test",
        topic=event.topic,
        partition_key=event.partition_key,
        document=event.model_dump(mode="json"),
        delivered_at=None,
        delivered_partition=None,
        delivered_offset=None,
    )
    return event, row


def test_full_original_native_payload_and_identity_are_retained_without_quality_claim(census):
    event, row = census
    files = seal_census((event,), (row,), environment="test")
    assert files["events.jsonl"] == canonical_bytes(event.model_dump(mode="json")) + b"\n"
    receipt = json.loads(files["receipt.json"])
    identity = receipt.pop("census_id")
    assert identity == "native-model-outbox-sha256-" + canonical_sha256(receipt)
    assert receipt["model_quality_attestation"] is False
    assert receipt["transport_status"] == "retained"
    assert receipt["events"]["rows"] == 1
    assert receipt["events"]["sha256"] == hashlib.sha256(files["events.jsonl"]).hexdigest()
    assert receipt["members"][0]["payload_sha256"] == canonical_sha256(row["document"]["payload"])


@pytest.mark.parametrize(
    "change", ["missing", "extra", "identity", "payload", "environment", "key", "topic", "type"]
)
def test_missing_or_substituted_committed_results_cannot_be_exported(census, change):
    event, original = census
    row = copy.deepcopy(original)
    rows = [row]
    if change == "missing":
        rows = []
    elif change == "extra":
        rows = [row, row]
    elif change == "identity":
        row["event_id"] = "11111111-1111-4111-8111-111111111111"
    elif change == "payload":
        row["document"]["payload"]["generated_at"] = "2026-10-07T01:00:00Z"
    elif change == "environment":
        row["environment"] = "local"
    elif change == "key":
        row["partition_key"] = "f" * 64
    elif change == "topic":
        row["topic"] = "retailops.events.v1"
    else:
        row["event_type"] = "forecast_generated"
    with pytest.raises(ValueError):
        seal_census((event,), rows, environment="test")


@pytest.mark.parametrize("change", ["partition_only", "time_only", "negative_offset", "naive_time"])
def test_partial_or_impossible_delivery_receipts_are_refused(census, change):
    event, row = census
    if change == "partition_only":
        row["delivered_partition"] = 0
    else:
        row["delivered_at"] = datetime(2026, 10, 7, tzinfo=UTC)
        if change != "time_only":
            row.update(
                delivered_partition=0, delivered_offset=-1 if change == "negative_offset" else 0
            )
        if change == "naive_time":
            row["delivered_at"] = datetime(2026, 10, 7)
    with pytest.raises(ValueError, match="partial_delivery_receipt"):
        seal_census((event,), (row,), environment="test")


def test_original_broker_position_is_preserved_when_committed(census):
    event, row = census
    row.update(
        delivered_at=datetime(2026, 10, 7, tzinfo=UTC), delivered_partition=2, delivered_offset=13
    )
    receipt = json.loads(seal_census((event,), (row,), environment="test")["receipt.json"])
    assert receipt["transport_status"] == "delivered"
    assert receipt["members"][0]["delivered_partition"] == 2
    assert receipt["members"][0]["delivered_offset"] == 13


def test_duplicate_expected_results_cannot_hide_a_missing_result(census):
    event, row = census
    with pytest.raises(ValueError, match="incomplete_census"):
        seal_census((event, event), (row, row), environment="test")
