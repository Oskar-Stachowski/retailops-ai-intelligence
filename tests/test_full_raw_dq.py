"""Independent all-parent replay, tail returns, causal history and fail-closed intake."""

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated.builder import build_curated
from retailops_ai.curated.contract import descriptor_id
from retailops_ai.full_raw_dq.contract import Binding, contract_bytes, parse_capture
from retailops_ai.full_raw_dq.replay import Replay
from retailops_ai.full_raw_dq.source import parent_facts
from retailops_ai.full_raw_dq.store import (
    Manifest,
    build_replay,
    replay_capture,
    replay_id,
    verify_replay,
)
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).parents[1]
LINEAGE = json.loads((ROOT / "data/fixtures/full-raw-dq-v2.lineage.json").read_bytes())


@pytest.mark.parametrize("version", [1, 2])
def test_boolean_partition_cannot_borrow_an_integer_capture_identity(version):
    from retailops_ai.raw_dq.contract import parse_capture as parse_v1

    record = delivery(None)
    if version == 1:
        record.pop("contract_version")
        record = seal(record)
    # Keep the integer-0 record ID while replacing the transport value with false.
    # Python considers False == 0, but canonical JSON and OPS07 do not.
    record["partition"] = False
    parser = parse_v1 if version == 1 else parse_capture
    with pytest.raises(SnapshotError, match="capture_identity_mismatch"):
        parser(record)


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def seal(payload):
    unsigned = {k: v for k, v in payload.items() if k != "record_id"}
    return {**unsigned, "record_id": "raw-record-sha256-" + json_sha256(unsigned)}


def delivery(event, offset=0, received="2026-10-01T00:00:00+00:00", body=None):
    return seal(
        {
            "contract_version": "raw-dq-capture-2.0.0",
            "kind": "event",
            "topic": "retailops.sales.v1",
            "partition": 0,
            "offset": offset,
            "received_at": received,
            "body_utf8": body if body is not None else canonical_json(event).decode(),
        }
    )


def progress(after=-1, through="2026-09-01T00:00:00+00:00", received="2026-10-01T00:00:00+00:00"):
    return seal(
        {
            "contract_version": "raw-dq-capture-2.0.0",
            "kind": "progress",
            "scope": "all_parent_sales_and_return_claims",
            "after_offset": after,
            "complete_through": through,
            "received_at": received,
        }
    )


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("full-dq").resolve()
    with ZipFile(ROOT / "data/fixtures/full-raw-dq-v2.zip") as archive:
        assert sum(i.file_size for i in archive.infolist()) < 32 * 1024**2
        assert {i.filename for i in archive.infolist()} == {
            k for c in LINEAGE["cases"].values() for k in c["files"]
        }
        archive.extractall(root / "inputs")
    case = request.param
    inputs = root / "inputs" / case
    generated = root / "data/generated"
    imported = import_snapshot(inputs / "public", generated, required_use_cases=("anomaly_source",))
    curated = build_curated(imported.directory, generated)
    binding = Binding.model_validate_json((inputs / "capture/source_binding.json").read_bytes())
    _, parent = parent_facts(curated.directory, imported.directory, binding)
    raw = (inputs / "capture/raw/events.jsonl").read_bytes()
    replay = replay_capture(raw, parent)
    result = build_replay(inputs / "capture", curated.directory, imported.directory, generated)
    return {
        "root": root,
        "case": case,
        "capture": inputs / "capture",
        "curated": curated.directory,
        "import": imported.directory,
        "binding": binding,
        "parent": parent,
        "raw": raw,
        "replay": replay,
        "result": result,
    }


def test_complete_operational_parity_tail_status_route_and_no_false_qualification(prepared):
    p = prepared
    snapshot = p["replay"].snapshot()
    reference = LINEAGE["cases"][p["case"]]
    operational = {k: v for k, v in snapshot.items() if k != "report"}
    assert json_sha256(operational) == reference["producer_operational_replay_sha256"]
    assert snapshot["report"] == dict(
        reference["producer_report"], policy_version="ai-full-parent-replay-2.0.0"
    )
    expected_sales, expected_returns = (1401, 232) if p["case"] == "demand" else (1390, 246)
    assert p["binding"].source_sales_count == expected_sales
    assert p["binding"].source_return_count == expected_returns
    assert len(p["parent"].events) == expected_sales + expected_returns
    assert snapshot["report"]["missing_parent_facts"] == snapshot["report"]["quarantined"] == 6
    assert all(
        snapshot["report"][k] == 2
        for k in ("duplicate_event", "duplicate_business", "late", "out_of_order")
    )
    returns = [f for f in snapshot["accepted_facts"] if f["event_type"] == "return_completed"]
    assert any(f["business_date"] > "2026-07-31" for f in returns)
    assert any(f["status"] == "rejected" for f in returns)
    assert max(f["occurred_at"] for f in returns).startswith("2026-08-31")
    assert all(f["currency"] in {"EUR", "PLN"} and f["stock_location_id"] for f in returns)
    for row in snapshot["final_aggregates"]:
        assert row["claim_units"] == row["units"] + row["rejected_units"]
    assert sum(len(r["missing_business_ids"]) for r in snapshot["parent_fact_coverage"]) == 6
    assert all(
        r["business_event_day_completeness"] == "not_qualified"
        for r in snapshot["parent_fact_coverage"]
    )
    assert not snapshot["report"]["transport_durability_proven"]
    published = Path(p["result"]["directory"])
    assert set(hashes(published)) == {
        "raw/events.jsonl",
        "source_binding.json",
        "replay.json",
        "dq_manifest.json",
        "manifest.sha256",
    }
    manifest = Manifest.model_validate_json((published / "dq_manifest.json").read_bytes())
    assert manifest.descriptor.evaluation_truth == "excluded"
    assert manifest.descriptor.model_readiness == "not_qualified"


def test_native_selling_route_is_not_the_legacy_wire_store(prepared):
    parent = prepared["parent"]
    changed = [
        (
            e,
            parent.facts[
                (
                    e["event_type"],
                    e["payload"]["sale_id" if e["event_type"] == "sale_completed" else "return_id"],
                )
            ],
        )
        for e in parent.events
    ]
    assert any(e["payload"]["store_id"] != f["selling_location_id"] for e, f in changed)
    assert any(
        e["payload"]["channel"] == "online" and f["stock_location_id"] != f["selling_location_id"]
        for e, f in changed
    )


def test_clean_full_capture_has_complete_parent_coverage_and_unknown_day(prepared):
    replay = Replay(prepared["parent"])
    for offset, event in enumerate(prepared["parent"].events):
        assert replay.consume(delivery(event, offset))["action"] == "accepted"
    assert replay.snapshot()["report"]["missing_parent_facts"] == 0
    assert all(r["status"] == "complete_parent_fact_coverage" for r in replay.coverage())
    assert all(r["business_event_day_completeness"] == "not_qualified" for r in replay.coverage())
    assert replay.aggregates_as_of("2026-09-30T00:00:00+00:00") == []
    assert all(r["quality_status"] == "partial_parent_fact_coverage" for r in replay.revisions)


@pytest.mark.parametrize("kind", ["sale_completed", "return_completed"])
@pytest.mark.parametrize(
    "change",
    [
        "money",
        "quantity",
        "private_field",
        "route",
        "clock",
        "source",
        "correlation",
        "hijacked_id",
    ],
)
def test_parent_valid_wire_changes_cannot_replace_native_facts(prepared, kind, change):
    parent = prepared["parent"]
    event = deepcopy(next(e for e in parent.events if e["event_type"] == kind))
    if change == "money":
        event["payload"]["total_amount" if kind == "sale_completed" else "refund_amount"] = "999.00"
    elif change == "quantity":
        event["payload"]["quantity"] = "999"
    elif change == "private_field":
        event["payload"]["anomaly_label"] = True
    elif change == "route":
        event["payload"]["store_id"] = "wrong-route"
    elif change == "clock":
        event["ingested_at"] = "2026-09-30T00:00:00+00:00"
    elif change == "source":
        event["source"] = "wrong-source"
    elif change == "correlation":
        event["correlation_id"] = "wrong-order"
    else:
        event["event_id"] = next(
            e["event_id"] for e in parent.events if e["event_id"] != event["event_id"]
        )
    replay = Replay(parent)
    assert replay.consume(delivery(event))["action"] == "quarantined"
    assert replay.facts == replay.revisions == []


@pytest.mark.parametrize(
    "kind,context", [("sale_completed", "sku"), ("return_completed", "order_id")]
)
def test_optional_context_exact_duplicates_and_business_duplicates(prepared, kind, context):
    event = deepcopy(next(e for e in prepared["parent"].events if e["event_type"] == kind))
    event["payload"].pop(context)
    replay = Replay(prepared["parent"])
    record = delivery(event)
    assert replay.consume(record)["action"] == "accepted"
    before = replay.snapshot()
    assert replay.consume(record)["action"] == "accepted"
    assert replay.snapshot() == before
    assert replay.consume(delivery(event, 1))["action"] == "duplicate_event"
    event["event_id"] = str(uuid4())
    assert replay.consume(delivery(event, 2))["action"] == "duplicate_business"
    assert len(replay.facts) == len(replay.revisions) == 1
    event["payload"]["quantity"] = "999"
    assert replay.consume(delivery(event, 3))["reason"] == "event_id_content_conflict"


@pytest.mark.parametrize(
    "body",
    [
        "[]",
        "{",
        '{"x":NaN}',
        '{"x":1e999}',
        '{"x":"\\ud800"}',
        '{"x":1,"x":2}',
        '{"schema_version":"1.0","event_id":[]}',
    ],
)
def test_bad_json_is_safe_quarantine_and_does_not_create_observation(prepared, body):
    replay = Replay(prepared["parent"])
    assert replay.consume(delivery(None, body=body))["action"] == "quarantined"
    assert replay.next_offset == 1
    assert replay.facts == replay.revisions == []
    assert "body_utf8" not in replay.quarantine[0]


@pytest.mark.parametrize("kind", ["sale_completed", "return_completed"])
def test_fact_cannot_be_visible_before_native_availability(prepared, kind):
    event = next(e for e in prepared["parent"].events if e["event_type"] == kind)
    replay = Replay(prepared["parent"])
    assert (
        replay.consume(delivery(event, received=event["occurred_at"]))["reason"]
        == "fact_unavailable_at_delivery"
    )
    assert replay.facts == []


def test_late_arrival_adds_revision_without_rewriting_past(prepared):
    replay = Replay(prepared["parent"])
    assert replay.consume(progress())["action"] == "progress"
    event = prepared["parent"].events[0]
    assert replay.consume(delivery(event, received="2026-10-02T00:00:00+00:00"))["reason"] == "late"
    assert replay.aggregates_as_of("2026-10-01T00:00:00+00:00") == []
    old = replay.aggregates_as_of("2026-10-02T00:00:00+00:00")
    second = next(
        e
        for e in prepared["parent"].events[1:]
        if e["payload"]["product_id"] == event["payload"]["product_id"]
    )
    replay.consume(delivery(second, 1, "2026-10-03T00:00:00+00:00"))
    assert replay.aggregates_as_of("2026-10-02T00:00:00+00:00") == old
    assert sum(r["fact_count"] for r in old) == 1


@pytest.mark.parametrize(
    "change", ["version", "offset", "record_id", "position", "future_frontier", "scope"]
)
def test_outer_contract_and_progress_fail_closed(prepared, change):
    event = prepared["parent"].events[0]
    record = delivery(event) if change in {"version", "offset", "record_id"} else progress()
    if change == "version":
        record["contract_version"] = "raw-dq-capture-1.0.0"
    elif change == "offset":
        record["offset"] = 8192
    elif change == "record_id":
        record["record_id"] = "raw-record-sha256-" + "f" * 64
    elif change == "position":
        record["after_offset"] = 0
    elif change == "future_frontier":
        record["complete_through"] = "2026-10-02T00:00:00+00:00"
    else:
        record["scope"] = "selected_sales_fixture"
    if change != "record_id":
        record = seal(record)
    with pytest.raises(ValueError):
        Replay(prepared["parent"]).consume(record)


def test_offset_delivery_and_watermark_cannot_regress(prepared):
    replay = Replay(prepared["parent"])
    replay.consume(delivery(prepared["parent"].events[0]))
    for record in [
        delivery(prepared["parent"].events[1], 2),
        delivery(prepared["parent"].events[1], 1, "2026-09-30T00:00:00+00:00"),
    ]:
        with pytest.raises(SnapshotError):
            replay.consume(record)
    replay.consume(progress(0))
    with pytest.raises(SnapshotError, match="frontier_not_advanced"):
        replay.consume(progress(0, received="2026-10-02T00:00:00+00:00"))


@pytest.mark.parametrize("change", ["descriptor", "events", "count", "table", "partial"])
def test_binding_cannot_hide_or_reassign_parent_facts(prepared, change):
    payload = prepared["binding"].model_dump(mode="json")
    if change == "descriptor":
        payload["source_descriptor_sha256"] = "f" * 64
    elif change == "events":
        payload["source_events_sha256"] = "f" * 64
    elif change == "count":
        payload["source_return_count"] -= 1
    elif change == "table":
        payload["projection_tables"]["inventory_sales"]["content_sha256"] = "f" * 64
    else:
        payload["source_event_count"] -= 1
        payload["source_return_count"] -= 1
        payload["projection_tables"]["return_events"]["row_count"] -= 1
    with pytest.raises(SnapshotError):
        parent_facts(
            prepared["curated"],
            prepared["import"],
            Binding.model_validate_json(canonical_json(payload)),
        )


@pytest.mark.parametrize(
    "extra", ["simulation_truth.json", "fault_plan.json", "curated/replay.json"]
)
def test_capture_rejects_private_plans_labels_and_precomputed_replay(prepared, tmp_path, extra):
    capture = Path(shutil.copytree(prepared["capture"], tmp_path / "capture"))
    p = capture / extra
    p.parent.mkdir(exist_ok=True)
    p.write_text("{}\n")
    reason = "unreferenced_directory" if "/" in extra else "missing_or_extra_artifact"
    with pytest.raises(SnapshotError, match=reason):
        build_replay(capture, prepared["curated"], prepared["import"], tmp_path / "data/generated")


def test_parent_link_resealed_with_different_seed_is_rejected(prepared, tmp_path):
    curated = Path(shutil.copytree(prepared["curated"], tmp_path / "curated"))
    document = json.loads((curated / "curated_manifest.json").read_bytes())
    document["descriptor"]["source_parameters"]["seed"] += 1
    document["curated_dataset_id"] = descriptor_id(document["descriptor"])
    raw = canonical_json(document) + b"\n"
    (curated / "curated_manifest.json").write_bytes(raw)
    (curated / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="parent_semantic_mismatch"):
        parent_facts(curated, prepared["import"], prepared["binding"])


def test_resealed_false_refund_aggregate_is_rejected(prepared, tmp_path):
    root = Path(shutil.copytree(prepared["result"]["directory"], tmp_path / "corrupt"))
    replay = json.loads((root / "replay.json").read_bytes())
    replay["final_aggregates"][0]["units"] += 999
    payload = canonical_json(replay) + b"\n"
    (root / "replay.json").write_bytes(payload)
    document = json.loads((root / "dq_manifest.json").read_bytes())
    document["descriptor"]["replay_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest = Manifest.model_validate_json(canonical_json(document))
    document["full_dq_replay_id"] = replay_id(manifest.descriptor)
    raw = canonical_json(document) + b"\n"
    (root / "dq_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="identity_runtime_or_parent"):
        verify_replay(root, prepared["curated"], prepared["import"])


def test_build_reuse_and_source_are_immutable(prepared):
    before = hashes(prepared["root"])
    result = build_replay(
        prepared["capture"],
        prepared["curated"],
        prepared["import"],
        prepared["root"] / "data/generated",
    )
    assert result["status"] == "reused"
    assert result["full_dq_replay_id"] == prepared["result"]["full_dq_replay_id"]
    assert hashes(prepared["root"]) == before


def test_staged_corruption_is_rejected_before_publication(prepared, tmp_path, monkeypatch):
    from retailops_ai.full_raw_dq import store

    original = store.write_private

    def corrupt(path, raw):
        if path.name == "replay.json":
            value = json.loads(raw)
            value["final_aggregates"][0]["units"] += 999
            raw = canonical_json(value) + b"\n"
        original(path, raw)

    monkeypatch.setattr(store, "write_private", corrupt)
    generated = tmp_path / "data/generated"
    with pytest.raises(SnapshotError, match="staged_payload_changed"):
        build_replay(prepared["capture"], prepared["curated"], prepared["import"], generated)
    assert list((generated / "full-dq-replay").iterdir()) == []


def test_file_cannot_hide_positions_using_repeated_receipts(prepared):
    record = delivery(prepared["parent"].events[0])
    with pytest.raises(SnapshotError, match="duplicate_capture_record"):
        replay_capture((canonical_json(record) + b"\n") * 2, prepared["parent"])
    with pytest.raises(SnapshotError, match="noncanonical_capture"):
        replay_capture(json.dumps(record).encode() + b"\n", prepared["parent"])
    with pytest.raises(SnapshotError, match="invalid_json_document"):
        replay_capture(json.dumps(record, indent=2).encode() + b"\n", prepared["parent"])


def test_reviewed_contracts_and_all_frozen_capture_records(prepared):
    for name, model in [("binding.schema.json", Binding), ("manifest.schema.json", Manifest)]:
        assert json.loads(contract_bytes(name)) == model.model_json_schema()
    validator = Draft202012Validator(json.loads(contract_bytes("producer_capture.schema.json")))
    for line in prepared["raw"].splitlines():
        record = json.loads(line)
        validator.validate(record)
        parse_capture(record)
    Draft202012Validator(json.loads(contract_bytes("producer_binding.schema.json"))).validate(
        prepared["binding"].model_dump(mode="json")
    )
