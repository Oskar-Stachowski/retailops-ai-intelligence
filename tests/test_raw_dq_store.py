"""Frozen operational raw DQ → verified canonical parent → immutable replay."""

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated.builder import build_curated
from retailops_ai.raw_dq.contract import Binding, contract_bytes
from retailops_ai.raw_dq.source import parent_facts
from retailops_ai.raw_dq.store import Manifest, build_replay, replay_id, verify_replay
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).parents[1]


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("raw-dq").resolve()
    for filename, directory in (("anomaly-v1_2.zip", "source"), ("raw-dq-v1.zip", "raw")):
        with ZipFile(ROOT / "data/fixtures" / filename) as archive:
            assert sum(r.file_size for r in archive.infolist()) < 32 * 1024**2
            archive.extractall(root / directory)
    case = request.param
    imported = import_snapshot(
        root / "source" / case / "public",
        root / "data/generated",
        required_use_cases=("anomaly_source",),
    )
    curated = build_curated(imported.directory, root / "data/generated")
    capture = root / "raw" / case
    result = build_replay(capture, curated.directory, root / "data/generated")
    return root, case, capture, curated.directory, result


def test_frozen_public_operational_parity_and_immutable_reuse(prepared):
    root, case, capture, curated, result = prepared
    directory = Path(result["directory"])
    before = hashes(root)
    repeated = build_replay(capture, curated, root / "data/generated")
    assert repeated["status"] == "reused"
    assert repeated["dq_replay_id"] == result["dq_replay_id"]
    manifest = verify_replay(directory, curated)
    assert hashes(root) == before
    snapshot = json.loads((directory / "replay.json").read_text())
    lineage = json.loads((ROOT / "data/fixtures/raw-dq-v1.lineage.json").read_text())[case]
    operational = {k: v for k, v in snapshot.items() if k != "report"}
    assert (
        hashlib.sha256(canonical_json(operational)).hexdigest()
        == lineage["operational_replay_sha256"]
    )
    for name, meta in lineage["files"].items():
        assert hashlib.sha256((capture / name).read_bytes()).hexdigest() == meta["sha256"]
    report = snapshot["report"]
    assert report["raw_events"] == 258 and report["accepted"] == 253
    assert report["quarantined"] == report["dlq_fixture"] == 3
    assert (
        report["duplicate_event"]
        == report["duplicate_business"]
        == report["late"]
        == report["out_of_order"]
        == 1
    )
    assert report["progress_declarations"] == 29
    assert report["raw_events"] == sum(
        report[k] for k in ("accepted", "quarantined", "duplicate_event", "duplicate_business")
    )
    assert (
        manifest.descriptor.model_readiness
        == manifest.descriptor.curated_completeness
        == "not_qualified"
    )
    assert not manifest.descriptor.transport_durability_proven
    assert not any("truth" in name for name in hashes(directory))
    assert all(
        r["quality_status"] == "partial_selected_sales_fixture"
        for r in snapshot["aggregate_revisions"]
    )
    capture_validator = Draft202012Validator(
        json.loads(contract_bytes("producer_capture.schema.json"))
    )
    for raw in (capture / "raw/events.jsonl").read_bytes().splitlines():
        capture_validator.validate(json.loads(raw))


@pytest.mark.parametrize("change", ["source_id", "projection_sha", "sales_count"])
def test_wrong_source_binding_is_rejected(prepared, change):
    _, _, capture, curated, _ = prepared
    payload = json.loads((capture / "source_binding.json").read_text())
    if change == "source_id":
        payload["source_dataset_id"] = "source-sha256-" + "f" * 64
    elif change == "projection_sha":
        payload["source_events_sha256"] = "f" * 64
    else:
        payload["source_sales_count"] += 1
    binding = Binding.model_validate_json(canonical_json(payload))
    with pytest.raises(SnapshotError):
        parent_facts(curated, binding)


def test_truth_files_are_rejected_at_capture_boundary(prepared, tmp_path):
    _, _, capture, curated, _ = prepared
    root = Path(shutil.copytree(capture, tmp_path / "capture"))
    (root / "simulation_truth.json").write_text("{}\n")
    with pytest.raises(SnapshotError, match="missing_or_extra_artifact"):
        build_replay(root, curated, tmp_path / "data/generated")


def test_resealed_false_aggregate_is_rejected_by_operational_replay(prepared, tmp_path):
    _, _, _, curated, result = prepared
    root = Path(shutil.copytree(result["directory"], tmp_path / "corrupt"))
    value = json.loads((root / "replay.json").read_text())
    value["aggregate_revisions"][0]["quantity"] += 100
    raw = canonical_json(value) + b"\n"
    (root / "replay.json").write_bytes(raw)
    document = json.loads((root / "dq_manifest.json").read_text())
    document["descriptor"]["replay_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest = Manifest.model_validate_json(canonical_json(document))
    document["dq_replay_id"] = replay_id(manifest.descriptor)
    raw = canonical_json(document) + b"\n"
    (root / "dq_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="identity_runtime_or_parent"):
        verify_replay(root, curated)


def test_contracts_match_runtime_and_reviewed_copies():
    for name, model in (("binding.schema.json", Binding), ("manifest.schema.json", Manifest)):
        assert json.loads(contract_bytes(name)) == model.model_json_schema()
    assert json.loads(contract_bytes("realtime-events.contract.json"))[
        "supported_schema_versions"
    ] == ["1.0"]
