"""Complete real Source point parity, original causal semantics and fatal corruption."""

import hashlib
import zlib
from contextlib import contextmanager
from copy import deepcopy

import pytest
from test_campaign_anomaly_day_gate import gate_plan, replay_plan
from test_campaign_anomaly_days import day_case, project  # noqa: F401
from test_campaign_anomaly_parent import public_parent  # noqa: F401
from test_full_raw_dq import delivery

from retailops_ai.anomaly_detectors.protocol import point_key
from retailops_ai.curated.builder import iter_rows
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.evaluation_campaign.campaign_anomaly_day_gate import CampaignAnomalyDiskDayGate
from retailops_ai.evaluation_campaign.campaign_anomaly_features import (
    CampaignAnomalyFeaturePlan,
    CampaignAnomalyFeatureProjection,
    CampaignAnomalyFeatureStateError,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_replay import CampaignAnomalyDiskReplay
from retailops_ai.full_raw_dq.replay import Replay
from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.qualified_anomalies.features import TABLES, Features, model_row
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json


def feature_plan(gate, **updates):
    return CampaignAnomalyFeaturePlan(
        **{
            "day_gate_plan_sha256": canonical_sha256(gate.plan.model_dump(mode="json")),
            "native_days_sha256": gate.projection.native_days_sha256,
            "expected_points": len(gate.days),
            "max_index_bytes": 128 * 1024**2,
            **updates,
        }
    )


@contextmanager
def open_gate(case, scratch, mode="causal"):
    parent, expected_days, manifest = case
    # Real earliest available parent facts, sorted by actual receipt time.
    events = sorted(
        parent.parent.events, key=lambda e: stamp(parent.parent.match(e)[1]["ingested_at"])
    )
    records = [
        delivery(e, i, received=parent.parent.match(e)[1]["ingested_at"])
        for i, e in enumerate(events)
    ]
    if mode == "missing":
        records.pop()
    elif mode == "late":
        records = [
            delivery(e, i, received="2026-10-01T00:00:00+00:00") for i, e in enumerate(events)
        ]
    elif mode == "quarantine":
        records.append(
            delivery(None, len(records), received="2026-10-01T00:00:00+00:00", body="{invalid")
        )
    baseline = Replay(parent.parent)
    for record in records:
        baseline.consume(record)
    raw = b"".join(canonical_json(r) + b"\n" for r in records)
    original = DayGate(expected_days, baseline.snapshot(), raw, parent.parent)
    tables = {
        name: list(iter_rows(parent._replay.curated, spec["files"], 256))
        for name in TABLES
        for spec in manifest["tables"]
        if spec["table"] == name
    }
    with project(case, scratch) as days:
        with CampaignAnomalyDiskReplay(
            parent.parent, replay_plan(parent.parent, records), scratch
        ) as replay:
            for record in records:
                replay.consume(record)
            replay.finish()
            with CampaignAnomalyDiskDayGate(days, replay, gate_plan(days, replay)) as gate:
                yield gate, original, tables


@pytest.mark.parametrize("mode", ["causal", "missing", "late", "quarantine"])
@pytest.mark.parametrize("policy", [Policy(), Policy(sales_delay_hours=0, returns_delay_hours=0)])
def test_all_public_source_native_points_history_context_and_model_rows_equal_original(
    day_case,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    mode,
    policy,  # noqa: F811 - imported parametrized fixture
):
    with open_gate(day_case, tmp_path, mode) as (gate, original, tables):
        expected = sorted(Features(original, tables, policy).rows(), key=point_key)
        adapter = CampaignAnomalyFeatureProjection(
            gate, feature_plan(gate, policy=policy), tmp_path
        )
        with adapter:
            actual = list(adapter.points())
            assert actual == expected
            assert [model_row(p) for p in actual] == [model_row(p) for p in expected]
            assert len(actual) == len(gate.days)
            assert all(len(p.history) == 28 for p in actual)
            assert {p.event_type for p in actual} == {"sale_completed", "return_completed"}
            assert adapter.stats["stored_context_rows"] == sum(map(len, tables.values()))
            assert adapter.stats["maximum_context_rows"] <= adapter.plan.max_context_rows
            assert adapter.stats["maximum_context_bytes"] <= adapter.plan.max_context_bytes
            assert (
                adapter.stats["compressed_point_payload_bytes"]
                < adapter.stats["point_payload_bytes"]
            )
            assert adapter._db().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall() == [("points",)]
            assert adapter.path.stat().st_mode & 0o777 == 0o600
            assert adapter.path.parent.stat().st_mode & 0o777 == 0o700
            with pytest.raises(SnapshotError, match="not_completed"):
                adapter.receipt()
        receipt = adapter.receipt()
        assert (
            receipt["native_points_sha256"]
            == hashlib.sha256(
                b"".join(canonical_json(p.model_dump(mode="json")) + b"\n" for p in expected)
            ).hexdigest()
        )
        assert receipt["complete_native_point_census_passed"]
        assert receipt["outer_gate_days_replay_public_parent_completion_required"]
        assert not receipt["audited_read_authorization_proven"]
        assert not receipt["quality_qualified"] and not receipt["stage_ready"]
        with pytest.raises(RuntimeError, match="state_unavailable"):
            list(adapter.points())
        with pytest.raises(SnapshotError, match="single_use"):
            with adapter:
                pass
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"day_gate_plan_sha256": "0" * 64}, "complete_parent_binding"),
        ({"native_days_sha256": "0" * 64}, "complete_parent_binding"),
        ({"expected_points": 1}, "complete_parent_binding"),
        ({"max_index_bytes": 4096}, "combined_index_budget"),
        ({"max_context_rows": 1}, "complete_context_budget"),
        ({"max_point_bytes": 4096}, "point_budget"),
    ],
)
def test_bindings_and_budgets_fail_whole_projection_without_smaller_output(
    day_case,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    updates,
    reason,  # noqa: F811
):
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate, **updates), tmp_path)
        with pytest.raises(SnapshotError, match=reason):
            with adapter:
                pass
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["payload", "resealed", "key", "delete"])
def test_private_point_corruption_blocks_completed_receipt(
    day_case,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    mutation,  # noqa: F811
):
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate), tmp_path)
        with pytest.raises(CampaignAnomalyFeatureStateError, match="private_"):
            with adapter:
                point = next(adapter.points())
                if mutation == "delete":
                    adapter._db().execute(
                        "DELETE FROM points WHERE rowid=(SELECT MIN(rowid) FROM points)"
                    )
                elif mutation == "key":
                    adapter._db().execute(
                        "UPDATE points SET product_id='wrong' WHERE rowid=(SELECT MIN(rowid) FROM points)"
                    )
                else:
                    value = deepcopy(point.model_dump(mode="json"))
                    value["context"]["planned_price"] = "999.00"
                    raw = canonical_json(value)
                    payload = zlib.compress(raw, level=1)
                    grain = (
                        point.event_type,
                        point.business_date.isoformat(),
                        point.product_id,
                        point.selling_location_id,
                        point.channel,
                        point.currency,
                    )
                    query = (
                        "UPDATE points SET payload=?,digest=? WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?"
                        if mutation == "resealed"
                        else "UPDATE points SET payload=? WHERE event_type=? AND business_date=? AND product_id=? AND location_id=? AND channel=? AND currency=?"
                    )
                    params = (
                        (payload, hashlib.sha256(raw).hexdigest(), *grain)
                        if mutation == "resealed"
                        else (payload, *grain)
                    )
                    adapter._db().execute(query, params)
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["oversized", "truncated", "trailing", "digest"])
def test_compressed_point_cannot_bypass_native_size_or_checksum_guard(mutation):
    adapter = object.__new__(CampaignAnomalyFeatureProjection)
    adapter.plan = CampaignAnomalyFeaturePlan(
        day_gate_plan_sha256="0" * 64,
        native_days_sha256="0" * 64,
        expected_points=1,
        max_index_bytes=4096,
        max_point_bytes=4096,
    )
    raw = b"a" * (4097 if mutation == "oversized" else 4096)
    payload = zlib.compress(raw)
    digest = hashlib.sha256(raw).hexdigest()
    if mutation == "truncated":
        payload = payload[:-1]
    elif mutation == "trailing":
        payload += b"extra"
    elif mutation == "digest":
        digest = "0" * 64
    with pytest.raises(CampaignAnomalyFeatureStateError, match="private_point_"):
        adapter._point_raw(payload, digest)
    raw = b"a" * 4096
    assert adapter._point_raw(zlib.compress(raw), hashlib.sha256(raw).hexdigest()) == raw


def test_later_context_reseal_during_projection_is_rejected(
    day_case,  # noqa: F811 - imported parametrized fixture
    tmp_path,
    monkeypatch,  # noqa: F811
):
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate), tmp_path)
        native = adapter._context_tables
        changed = False

        def corrupt(day):
            nonlocal changed
            if not changed:
                # Altering lookup metadata cannot hide an original context row.
                adapter._db().execute(
                    "UPDATE context SET product_id='wrong' WHERE name='price_plans'"
                )
                changed = True
            return native(day)

        monkeypatch.setattr(adapter, "_context_tables", corrupt)
        with pytest.raises(CampaignAnomalyFeatureStateError, match="private_context"):
            with adapter:
                pass
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_body_failure_withholds_receipt_and_closes_only_own_index(day_case, tmp_path):  # noqa: F811
    with open_gate(day_case, tmp_path) as (gate, _, _):
        adapter = CampaignAnomalyFeatureProjection(gate, feature_plan(gate), tmp_path)
        with pytest.raises(OSError, match="control_body_failure"):
            with adapter:
                raise OSError("control_body_failure")
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        assert gate.days
    assert not list(tmp_path.iterdir())
