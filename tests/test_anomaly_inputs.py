"""Independent frozen snapshot → curated → immutable input acceptance."""

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from retailops_ai.anomalies.contract import MODEL_FEATURES, TABLES, Point, Policy
from retailops_ai.anomalies.store import Manifest, build_inputs, input_id, verify_inputs
from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, descriptor_id
from retailops_ai.curated.reader import rows_as_of
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.importer import import_snapshot


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module", params=["demand", "physical"])
def prepared(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("anomaly-inputs").resolve()
    with ZipFile(Path(__file__).parents[1] / "data/fixtures/anomaly-v1_2.zip") as archive:
        assert sum(r.file_size for r in archive.infolist()) < 32 * 1024**2
        archive.extractall(root / "fixture")
    imported = import_snapshot(
        root / "fixture" / request.param / "public",
        root / "data/generated",
        required_use_cases=("anomaly_source",),
    )
    curated = build_curated(imported.directory, root / "data/generated")
    inputs = build_inputs(curated.directory, root / "data/generated")
    return root, request.param, imported, curated, inputs


def test_immutable_inputs_full_replay_and_pit_native_reads(prepared):
    root, _, imported, curated, inputs = prepared
    before = hashes(root / "data/generated")
    assert build_inputs(curated.directory, root / "data/generated").status == "reused"
    assert verify_inputs(inputs.directory, curated.directory) == inputs.manifest
    assert hashes(root / "data/generated") == before
    assert curated.manifest["descriptor"]["source_schema_version"] == "2.8.0"
    assert len(curated.manifest["tables"]) == 43
    descriptor = inputs.manifest.descriptor
    assert descriptor.parent.source_dataset_id == imported.snapshot.source_id
    assert (
        descriptor.input_tables == TABLES and descriptor.model_feature_allowlist == MODEL_FEATURES
    )
    assert descriptor.status_counts["ready_input"] > 0
    assert descriptor.status_counts["insufficient_data"] > 0
    points = [
        Point.model_validate_json(line)
        for line in (inputs.directory / "features.jsonl").read_bytes().splitlines()
    ]
    assert descriptor.row_count == len(points)
    assert len(
        {(p.business_date, p.product_id, p.selling_location_id, p.channel) for p in points}
    ) == len(points)
    point = next(p for p in points if p.on_hand is not None)
    native = list(
        rows_as_of(
            curated.directory,
            point.scoring_origin,
            table="inventory_daily_snapshots",
            business_date=point.business_date,
        )
    )
    assert any(
        r["product_id"] == point.product_id
        and r["stock_location_id"] == point.stock_location_id
        and r["on_hand"] == point.on_hand
        for r in native
    )
    assert not any("truth" in name for name in hashes(inputs.directory))
    assert all(p.raw_dq_completeness == "not_qualified" for p in points)


def test_private_truth_cannot_change_curated_or_feature_content(prepared):
    root, case, _, curated, inputs = prepared
    private = import_snapshot(
        root / "fixture" / case / "private",
        root / "private/data/generated",
        allow_evaluation_truth=True,
        required_use_cases=("anomaly_source",),
    )
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        build_curated(private.directory, root / "private/data/generated")
    other = build_curated(
        private.directory, root / "private/data/generated", allow_evaluation_truth=True
    )
    other_inputs = build_inputs(other.directory, root / "private/data/generated")
    assert other.manifest["tables"] == curated.manifest["tables"]
    assert (other_inputs.directory / "features.jsonl").read_bytes() == (
        inputs.directory / "features.jsonl"
    ).read_bytes()
    assert other_inputs.manifest.anomaly_input_id != inputs.manifest.anomaly_input_id


def test_resealed_false_residual_is_rejected_by_parent_replay(prepared, tmp_path):
    _, _, _, curated, inputs = prepared
    root = Path(shutil.copytree(inputs.directory, tmp_path / "corrupt"))
    lines = (root / "features.jsonl").read_bytes().splitlines()
    for index, raw in enumerate(lines):
        point = json.loads(raw)
        if point["status"] == "ready_input":
            point["observed_units"] += 100
            point["residual_units"] += 100
            point["standardized_residual"] = point["residual_units"] / point["robust_scale_units"]
            lines[index] = canonical_json(point)
            break
    raw = b"\n".join(lines) + b"\n"
    (root / "features.jsonl").write_bytes(raw)
    document = inputs.manifest.model_dump(mode="json")
    document["file_bytes"] = len(raw)
    document["file_sha256"] = hashlib.sha256(raw).hexdigest()
    document["descriptor"]["content_sha256"] = hashlib.sha256(raw).hexdigest()
    changed = Manifest.model_validate_json(canonical_json(document))
    document["anomaly_input_id"] = input_id(changed.descriptor)
    manifest_raw = canonical_json(document) + b"\n"
    (root / "anomaly_manifest.json").write_bytes(manifest_raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(manifest_raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="semantic_replay"):
        verify_inputs(root, curated.directory)


def test_scoring_policy_is_part_of_identity(prepared):
    root, _, _, curated, inputs = prepared
    delayed = build_inputs(
        curated.directory, root / "delayed/data/generated", Policy(scoring_delay_hours=48)
    )
    assert delayed.manifest.anomaly_input_id != inputs.manifest.anomaly_input_id


def test_resealed_early_curated_inventory_is_rejected(prepared, tmp_path):
    _, _, _, curated, _ = prepared
    root = Path(shutil.copytree(curated.directory, tmp_path / "curated-corrupt"))
    document = json.loads((root / "curated_manifest.json").read_text())
    table = next(t for t in document["tables"] if t["table"] == "inventory_daily_snapshots")
    ref = table["files"][0]
    path = root / ref["path"]
    arrow = pq.ParquetFile(path).read()
    rows = arrow.to_pylist()
    rows[0]["curated_available_at"] -= timedelta(microseconds=1)
    pq.write_table(pa.Table.from_pylist(rows, schema=arrow.schema), path)
    ref.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    digest = Digest(tmp_path / "reseal.sqlite", table["schema"], table["grain"])
    try:
        for row in iter_rows(root, table["files"], 8192):
            digest.add(row)
        table.update(digest.summary())
    finally:
        digest.close()
    document["descriptor"]["tables"] = [
        {k: v for k, v in t.items() if k != "files"} for t in document["tables"]
    ]
    document["curated_dataset_id"] = descriptor_id(document["descriptor"])
    raw = canonical_json(document) + b"\n"
    (root / "curated_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="causal_projection"):
        verify_curated(root)


def test_reviewed_schemas_match_runtime_models():
    root = Path(__file__).parents[1] / "contracts/anomaly/v1"
    for filename, model in (
        ("policy.schema.json", Policy),
        ("point.schema.json", Point),
        ("manifest.schema.json", Manifest),
    ):
        assert json.loads((root / filename).read_text()) == model.model_json_schema()
