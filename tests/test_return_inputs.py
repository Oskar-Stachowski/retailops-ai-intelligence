"""Public source handoff → PIT return queries → independently replayed immutable views."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated.builder import build_curated
from retailops_ai.curated.reader import CuratedReader
from retailops_ai.return_inputs.contract import Point, Policy
from retailops_ai.return_inputs.store import (
    Manifest,
    build_inputs,
    contract_bytes,
    input_id,
    parent_inputs,
    verify_inputs,
)
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).parents[1]
HISTORY = datetime(2026, 8, 1, tzinfo=UTC)
TAIL = datetime(2026, 9, 9, tzinfo=UTC)
POLICY = Policy(start_date=date(2026, 7, 2), end_date=date(2026, 9, 8), as_of_time=TAIL)


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("return-inputs").resolve()
    with ZipFile(ROOT / "data/fixtures/anomaly-v1_2.zip") as archive:
        archive.extractall(root / "fixture")
    imported = import_snapshot(
        root / "fixture" / request.param / "public",
        root / "data/generated",
        required_use_cases=("anomaly_source",),
    )
    curated = build_curated(imported.directory, root / "data/generated")
    result = build_inputs(curated.directory, root / "data/generated", POLICY)
    _, tables = parent_inputs(curated.directory)
    return root, request.param, curated, result, tables


def test_frozen_tail_all_events_reconcile_and_absent_days_stay_unknown(prepared):
    root, case, curated, result, tables = prepared
    before = hashes(root / "data/generated")
    assert build_inputs(curated.directory, root / "data/generated", POLICY).status == "reused"
    assert verify_inputs(result.directory, curated.directory) == result.manifest
    assert hashes(root / "data/generated") == before
    points = [
        Point.model_validate_json(line)
        for line in (result.directory / "returns.jsonl").read_bytes().splitlines()
    ]
    events = tables["return_events"]
    assert (
        sum(p.known_event_count for p in points)
        == len(events)
        == (232 if case == "demand" else 246)
    )
    assert sum(p.known_event_count for p in points if p.business_date > date(2026, 7, 31)) == (
        102 if case == "demand" else 116
    )
    assert sum(p.known_refunded_units for p in points) == sum(
        r["quantity"] for r in events if r["status"] == "refunded"
    )
    assert sum(p.known_rejected_units for p in points) == sum(
        r["quantity"] for r in events if r["status"] == "rejected"
    )
    assert all(p.observed_return_units is None and p.status == "insufficient_data" for p in points)
    assert any(p.known_event_count == 0 for p in points)
    assert result.manifest.descriptor.model_feature_allowlist == ()
    assert result.manifest.descriptor.detector_readiness == "not_qualified"
    assert len(points) == len(
        {
            (p.business_date, p.product_id, p.selling_location_id, p.channel, p.currency)
            for p in points
        }
    )
    assert not any("truth" in name for name in hashes(result.directory))


def test_native_returns_obey_event_clock_and_causal_availability(prepared):
    _, _, curated, _, tables = prepared
    reader = CuratedReader(curated.directory)
    first = min(tables["return_events"], key=lambda r: r["curated_available_at"])
    origin = first["curated_available_at"]
    assert list(reader.rows(origin - timedelta(microseconds=1), table="return_events")) == []
    selected = list(reader.rows(origin, table="return_events"))
    assert first["id"] in {r["id"] for r in selected}
    assert all(
        max(r["returned_at"], r["ingested_at"], r["available_at"], r["curated_available_at"])
        <= origin
        for r in selected
    )
    by_day = list(
        reader.rows(TAIL, table="return_events", business_date=first["returned_at"].date())
    )
    assert {r["id"] for r in by_day} == {
        r["id"]
        for r in tables["return_events"]
        if r["returned_at"].date() == first["returned_at"].date()
    }
    earlier = list(reader.rows(HISTORY, table="return_events"))
    assert len(list(reader.rows(TAIL, table="return_events"))) > len(earlier)
    assert list(reader.rows(HISTORY, table="return_events")) == earlier


def test_sale_cohorts_choose_latest_known_snapshot_and_keep_purchase_day(prepared):
    _, _, curated, _, _ = prepared
    reader = CuratedReader(curated.directory)
    history = list(reader.rows(HISTORY, table="daily_return_cohorts"))
    tail = list(reader.rows(TAIL, table="daily_return_cohorts"))
    assert history and len(history) == len(tail)
    assert all(r["snapshot_kind"] == "history" and r["as_of_time"] == HISTORY for r in history)
    assert any(not r["return_data_complete"] for r in history)
    assert all(r["snapshot_kind"] == "return_tail" and r["return_data_complete"] for r in tail)

    def key(r):
        return (r["business_date"], r["product_id"], r["selling_location_id"], r["channel"])

    assert len(tail) == len({key(r) for r in tail})
    assert max(r["business_date"] for r in tail) == date(2026, 7, 31)
    assert (
        list(reader.rows(HISTORY - timedelta(microseconds=1), table="daily_return_cohorts")) == []
    )
    assert list(reader.rows(HISTORY, table="daily_return_cohorts")) == history


def test_return_policy_unavailable_before_known_at(prepared):
    _, _, curated, _, tables = prepared
    reader = CuratedReader(curated.directory)
    first = min(r["curated_available_at"] for r in tables["return_policies"])
    assert list(reader.rows(first - timedelta(microseconds=1), table="return_policies")) == []
    assert len(list(reader.rows(first, table="return_policies"))) == len(tables["return_policies"])


def test_resealed_return_quantity_is_rejected_by_full_parent_replay(prepared, tmp_path):
    _, _, curated, result, _ = prepared
    root = Path(shutil.copytree(result.directory, tmp_path / "corrupt"))
    points = [json.loads(line) for line in (root / "returns.jsonl").read_bytes().splitlines()]
    point = next(p for p in points if p["known_event_count"])
    point["known_refunded_units"] += 100
    raw = b"".join(canonical_json(p) + b"\n" for p in points)
    (root / "returns.jsonl").write_bytes(raw)
    doc = result.manifest.model_dump(mode="json")
    doc.update(file_bytes=len(raw), file_sha256=hashlib.sha256(raw).hexdigest())
    doc["descriptor"]["content_sha256"] = doc["file_sha256"]
    changed = Manifest.model_validate_json(canonical_json(doc))
    doc["return_input_id"] = input_id(changed.descriptor)
    raw = canonical_json(doc) + b"\n"
    (root / "return_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="semantic_replay"):
        verify_inputs(root, curated.directory)


def test_runtime_rejects_self_attested_coverage_and_truth_files(prepared, tmp_path):
    _, _, curated, result, _ = prepared
    value = json.loads(next(iter((result.directory / "returns.jsonl").read_bytes().splitlines())))
    value.update(observed_return_units=0, coverage_status="complete")
    with pytest.raises(ValueError):
        Point.model_validate_json(canonical_json(value))
    root = Path(shutil.copytree(result.directory, tmp_path / "truth"))
    (root / "simulation_truth.json").write_text("{}\n")
    with pytest.raises(SnapshotError, match="missing_or_extra_artifact"):
        verify_inputs(root, curated.directory)


def test_return_reader_rejects_post_verification_replacement(prepared, tmp_path):
    _, _, curated, _, _ = prepared
    root = Path(shutil.copytree(curated.directory, tmp_path / "mutable"))
    reader = CuratedReader(root)
    spec = next(t for t in reader.manifest["tables"] if t["table"] == "return_events")
    path = root / spec["files"][0]["path"]
    arrow = pq.ParquetFile(path).read()
    records = arrow.to_pylist()
    records[0]["quantity"] += 1
    pq.write_table(pa.Table.from_pylist(records, schema=arrow.schema), path)
    with pytest.raises(SnapshotError, match="changed_during_as_of_read"):
        list(reader.rows(TAIL, table="return_events"))


def test_origin_is_part_of_immutable_identity(prepared):
    root, _, curated, _, _ = prepared
    first = Policy(start_date=date(2026, 7, 2), end_date=date(2026, 7, 31), as_of_time=HISTORY)
    late = first.model_copy(update={"as_of_time": TAIL})
    initial = build_inputs(curated.directory, root / "data/generated", first)
    before = hashes(initial.directory)
    corrected = build_inputs(curated.directory, root / "data/generated", late)
    assert initial.manifest.return_input_id != corrected.manifest.return_input_id
    assert hashes(initial.directory) == before
    assert verify_inputs(initial.directory, curated.directory) == initial.manifest


def test_private_truth_does_not_change_operational_return_content(prepared):
    root, case, _, result, _ = prepared
    imported = import_snapshot(
        root / "fixture" / case / "private",
        root / "private/data/generated",
        allow_evaluation_truth=True,
        required_use_cases=("anomaly_source",),
    )
    private = build_curated(
        imported.directory, root / "private/data/generated", allow_evaluation_truth=True
    )
    other = build_inputs(private.directory, root / "private/data/generated", POLICY)
    assert (other.directory / "returns.jsonl").read_bytes() == (
        result.directory / "returns.jsonl"
    ).read_bytes()
    assert other.manifest.return_input_id != result.manifest.return_input_id


def test_registered_contracts_match_runtime_and_validate_output(prepared):
    _, _, _, result, _ = prepared
    for name, model in (
        ("point.schema.json", Point),
        ("policy.schema.json", Policy),
        ("manifest.schema.json", Manifest),
    ):
        schema = json.loads(contract_bytes(name))
        assert schema == model.model_json_schema()
        Draft202012Validator(schema).check_schema(schema)
    validator = Draft202012Validator(json.loads(contract_bytes("point.schema.json")))
    for line in (result.directory / "returns.jsonl").read_bytes().splitlines():
        validator.validate(json.loads(line))
