"""Native facts, temporal corrections, overlap, poisoned handoffs and atomic candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from retailops_ai.source_replay import (
    Capture,
    Envelope,
    ObservationHistory,
    ObservationVersion,
    Record,
    ReplayError,
    Stream,
)
from retailops_ai.source_replay.wire import canonical

AUTHORITY = str(uuid5(NAMESPACE_URL, "ai10-observation-replay-fixture-only"))
STREAM = Stream(source_authority_id=AUTHORITY, cluster_id="fixture-cluster", topic_id="A/+topic")
DAY = date(2026, 7, 2)
TIME = datetime(2026, 7, 3, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def identifier(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, name))


def row(version=1, units=10, **changes):
    data = {
        "id": identifier(f"row-{version}"),
        "observation_id": identifier("observation"),
        "business_date": DAY,
        "product_id": identifier("product"),
        "selling_location_id": identifier("selling-location"),
        "channel": "store",
        "version": version,
        "observed_units": units,
        "observation_status": "observed_positive" if units else "observed_zero",
        "available_at": TIME + timedelta(hours=version - 1),
        "history_policy_version": "observed-quantity-history-1.0.0",
        **changes,
    }
    return ObservationVersion(**data)


def record(fact, offset=0, partition=0, **changes):
    envelope = Envelope(
        **{
            "source_authority_id": AUTHORITY,
            "event_id": identifier(f"event-{partition}-{offset}"),
            "fact": fact,
            **changes,
        }
    )
    return Record(partition=partition, offset=offset, envelope=envelope)


def state(*records, partitions=2):
    return ObservationHistory(STREAM, partitions=partitions).apply_batch(records, stream=STREAM)


def reseal(document):
    document = {k: v for k, v in document.items() if k != "capture_id"}
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return json.dumps(
        {**document, "capture_id": "observation-capture-sha256-" + hashlib.sha256(raw).hexdigest()}
    ).encode()


def test_corrections_replace_contribution_and_preserve_as_of_history():
    first, second, third = row(), row(2, 4), row(3, 2)
    history = state(record(first), record(third, 1), record(second, 2))
    assert history.quantity_total(TIME - timedelta(microseconds=1)) == 0
    assert history.quantity_total(TIME) == 10
    assert history.quantity_total(TIME + timedelta(hours=1)) == 4
    assert history.quantity_total(TIME + timedelta(hours=2)) == 2
    assert len(history.rows) == 3
    assert history.as_of(TIME + timedelta(days=2))[0].id == third.id


def test_same_fact_new_envelope_and_cross_partition_delivery_have_one_effect():
    fact = row()
    history = state(record(fact), record(fact, 1), record(fact, partition=1))
    assert history.rows == (fact,)
    assert len(history.receipts) == 3
    assert [b.next_offset for b in history.boundaries] == [2, 1]
    assert history.quantity_total(TIME) == 10
    saved = canonical(history.capture())
    assert history.process(record(fact), stream=STREAM) == "duplicate"
    assert canonical(history.capture()) == saved


def test_empty_partitions_are_explicit_and_roundtrip_is_byte_identical():
    history = state(record(row()), partitions=3)
    capture = history.capture()
    assert [b.next_offset for b in capture.boundaries] == [1, 0, 0]
    restored = ObservationHistory.restore(canonical(capture), stream=STREAM, partitions=3)
    assert canonical(restored.capture()) == canonical(capture)
    assert state(partitions=3).capture().rows == ()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"units": 12}, "observation_fact_version_collision"),
        ({"version": 2, "id": identifier("row-1")}, "observation_row_identity_collision"),
        ({"product_id": identifier("other")}, "observation_fact_version_collision"),
        ({"version": 2, "product_id": identifier("other")}, "observation_history_grain_changed"),
        (
            {"observation_id": identifier("other"), "id": identifier("other-row")},
            "observation_natural_grain_collision",
        ),
        (
            {"version": 2, "available_at": TIME - timedelta(seconds=1)},
            "observation_history_availability_regression",
        ),
    ],
)
def test_fact_identity_collisions_do_not_advance_offset(change, reason):
    history = state(record(row()))
    before = canonical(history.capture())
    changed = row(**change)
    with pytest.raises(ReplayError, match=reason):
        history.process(record(changed, 1), stream=STREAM)
    assert canonical(history.capture()) == before


def test_event_identity_collision_across_partitions_is_not_business_dedup():
    original = record(row())
    history = state(original)
    poisoned = Record(
        partition=1,
        offset=0,
        envelope=Envelope(
            source_authority_id=AUTHORITY,
            event_id=original.envelope.event_id,
            fact=row(2),
        ),
    )
    with pytest.raises(ReplayError, match="observation_event_identity_collision"):
        history.process(poisoned, stream=STREAM)
    assert [b.next_offset for b in history.boundaries] == [1, 0]


def test_overlap_must_match_original_envelope_not_only_fact():
    history = state(record(row()))
    overlap = record(row(), event_id=identifier("other-envelope"))
    with pytest.raises(ReplayError, match="observation_overlap_receipt_mismatch"):
        history.process(overlap, stream=STREAM)


@pytest.mark.parametrize(
    "offset,partition,reason", [(2, 0, "offset_gap"), (0, 2, "partition_unknown")]
)
def test_transport_gap_and_unknown_partition_stop_before_mutation(offset, partition, reason):
    history = state(record(row()))
    before = canonical(history.capture())
    with pytest.raises(ReplayError, match=reason):
        history.process(record(row(2), offset, partition), stream=STREAM)
    assert canonical(history.capture()) == before


@pytest.mark.parametrize("field", ["source_authority_id", "cluster_id", "topic_id"])
def test_stream_replacement_requires_resync(field):
    history = state(record(row()))
    changed = STREAM.model_dump()
    changed[field] = identifier("replacement") if field == "source_authority_id" else "replacement"
    replacement = Stream(**changed)
    with pytest.raises(ReplayError, match="stream_identity_changed"):
        history.process(record(row(2), 1), stream=replacement)
    with pytest.raises(ReplayError, match="stream_identity_changed"):
        ObservationHistory.restore(canonical(history.capture()), stream=replacement, partitions=2)


def test_foreign_authority_inside_envelope_is_rejected():
    history = state()
    envelope = Envelope(
        source_authority_id=identifier("foreign"), event_id=identifier("e"), fact=row()
    )
    with pytest.raises(ReplayError, match="source_authority_changed"):
        history.process(Record(partition=0, offset=0, envelope=envelope), stream=STREAM)
    assert history.rows == () and not history.receipts


def test_failed_batch_does_not_publish_partially_applied_candidate():
    history = state(record(row()))
    before = canonical(history.capture())
    with pytest.raises(ReplayError, match="offset_gap"):
        history.apply_batch([record(row(2), 1), record(row(3), 3)], stream=STREAM)
    assert canonical(history.capture()) == before
    with pytest.raises(ReplayError, match="version_gap"):
        history.apply_batch([record(row(3), 1)], stream=STREAM)
    assert canonical(history.capture()) == before


def test_out_of_order_history_is_retained_but_cannot_be_read_or_captured_until_complete():
    history = ObservationHistory(STREAM, partitions=1)
    history.process(record(row(3)), stream=STREAM)
    with pytest.raises(ReplayError, match="version_gap"):
        history.capture()
    with pytest.raises(ReplayError, match="version_gap"):
        history.as_of(TIME + timedelta(days=1))
    history.process(record(row(), 1), stream=STREAM)
    history.process(record(row(2), 2), stream=STREAM)
    assert history.quantity_total(TIME + timedelta(days=1)) == 10


def test_closed_zero_and_missing_are_distinct_and_future_availability_is_not_leaked():
    history = state(
        record(row()),
        record(row(2, 0, observation_status="closed"), 1),
        record(row(3, None, observation_status="missing"), 2),
    )
    assert history.quantity_total(TIME) == 10
    assert history.as_of(TIME + timedelta(hours=1))[0].observation_status == "closed"
    assert history.quantity_total(TIME + timedelta(hours=1)) == 0
    assert history.as_of(TIME + timedelta(hours=2))[0].observed_units is None
    with pytest.raises(ReplayError, match="quantity_unknown"):
        history.quantity_total(TIME + timedelta(hours=2))


@pytest.mark.parametrize(
    "origin", [TIME.replace(tzinfo=None), TIME.replace(tzinfo=timezone(timedelta(hours=1)))]
)
def test_as_of_requires_explicit_utc(origin):
    with pytest.raises(ReplayError, match="origin_requires_utc"):
        state().as_of(origin)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 0},
        {"observed_units": True},
        {"observed_units": -1},
        {"observation_status": "observed_zero"},
        {"observation_status": "missing"},
        {"channel": "all"},
        {"store_id": identifier("invented-grain")},
        {"available_at": TIME.replace(tzinfo=None)},
        {"history_policy_version": "unknown"},
    ],
)
def test_native_row_types_and_semantics_are_closed(change):
    with pytest.raises(ValidationError):
        row(**change)


@pytest.mark.parametrize(
    "mutation", ["offset", "missing_partition", "receipt", "unbound_row", "hash", "extra", "order"]
)
def test_resealed_poisoned_capture_is_not_authoritative(mutation):
    history = state(record(row()), record(row(2), 1))
    document = history.capture().model_dump(mode="json")
    if mutation == "offset":
        document["boundaries"][0]["next_offset"] += 1
    elif mutation == "missing_partition":
        document["boundaries"][0]["partition"] = 2
    elif mutation == "receipt":
        document["receipts"][0]["event_sha256"] = "0" * 64
    elif mutation == "unbound_row":
        document["rows"].append(row(3).model_dump(mode="json"))
    elif mutation == "hash":
        document["rows"][0]["observed_units"] = 900
        raw = json.dumps(document).encode()
    elif mutation == "extra":
        document["snapshot_replay_handoff"] = True
    else:
        document["receipts"].reverse()
    if mutation != "hash":
        raw = reseal(document)
    with pytest.raises(ReplayError, match="capture_rejected"):
        ObservationHistory.restore(raw, stream=STREAM, partitions=2)


def test_json_duplicates_and_byte_limits_are_rejected(monkeypatch):
    import retailops_ai.source_replay.history as module

    raw = canonical(state().capture())
    duplicate = raw.replace(b'"table":', b'"table":"daily_demand_versions","table":', 1)
    with pytest.raises(ReplayError, match="capture_rejected"):
        ObservationHistory.restore(duplicate, stream=STREAM, partitions=2)
    monkeypatch.setattr(module, "MAX_CAPTURE_BYTES", len(raw) - 1)
    with pytest.raises(ReplayError, match="capture_size_limit"):
        ObservationHistory.restore(raw, stream=STREAM, partitions=2)


@pytest.mark.parametrize("limit", ["MAX_FACTS", "MAX_RECEIPTS"])
def test_candidate_limits_fail_before_any_mutation(monkeypatch, limit):
    import retailops_ai.source_replay.history as module

    history = state(record(row()))
    before = canonical(history.capture())
    monkeypatch.setattr(module, limit, 1)
    with pytest.raises(ReplayError, match="candidate_size_limit"):
        history.process(record(row(2), 1), stream=STREAM)
    assert canonical(history.capture()) == before


def test_original_native_snapshot_remains_explicitly_without_handoff():
    from retailops_ai.source_bundle.wire import BundleManifest

    assert BundleManifest.model_fields["replay_handoff"].default is False
    assert Capture.model_fields["table"].default == "daily_demand_versions"


def test_native_smoke_snapshot_overlap_matches_full_replay_and_curated_as_of(tmp_path):
    from retailops_ai.source_replay.drill import run_drill

    result = run_drill(ROOT / "data/fixtures/ai-smoke-v1/snapshot", tmp_path.resolve())
    assert result["status"] == "passed"
    assert result["native_versions"] == 1612
    assert result["captured_native_versions"] == 806
    assert result["source_snapshot_version"] == "1.0.0"
    assert result["overlap_full_replay_equal"] is True
    assert result["business_dedup_and_correction_equal"] is True
    assert result["curated_as_of_equal"] is True
    assert result["source_live_capture_supported"] is False
    assert result["broker_ack_performed"] is False


def test_replay_batch_of_repeated_old_offsets_is_also_bounded(monkeypatch):
    import retailops_ai.source_replay.history as module

    history = state(record(row()))
    before = canonical(history.capture())
    monkeypatch.setattr(module, "MAX_RECEIPTS", 1)
    with pytest.raises(ReplayError, match="replay_batch_size_limit"):
        history.apply_batch([record(row())] * 3, stream=STREAM)
    assert canonical(history.capture()) == before


@pytest.mark.parametrize(
    "change", [{"version": "source-observation-event-2.0"}, {"table": "sales"}, {"unknown": 1}]
)
def test_unknown_protocol_major_table_and_fields_are_rejected(change):
    with pytest.raises(ValidationError):
        record(row(), **change)


def test_capture_byte_limit_includes_the_identity_field(monkeypatch):
    import retailops_ai.source_replay.wire as wire

    history = state(record(row()))
    raw = canonical(history.capture())
    monkeypatch.setattr(wire, "MAX_CAPTURE_BYTES", len(raw) - 1)
    with pytest.raises(ValidationError, match="capture_size_limit"):
        history.capture()


def test_removed_empty_partition_requires_resync_even_with_a_valid_new_digest():
    history = state(record(row()))
    document = history.capture().model_dump(mode="json")
    document["boundaries"].pop()
    raw = reseal(document)
    # It is internally coherent, but it disagrees with the trusted topology.
    Capture.model_validate_json(raw)
    with pytest.raises(ReplayError, match="partition_vector_changed"):
        ObservationHistory.restore(raw, stream=STREAM, partitions=2)


@pytest.mark.parametrize("partitions", [True, 0, 33])
def test_restore_requires_valid_trusted_partition_count(partitions):
    with pytest.raises(ReplayError, match="invalid_partition_count"):
        ObservationHistory.restore(
            canonical(state().capture()), stream=STREAM, partitions=partitions
        )
