"""Whole real native captures, global identities and fatal resource boundaries."""

import hashlib
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_full_raw_dq import delivery, progress, seal
from test_full_raw_dq import prepared as prepared

from retailops_ai.evaluation_campaign.campaign_anomaly_replay import (
    CAPTURE_VERSION,
    CampaignAnomalyDiskReplay,
    CampaignAnomalyReplayPlan,
    CampaignAnomalyReplayResourceError,
    CampaignAnomalyReplayStateError,
)
from retailops_ai.full_raw_dq.contract import parse_capture
from retailops_ai.full_raw_dq.replay import GRAIN, Replay
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, decode_json


def plan(parent, records, **overrides):
    return CampaignAnomalyReplayPlan(
        **{
            "capture_sha256": hashlib.sha256(
                b"".join(canonical_json(row) + b"\n" for row in records)
            ).hexdigest(),
            "capture_version": records[0]["contract_version"],
            "capture_records": len(records),
            "parent_events": len(parent.facts),
            "max_index_bytes": 64 * 1024**2,
            **overrides,
        }
    )


def project(record):
    return seal({**record, "contract_version": CAPTURE_VERSION})


OUTPUTS = {
    "receipts": "receipts",
    "facts": "accepted_facts",
    "revisions": "aggregate_revisions",
    "progress": "progress",
    "quarantine": "quarantine",
}


def test_legacy_record_limit_is_checked_before_any_capture_consumption():
    with pytest.raises(ValidationError, match="legacy_record_budget"):
        CampaignAnomalyReplayPlan(
            capture_sha256="a" * 64,
            capture_version="raw-dq-capture-2.0.0",
            capture_records=16385,
            parent_events=1,
            max_index_bytes=64 * 1024**2,
        )


@pytest.mark.parametrize("transaction_records", [1, 256])
def test_whole_real_source_capture_matches_unchanged_native_replay(
    prepared, tmp_path, transaction_records
):
    parent = prepared["parent"]
    records = [decode_json(raw) for raw in prepared["raw"].splitlines()]
    native = prepared["replay"].snapshot()
    configuration = plan(parent, records, transaction_records=transaction_records)
    with CampaignAnomalyDiskReplay(parent, configuration, tmp_path) as replay:
        for row, expected in zip(records, native["receipts"], strict=True):
            assert replay.consume(row) == expected
        with pytest.raises(SnapshotError, match="state_unavailable"):
            list(replay.rows("facts"))
        result = replay.finish()
        for table, reference in OUTPUTS.items():
            assert canonical_json(list(replay.rows(table))) == canonical_json(native[reference])
        report = native["report"]
        for name in (
            "raw_events",
            "missing_parent_facts",
            "declared_source_watermark",
            "max_accepted_event_time",
        ):
            assert result[name] == report[name]
        assert (
            result["accepted_parent_facts"]
            == report["accepted_sales"] + report["accepted_return_claims"]
        )
        assert result["unique_receipts"] == report["input_records"]
        assert (
            dict(
                replay._db().execute("SELECT identifier,digest FROM event_hashes ORDER BY sequence")
            )
            == prepared["replay"].events
        )
        assert (
            result["output_sha256"]["event_hashes"]
            == hashlib.sha256(
                b"".join(
                    canonical_json((i, key, value)) + b"\n"
                    for i, (key, value) in enumerate(prepared["replay"].events.items(), 1)
                )
            ).hexdigest()
        )
        assert result["actions"] == {
            action: report[action]
            for action in ("accepted", "duplicate_event", "duplicate_business", "quarantined")
        } | {"progress": report["progress_declarations"]}
        assert report["late"] == report["out_of_order"] == report["duplicate_business"] == 2
        assert report["missing_parent_facts"] == report["quarantined"] == 6
        assert any(
            f["status"] == "rejected" and f["business_date"] > "2026-07-31"
            for f in replay.rows("facts")
        )
        assert not result["source_parent_verified"]
        assert not result["transport_durability_proven"]
        assert not result["quality_qualified"] and not result["stage_ready"]
        assert result["business_event_day_completeness"] == "not_qualified"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("when", ["before_finish", "after_finish"])
@pytest.mark.parametrize("mutation", ["content", "delete", "extra", "sequence"])
def test_global_event_identity_corruption_blocks_complete_replay(
    prepared, tmp_path, when, mutation
):
    parent = prepared["parent"]
    records = [decode_json(raw) for raw in prepared["raw"].splitlines()]
    with pytest.raises(CampaignAnomalyReplayStateError, match="private_output_changed"):
        with CampaignAnomalyDiskReplay(parent, plan(parent, records), tmp_path) as replay:
            for record in records:
                replay.consume(record)
            if when == "after_finish":
                replay.finish()
            database = replay._db()
            if mutation == "content":
                database.execute("UPDATE event_hashes SET digest=? WHERE sequence=1", ("0" * 64,))
            elif mutation == "delete":
                database.execute("DELETE FROM event_hashes WHERE sequence=1")
            elif mutation == "extra":
                database.execute(
                    "INSERT INTO event_hashes(sequence,identifier,digest) VALUES(?,?,?)",
                    (100000, "00000000-0000-0000-0000-000000000999", "0" * 64),
                )
            else:
                database.execute("UPDATE event_hashes SET sequence=100000 WHERE sequence=1")
            # Public receipts/facts are unchanged. The independent native-ID
            # insertion trace detects a gap in state those outputs do not bind.
            if when == "before_finish":
                replay.finish()
    assert not list(tmp_path.iterdir())


def test_fact_lookup_uses_exact_native_delivery_availability(prepared, tmp_path):
    parent = prepared["parent"]
    records = [delivery(parent.events[0])]
    with CampaignAnomalyDiskReplay(parent, plan(parent, records), tmp_path) as replay:
        replay.consume(records[0])
        replay.finish()
        fact = next(replay.rows("facts"))
        at = stamp(fact["available_at"])
        assert replay.accepted_fact(fact["event_type"], fact["business_id"], at.isoformat()) == fact
        assert (
            replay.accepted_fact(
                fact["event_type"],
                fact["business_id"],
                (at - timedelta(microseconds=1)).isoformat(),
            )
            is None
        )
        assert replay.accepted_fact("sale_completed", "not-in-parent", at.isoformat()) is None
        with pytest.raises(SnapshotError, match="utc_cutoff_required"):
            replay.accepted_fact(
                fact["event_type"], fact["business_id"], "2026-10-01T01:00:00+01:00"
            )


def test_explicit_project_capture_keeps_all_global_offsets_above_legacy_caps(prepared, tmp_path):
    parent = prepared["parent"]
    count = 20000

    def records():
        for offset in range(count):
            yield project(delivery(parent.events[0], offset))

    digest = hashlib.sha256()
    for row in records():
        digest.update(canonical_json(row) + b"\n")
    configuration = CampaignAnomalyReplayPlan(
        capture_sha256=digest.hexdigest(),
        capture_records=count,
        parent_events=len(parent.facts),
        max_index_bytes=64 * 1024**2,
    )
    # Closing AI 07 limits are not raised or bypassed.
    with pytest.raises(ValidationError):
        parse_capture(delivery(parent.events[0], 8192))
    with CampaignAnomalyDiskReplay(parent, configuration, tmp_path) as replay:
        for offset, row in enumerate(records()):
            assert replay.consume(row)["action"] == (
                "accepted" if offset == 0 else "duplicate_event"
            )
        result = replay.finish()
        assert (
            result["capture_records"] == result["unique_receipts"] == result["raw_events"] == count
        )
        assert result["actions"] == {"accepted": 1, "duplicate_event": count - 1}
        assert result["missing_parent_facts"] == len(parent.facts) - 1
        assert sum(1 for _ in replay.rows("receipts")) == count
        assert len(list(replay.rows("facts"))) == len(list(replay.rows("revisions"))) == 1
        assert not result["source_parent_verified"] and not result["stage_ready"]


def test_repeated_record_is_idempotent_without_regressing_native_transport(prepared, tmp_path):
    parent = prepared["parent"]
    records = [delivery(parent.events[0]), progress(after=0), delivery(parent.events[0])]
    native = Replay(parent)
    expected = [native.consume(row) for row in records]
    with CampaignAnomalyDiskReplay(parent, plan(parent, records), tmp_path) as replay:
        for row, receipt in zip(records, expected, strict=True):
            returned = replay.consume(row)
            assert returned == receipt
            returned["action"] = "caller_mutation"
        result = replay.finish()
        assert result["capture_records"] == 3 and result["unique_receipts"] == 2
        for table, reference in OUTPUTS.items():
            assert list(replay.rows(table)) == native.snapshot()[reference]


@pytest.mark.parametrize(
    "failure",
    [
        "boolean_partition",
        "wrong_version",
        "wrong_identity",
        "offset_gap",
        "time_regression",
        "progress_position",
        "frontier_regression",
    ],
)
def test_bad_capture_fails_closed_instead_of_publishing_partial_outputs(
    prepared, tmp_path, failure
):
    parent = prepared["parent"]
    first = project(delivery(parent.events[0]))
    bad = project(delivery(parent.events[0], 1))
    if failure == "boolean_partition":
        bad["partition"] = False
    elif failure == "wrong_version":
        bad = delivery(parent.events[0], 1)
    elif failure == "wrong_identity":
        bad["record_id"] = "raw-record-sha256-" + "0" * 64
    elif failure == "offset_gap":
        bad = project(delivery(parent.events[0], 2))
    elif failure == "time_regression":
        bad = project(delivery(parent.events[0], 1, received="2026-09-30T00:00:00+00:00"))
    elif failure == "progress_position":
        bad = project(progress(after=-1))
    else:
        first = project(progress())
        bad = project(progress())
        bad = seal({**bad, "received_at": "2026-10-02T00:00:00+00:00"})
    replay = CampaignAnomalyDiskReplay(parent, plan(parent, [first, bad]), tmp_path).__enter__()
    try:
        replay.consume(first)
        with pytest.raises((SnapshotError, ValidationError)):
            replay.consume(bad)
        with pytest.raises(SnapshotError, match="state_unavailable"):
            replay.finish()
        with pytest.raises(SnapshotError, match="state_unavailable"):
            list(replay.rows("facts"))
    finally:
        replay.close()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["missing", "wrong_hash", "extra"])
def test_exact_preregistered_hash_and_count_are_required(prepared, tmp_path, failure):
    parent = prepared["parent"]
    record = delivery(parent.events[0])
    configuration = plan(
        parent, [record], **({"capture_sha256": "f" * 64} if failure == "wrong_hash" else {})
    )
    replay = CampaignAnomalyDiskReplay(parent, configuration, tmp_path).__enter__()
    try:
        if failure != "missing":
            replay.consume(record)
        with pytest.raises(
            SnapshotError, match="extra_capture_record" if failure == "extra" else "extent_or_hash"
        ):
            replay.consume(record) if failure == "extra" else replay.finish()
        with pytest.raises(SnapshotError, match="state_unavailable"):
            list(replay.rows("receipts"))
    finally:
        replay.close()


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_current_accepted_grain_must_fit_budget_not_only_its_prior_state(
    prepared, tmp_path, budget
):
    parent = prepared["parent"]
    grains = {}
    for event in parent.events:
        key, fact = parent.match(event)
        grain = tuple(fact[name] for name in GRAIN)
        grains.setdefault(grain, []).append(event)
    pair = next(events[:2] for events in grains.values() if len(events) >= 2)
    records = [delivery(event, offset) for offset, event in enumerate(pair)]
    native = Replay(parent)
    for row in records:
        assert native.consume(row)["action"] == "accepted"
    native_size = sum(len(canonical_json(row)) for row in native.facts)
    overrides = (
        {"max_group_rows": 1}
        if budget == "rows"
        else {"max_group_bytes": max(1024, native_size - 1)}
    )
    assert native_size > 1024
    replay = CampaignAnomalyDiskReplay(
        parent, plan(parent, records, **overrides), tmp_path
    ).__enter__()
    try:
        replay.consume(records[0])
        with pytest.raises(CampaignAnomalyReplayResourceError, match="native_group_budget"):
            replay.consume(records[1])
        with pytest.raises(SnapshotError, match="state_unavailable"):
            replay.finish()
    finally:
        replay.close()


def test_dirty_database_growth_counts_before_transaction_commit(prepared, tmp_path):
    parent = prepared["parent"]
    records = [delivery(parent.events[0], offset) for offset in range(512)]
    replay = CampaignAnomalyDiskReplay(
        parent,
        plan(parent, records, max_index_bytes=128 * 1024, transaction_records=4096),
        tmp_path,
    ).__enter__()
    try:
        with pytest.raises(CampaignAnomalyReplayResourceError, match="index_budget"):
            for row in records:
                replay.consume(row)
        assert replay._calls < replay.plan.transaction_records
        with pytest.raises(SnapshotError, match="state_unavailable"):
            replay.finish()
    finally:
        replay.close()


def test_parent_io_failure_is_fatal_not_native_quarantine(prepared, tmp_path, monkeypatch):
    parent = prepared["parent"]
    record = delivery(parent.events[0])

    def broken(event):
        raise OSError("control_parent_storage_unavailable")

    monkeypatch.setattr(parent, "match", broken)
    replay = CampaignAnomalyDiskReplay(parent, plan(parent, [record]), tmp_path).__enter__()
    try:
        with pytest.raises(OSError, match="parent_storage_unavailable"):
            replay.consume(record)
        with pytest.raises(SnapshotError, match="state_unavailable"):
            list(replay.rows("quarantine"))
    finally:
        replay.close()


def test_single_use_outputs_and_owned_scratch_lifecycle(prepared, tmp_path):
    parent = prepared["parent"]
    record = delivery(parent.events[0])
    configuration = plan(parent, [record])
    replay = CampaignAnomalyDiskReplay(parent, configuration, tmp_path)
    with replay:
        replay.consume(record)
        replay.finish()
        with pytest.raises(SnapshotError, match="output_table_invalid"):
            list(replay.rows("facts; DROP TABLE facts"))
        with pytest.raises(SnapshotError, match="already_finished"):
            replay.consume(record)
        with pytest.raises(SnapshotError, match="already_finished"):
            replay.finish()
    with pytest.raises(SnapshotError, match="single_use"):
        replay.__enter__()
    with pytest.raises(SnapshotError, match="state_unavailable"):
        list(replay.rows("facts"))
    with pytest.raises(SnapshotError, match="capture_not_finished"):
        with CampaignAnomalyDiskReplay(parent, configuration, tmp_path):
            pass
    assert not list(tmp_path.iterdir())


def test_declared_parent_count_and_initial_index_budget_fail_before_capture(prepared, tmp_path):
    parent = prepared["parent"]
    record = delivery(parent.events[0])
    for overrides, error, reason in (
        ({"parent_events": len(parent.facts) - 1}, SnapshotError, "parent_count_binding"),
        ({"max_index_bytes": 4096}, CampaignAnomalyReplayResourceError, "index_budget"),
    ):
        with pytest.raises(error, match=reason):
            with CampaignAnomalyDiskReplay(parent, plan(parent, [record], **overrides), tmp_path):
                pass
    assert not list(tmp_path.iterdir())
