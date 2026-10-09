"""Full public Source1.1/1.2, native global parent parity and fail-closed lifetime."""

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from test_full_raw_dq import delivery

from retailops_ai.curated.builder import build_curated, iter_rows
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_parent as component
from retailops_ai.evaluation_campaign.campaign_anomaly_replay import (
    CampaignAnomalyDiskReplay,
    CampaignAnomalyReplayPlan,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.forecasting.contract import Parent
from retailops_ai.full_raw_dq.replay import Replay
from retailops_ai.full_raw_dq.source import TABLES, business_key, projection
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot


@pytest.fixture(scope="module", params=["inventory", "demand", "physical"])
def public_parent(request, tmp_path_factory):
    """Already exposed repository controls; no private truth or new Project data."""
    root = tmp_path_factory.mktemp("ai09-anomaly-public-parent").resolve()
    name = "inventory-v1_1.zip" if request.param == "inventory" else "anomaly-v1_2.zip"
    prefix = "facts" if request.param == "inventory" else request.param + "/public"
    with ZipFile(Path(__file__).parents[1] / "data/fixtures" / name) as archive:
        for member in archive.infolist():
            if member.filename.startswith(prefix + "/"):
                archive.extract(member, root / "fixture")
    imported = import_snapshot(root / "fixture" / prefix, root / "data/generated")
    curated = build_curated(imported.directory, root / "data/generated")
    tables = {
        name: list(iter_rows(curated.directory, spec["files"], 256))
        for name in TABLES
        for spec in curated.manifest["tables"]
        if spec["table"] == name
    }
    native = projection(tables, curated.manifest["descriptor"]["source_parameters"]["seed"])
    return imported.directory / "snapshot", curated.directory, native, curated.manifest


def source(snapshot, curated):
    metadata = json.loads((snapshot / "snapshot_manifest.json").read_bytes())
    manifest = json.loads((curated / "curated_manifest.json").read_bytes())
    return PhysicalSourceSpec(
        schema_version=metadata["schema_version"],
        parent=Parent(
            source_dataset_id=metadata["source_dataset_id"],
            snapshot_id=metadata["snapshot_id"],
            curated_dataset_id=manifest["curated_dataset_id"],
            curated_descriptor_sha256=canonical_sha256(manifest["descriptor"]),
            business_timezone="UTC",
            forecast_source_status="passed",
        ),
        source_parameters=metadata["source"]["descriptor"]["resolved_parameters"],
        snapshot_manifest_sha256=hashlib.sha256(
            (snapshot / "snapshot_manifest.json").read_bytes()
        ).hexdigest(),
        curated_manifest_sha256=hashlib.sha256(
            (curated / "curated_manifest.json").read_bytes()
        ).hexdigest(),
    )


def reader(case, scratch, **updates):
    snapshot, curated, native, _ = case
    plan = component.CampaignAnomalyParentPlan(
        **{
            "source": source(snapshot, curated),
            "runtime": runtime_pin(),
            "parent_events": len(native.events),
            "max_index_bytes": 128 * 1024**2,
            **updates,
        }
    )
    return component.CampaignAnomalyPublicParent(snapshot, curated, plan, scratch)


def test_complete_real_projection_and_global_identity_match_native_parent(public_parent, tmp_path):
    _, _, native, manifest = public_parent
    adapter = reader(public_parent, tmp_path)
    with adapter:
        assert list(adapter.parent.events) == native.events
        assert dict(adapter.parent.canonical.items()) == native.canonical
        assert dict(adapter.parent.facts.items()) == native.facts
        assert dict(adapter.parent.ids.items()) == native.ids
        assert adapter.parent.events[0] == native.events[0]
        assert adapter.parent.events[-1] == native.events[-1]
        assert len(adapter.parent.events) == len(adapter.parent.facts) == len(native.events)
        for event in native.events:
            assert adapter.parent.match(event) == native.match(event)
        assert adapter.stats["stored_source_rows"] == sum(
            spec["row_count"] for spec in manifest["tables"] if spec["table"] in TABLES
        )
        assert adapter.stats["projected_parent_events"] == len(native.events)
        assert adapter.stats["maximum_selected_bytes"] <= adapter.plan.max_selected_bytes
        assert adapter.path.stat().st_mode & 0o777 == 0o600
        assert adapter.path.parent.stat().st_mode & 0o777 == 0o700
        with pytest.raises(SnapshotError, match="not_completed"):
            adapter.receipt()
        with pytest.raises(SnapshotError, match="stream_instead_of_slice"):
            adapter.parent.events[:]
        with pytest.raises(IndexError):
            adapter.parent.events[len(native.events)]
    receipt = adapter.receipt()
    assert receipt["native_events_sha256"] == json_sha256(native.events)
    assert receipt["physical_source_and_curated_replay_passed"]
    assert not receipt["source_generation_ancestry_verified"]
    assert not receipt["audited_read_authorization_proven"]
    assert not receipt["quality_qualified"] and not receipt["stage_ready"]
    assert receipt["business_event_day_completeness"] == "not_qualified"
    assert not list(tmp_path.iterdir())


def test_native_optional_context_alias_and_global_uuid_provenance_remain_exact(
    public_parent, tmp_path
):
    native = public_parent[2]
    with reader(public_parent, tmp_path) as adapter:
        for kind, optional in (("sale_completed", "sku"), ("return_completed", "order_id")):
            event = deepcopy(next(row for row in native.events if row["event_type"] == kind))
            event["payload"].pop(optional, None)
            event["event_id"] = str(uuid4())
            assert adapter.parent.match(event) == native.match(event)
            event["event_id"] = next(
                row["event_id"] for row in native.events if business_key(row) != business_key(event)
            )
            with pytest.raises(SnapshotError, match="canonical_operational_mismatch"):
                native.match(event)
            with pytest.raises(SnapshotError, match="canonical_operational_mismatch"):
                adapter.parent.match(event)


def test_whole_global_replay_from_disk_parent_matches_native_operational_outputs(
    public_parent, tmp_path
):
    native = public_parent[2]
    records = [delivery(event, offset) for offset, event in enumerate(native.events)]
    assert len(records) < 8192  # These are exposed bounded controls, not full capacity proof.
    baseline = Replay(native)
    for record in records:
        assert baseline.consume(record)["action"] == "accepted"
    plan = CampaignAnomalyReplayPlan(
        capture_version="raw-dq-capture-2.0.0",
        capture_sha256=hashlib.sha256(
            b"".join(canonical_json(record) + b"\n" for record in records)
        ).hexdigest(),
        capture_records=len(records),
        parent_events=len(native.events),
        max_index_bytes=128 * 1024**2,
    )
    with reader(public_parent, tmp_path) as adapter:
        with CampaignAnomalyDiskReplay(adapter.parent, plan, tmp_path) as replay:
            for record in records:
                assert replay.consume(record)["action"] == "accepted"
            report = replay.finish()
            assert report["missing_parent_facts"] == 0
            snapshot = baseline.snapshot()
            for table, key in (
                ("receipts", "receipts"),
                ("facts", "accepted_facts"),
                ("revisions", "aggregate_revisions"),
                ("quarantine", "quarantine"),
            ):
                assert list(replay.rows(table)) == snapshot[key]


@pytest.mark.parametrize("failure", ["parent_count", "index", "record", "pieces", "runtime"])
def test_whole_population_resource_or_runtime_failure_never_returns_receipt(
    public_parent, tmp_path, failure
):
    _, _, native, _ = public_parent
    changes = {
        "parent_count": {"parent_events": len(native.events) - 1},
        "index": {"max_index_bytes": 4096},
        "record": {"max_record_bytes": 1024},
        "pieces": {"max_selected_bytes": 4096},
        "runtime": {"runtime": runtime_pin().model_copy(update={"code_sha256": "f" * 64})},
    }[failure]
    adapter = reader(public_parent, tmp_path, **changes)
    reasons = {
        "parent_count": "full_population_binding",
        "index": "combined_index_budget",
        "record": "source_record_budget",
        "pieces": "projection_piece_budget",
        "runtime": "execution_runtime_changed",
    }
    with pytest.raises(SnapshotError, match=reasons[failure]):
        with adapter:
            pass
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", ["row", "resealed_row", "identifier", "extent"])
def test_private_state_changes_cannot_be_hidden_by_resealing_its_row(
    public_parent, tmp_path, failure
):
    adapter = reader(public_parent, tmp_path)
    with pytest.raises(component.CampaignAnomalyParentStateError, match="private_"):
        with adapter:
            database = adapter._db()
            if failure in {"row", "resealed_row"}:
                key, fact = adapter.parent.match(adapter.parent.events[0])
                fact["quantity"] += 1
                raw = canonical_json(fact)
                if failure == "row":
                    database.execute(
                        "UPDATE parents SET fact=? WHERE event_type=? AND business_id=?",
                        (raw, *key),
                    )
                    adapter.parent.facts[key]
                else:
                    database.execute(
                        "UPDATE parents SET fact=?,fact_sha256=? WHERE event_type=? AND business_id=?",
                        (raw, hashlib.sha256(raw).hexdigest(), *key),
                    )
            elif failure == "identifier":
                database.execute(
                    "UPDATE parents SET identifier=? WHERE sequence=1", (str(uuid4()),)
                )
            else:
                database.execute("DELETE FROM parents WHERE sequence=1")
            database.commit()
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(tmp_path.iterdir())


def test_public_input_change_to_a_non_projection_file_is_caught_on_exit(public_parent, tmp_path):
    snapshot, curated, native, manifest = public_parent
    target = tmp_path / "curated"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    shutil.copytree(curated, target)
    adapter = reader((snapshot, target, native, manifest), scratch)
    with pytest.raises(SnapshotError):
        with adapter:
            spec = next(
                spec
                for spec in manifest["tables"]
                if spec["table"] not in TABLES and spec["row_count"]
            )
            path = target / spec["files"][0]["path"]
            path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    assert not list(scratch.iterdir())


def test_body_failure_and_out_of_context_native_parent_never_quarantine_or_publish(
    public_parent, tmp_path
):
    adapter = reader(public_parent, tmp_path)
    with pytest.raises(OSError, match="controlled_body_failure"):
        with adapter:
            event = adapter.parent.events[0]
            raise OSError("controlled_body_failure")
    with pytest.raises(SnapshotError, match="not_completed"):
        adapter.receipt()
    with pytest.raises(component.CampaignAnomalyParentStateError, match="state_unavailable"):
        adapter.parent.match(event)
    with pytest.raises(SnapshotError, match="single_use"):
        adapter.__enter__()
    assert not list(tmp_path.iterdir())
