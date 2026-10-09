"""Complete real public days/facts, causal native parity and private-state failure."""

import hashlib
from copy import deepcopy
from datetime import timedelta

import pytest
from test_campaign_anomaly_days import day_case  # noqa: F401
from test_campaign_anomaly_days import project as project_days
from test_campaign_anomaly_parent import public_parent  # noqa: F401
from test_full_raw_dq import delivery

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.contract import GRAIN
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.evaluation_campaign.campaign_anomaly_day_gate import (
    CampaignAnomalyDayGatePlan,
    CampaignAnomalyDiskDayGate,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_replay import (
    CampaignAnomalyDiskReplay,
    CampaignAnomalyReplayPlan,
    CampaignAnomalyReplayStateError,
)
from retailops_ai.full_raw_dq.replay import Replay
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json


def replay_plan(parent, records):
    return CampaignAnomalyReplayPlan(
        capture_version="raw-dq-capture-2.0.0",
        capture_sha256=hashlib.sha256(
            b"".join(canonical_json(r) + b"\n" for r in records)
        ).hexdigest(),
        capture_records=len(records),
        parent_events=len(parent.facts),
        max_index_bytes=128 * 1024**2,
    )


def gate_plan(days, replay, **updates):
    return CampaignAnomalyDayGatePlan(
        **{
            "day_plan_sha256": canonical_sha256(days.plan.model_dump(mode="json")),
            "native_days_sha256": days.native_days_sha256,
            "replay_plan_sha256": canonical_sha256(replay.plan.model_dump(mode="json")),
            "accepted_facts": sum(1 for _ in replay.rows("facts")),
            "quarantined_records": sum(1 for _ in replay.rows("quarantine")),
            **updates,
        }
    )


def captured(native_parent, mode):
    records = []
    first = deepcopy(native_parent.events[0])
    if mode == "repair":
        invalid = deepcopy(first)
        invalid["payload"]["quantity"] = "bad"
        records.append(delivery(invalid, 0, received="2026-09-30T00:00:00+00:00"))
    for event in native_parent.events:
        if mode == "missing" and event == first:
            continue
        records.append(delivery(event, len(records)))
    if mode.startswith("unknown") or mode == "attributed":
        invalid = deepcopy(first)
        invalid["payload"]["quantity"] = "bad"
        if mode == "unknown_json":
            body = "{invalid"
        else:
            if mode == "unknown_uuid":
                invalid["event_id"] = native_parent.events[-1]["event_id"]
            elif mode == "unknown_business_list":
                field = "sale_id" if first["event_type"] == "sale_completed" else "return_id"
                invalid["payload"][field] = []
            elif mode == "unknown_uuid_dict":
                invalid["event_id"] = {}
            body = canonical_json(invalid).decode()
        records.append(
            delivery(None, len(records), received="2026-10-02T00:00:00+00:00", body=body)
        )
    return records


@pytest.mark.parametrize(
    "mode",
    [
        "complete",
        "missing",
        "repair",
        "attributed",
        "unknown_json",
        "unknown_uuid",
        "unknown_business_list",
        "unknown_uuid_dict",
    ],
)
def test_all_native_day_points_equal_original_with_causal_global_quarantine(
    day_case,  # noqa: F811 - imported parametrized fixture
    public_parent,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    mode,  # noqa: F811 - imported parametrized fixtures
):
    parent, expected_days, _ = day_case
    native_parent = public_parent[2]
    records = captured(native_parent, mode)
    baseline = Replay(native_parent)
    for record in records:
        baseline.consume(record)
    raw = b"".join(canonical_json(r) + b"\n" for r in records)
    original = DayGate(expected_days, baseline.snapshot(), raw, native_parent)
    with project_days(day_case, tmp_path) as days:
        with CampaignAnomalyDiskReplay(
            parent.parent, replay_plan(parent.parent, records), tmp_path
        ) as replay:
            for record in records:
                replay.consume(record)
            replay.finish()
            quarantined = list(replay.quarantined_captures())
            assert [q for q, _ in quarantined] == baseline.snapshot()["quarantine"]
            assert [r for _, r in quarantined] == [
                next(r for r in records if r["record_id"] == q["raw_ref"])
                for q in baseline.snapshot()["quarantine"]
            ]
            adapter = CampaignAnomalyDiskDayGate(days, replay, gate_plan(days, replay))
            with adapter:
                assert len(adapter.days) == len(expected_days)
                assert dict(adapter.accepted) == original.accepted
                for day in expected_days:
                    grain = tuple(getattr(day, key) for key in GRAIN)
                    cutoffs = [
                        (stamp(day.known_at) - timedelta(microseconds=1)).isoformat(),
                        day.known_at,
                        "2026-10-01T00:00:00+00:00",
                        "2026-10-02T00:00:00+00:00",
                    ]
                    for cutoff in cutoffs:
                        assert adapter.point(grain, cutoff) == original.point(grain, cutoff)
                grain = ("sale_completed", "1900-01-01", *next(iter(adapter.days))[2:])
                assert adapter.point(grain, "2026-10-03T00:00:00+00:00").status == "no_declaration"
                with pytest.raises(SnapshotError, match="not_completed"):
                    adapter.receipt()
            receipt = adapter.receipt()
            assert receipt["unattributed_records"] == len(original.unattributed)
            assert receipt["earliest_unattributed_received_at"] == (
                min(original.unattributed, key=stamp) if original.unattributed else None
            )
            assert receipt["native_causal_day_queries_verified"]
            assert receipt["outer_day_replay_and_public_parent_completion_required"]
            assert not receipt["producer_closure_policy_verified"]
            assert receipt["business_event_day_completeness"] == "not_qualified"
            assert not receipt["quality_qualified"] and not receipt["stage_ready"]
            with pytest.raises(RuntimeError, match="state_unavailable"):
                adapter.point(grain, "2026-10-03T00:00:00+00:00")
            with pytest.raises(SnapshotError, match="single_use"):
                with adapter:
                    pass
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field",
    [
        "day_plan_sha256",
        "native_days_sha256",
        "replay_plan_sha256",
        "accepted_facts",
        "quarantined_records",
    ],
)
def test_wrong_complete_bindings_reject_gate(day_case, tmp_path, field):  # noqa: F811
    parent = day_case[0]
    records = [delivery(parent.parent.events[0])]
    with project_days(day_case, tmp_path) as days:
        with CampaignAnomalyDiskReplay(
            parent.parent, replay_plan(parent.parent, records), tmp_path
        ) as replay:
            replay.consume(records[0])
            replay.finish()
            updates = {field: "0" * 64} if field.endswith("sha256") else {field: 2}
            adapter = CampaignAnomalyDiskDayGate(days, replay, gate_plan(days, replay, **updates))
            with pytest.raises(SnapshotError, match="binding"):
                with adapter:
                    pass
            with pytest.raises(SnapshotError, match="not_completed"):
                adapter.receipt()


@pytest.mark.parametrize(
    "tamper", ["fact", "resealed_fact", "fact_key", "capture", "resealed_capture", "receipt_delete"]
)
def test_private_replay_changes_are_fatal_not_quarantine_or_accepted_output(
    day_case,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    tamper,  # noqa: F811
):
    parent = day_case[0]
    event = deepcopy(parent.parent.events[0])
    event["payload"]["quantity"] = "bad"
    records = [delivery(parent.parent.events[0]), delivery(event, 1)]
    replay = CampaignAnomalyDiskReplay(parent.parent, replay_plan(parent.parent, records), tmp_path)
    with pytest.raises(CampaignAnomalyReplayStateError, match="private_"):
        with replay:
            for record in records:
                replay.consume(record)
            replay.finish()
            if tamper == "receipt_delete":
                replay._db().execute("DELETE FROM receipts WHERE sequence=1")
            elif tamper == "fact_key":
                replay._db().execute("UPDATE facts SET business_id='wrong'")
            elif "fact" in tamper:
                value = next(replay.rows("facts"))
                value["quantity"] += 1
                raw = canonical_json(value)
                if tamper == "resealed_fact":
                    replay._db().execute(
                        "UPDATE facts SET payload=?,digest=?",
                        (raw, hashlib.sha256(raw).hexdigest()),
                    )
                else:
                    replay._db().execute("UPDATE facts SET payload=?", (raw,))
            else:
                row, capture = next(replay.quarantined_captures())
                capture["body_utf8"] = "{}"
                raw = canonical_json(capture)
                if tamper == "resealed_capture":
                    row["body_sha256"] = hashlib.sha256(b"{}").hexdigest()
                    qraw = canonical_json(row)
                    replay._db().execute(
                        "UPDATE quarantine SET payload=?,digest=?,capture=?,capture_digest=?",
                        (
                            qraw,
                            hashlib.sha256(qraw).hexdigest(),
                            raw,
                            hashlib.sha256(raw).hexdigest(),
                        ),
                    )
                else:
                    replay._db().execute("UPDATE quarantine SET capture=?", (raw,))
            replay._db().commit()
    with pytest.raises(SnapshotError, match="state_unavailable"):
        list(replay.rows("facts"))
    assert not list(tmp_path.iterdir())


def test_body_error_never_completes_gate(day_case, tmp_path):  # noqa: F811
    parent = day_case[0]
    records = [delivery(parent.parent.events[0])]
    with project_days(day_case, tmp_path) as days:
        with CampaignAnomalyDiskReplay(
            parent.parent, replay_plan(parent.parent, records), tmp_path
        ) as replay:
            replay.consume(records[0])
            replay.finish()
            adapter = CampaignAnomalyDiskDayGate(days, replay, gate_plan(days, replay))
            with pytest.raises(OSError, match="control_body_failure"):
                with adapter:
                    raise OSError("control_body_failure")
            with pytest.raises(SnapshotError, match="not_completed"):
                adapter.receipt()
