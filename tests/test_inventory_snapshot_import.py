"""Offline source 2.7 transport, independent reconciliation and truth opt-in."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path
from zipfile import ZipFile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from retailops_ai.curated.builder import build_curated
from retailops_ai.source_snapshot.canonical import RowDigest
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot, verify_import, verify_snapshot
from retailops_ai.source_snapshot.inventory_projection import native_row
from retailops_ai.source_snapshot.protocol import LOGICAL_FIELDS, Limits

ARCHIVE = Path(__file__).resolve().parents[1] / "data/fixtures/inventory-v1_1.zip"


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("inventory11").resolve()
    with ZipFile(ARCHIVE) as archive:
        assert sum(r.file_size for r in archive.infolist()) < 5 * 1024**2
        assert all(
            not r.filename.startswith("/") and ".." not in Path(r.filename).parts
            for r in archive.infolist()
        )
        archive.extractall(root)
    return root


def reseal(root, document):
    parent = document["source"]
    parent["dataset_id"] = "source-sha256-" + json_sha256(parent["descriptor"])
    document["source_dataset_id"] = document["descriptor"]["parent_source_dataset_id"] = parent[
        "dataset_id"
    ]
    qdesc = document["descriptor"]["qualification"]
    qdesc["parent_source_id"] = parent["dataset_id"]
    document["descriptor"]["parent_qualification_id"] = "inventory-labels-sha256-" + json_sha256(
        qdesc
    )
    (root / "manifests/dataset_manifest.v2.json").write_bytes(canonical_json(parent) + b"\n")
    for ref in [*document["metadata_files"], *(r for t in document["tables"] for r in t["files"])]:
        raw = (root / ref["path"]).read_bytes()
        ref.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    document["descriptor"]["schemas"] = {
        r["path"]: r["sha256"]
        for r in document["metadata_files"]
        if r["path"].startswith("schemas/")
    }
    document["descriptor"]["source_qualification_sha256"] = next(
        r["sha256"] for r in document["metadata_files"] if r["path"] == "reports/source_report.json"
    )
    document["snapshot_id"] = "snapshot-sha256-" + json_sha256(document["descriptor"])
    raw = canonical_json(document) + b"\n"
    (root / "snapshot_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


def change_table(root, document, name, change, scratch):
    table = next(t for t in document["tables"] if t["table"] == name)
    path = root / table["files"][0]["path"]
    arrow = pq.ParquetFile(path).read()
    first = arrow.to_pylist()
    change(first)
    pq.write_table(pa.Table.from_pylist(first, schema=arrow.schema), path)
    all_rows = [
        r for ref in table["files"] for r in pq.ParquetFile(root / ref["path"]).read().to_pylist()
    ]
    digest = RowDigest(
        scratch / (name + ".sqlite"),
        arrow.schema.names,
        table["grain"],
        [f.name for f in arrow.schema if pa.types.is_timestamp(f.type) or pa.types.is_date(f.type)],
    )
    try:
        for row in all_rows:
            digest.add(row)
        table.update(
            content_sha256=digest.digest(),
            field_ranges=digest.ranges,
            date_range=digest.date_range(),
        )
    finally:
        digest.close()
    if name in {"sales", "product_catalog", "fulfillment_routes"}:
        source_hash = table["content_sha256"]
    else:
        source_rows = sorted(
            [native_row(r) for r in all_rows], key=lambda r: tuple(r[k] for k in table["grain"])
        )
        source_hash = json_sha256(source_rows)
    document["source"]["descriptor"]["tables"][name]["content_sha256"] = source_hash
    document["descriptor"]["tables"] = [
        {k: t[k] for k in LOGICAL_FIELDS} for t in document["tables"]
    ]
    reseal(root, document)


def test_complete_import_verify_repeat_and_source_isolation(fixture, tmp_path):
    before = hashes(fixture)
    result = import_snapshot(
        fixture / "facts", tmp_path / "data/generated", required_use_cases=("inventory_source",)
    )
    assert result.status == "published" and len(result.snapshot.manifest["tables"]) == 43
    assert result.snapshot.manifest["source"]["schema_version"] == "2.7.0"
    assert verify_import(result.directory).snapshot_id == result.snapshot.snapshot_id
    published = hashes(result.directory)
    assert import_snapshot(fixture / "facts", tmp_path / "data/generated").status == "reused"
    assert hashes(result.directory) == published and hashes(fixture) == before
    assert not (result.directory / "snapshot/evaluation_truth").exists()
    assert all(
        p.stat().st_mode & 0o777 == 0o600 for p in result.directory.rglob("*") if p.is_file()
    )


def test_truth_and_qualification_require_explicit_opt_in(fixture, tmp_path):
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        import_snapshot(fixture / "private", tmp_path / "blocked/data/generated")
    result = import_snapshot(
        fixture / "private", tmp_path / "private/data/generated", allow_evaluation_truth=True
    )
    assert len(result.snapshot.manifest["tables"]) == 55
    assert (
        verify_import(result.directory, allow_evaluation_truth=True).snapshot_id
        == result.snapshot.snapshot_id
    )
    assert (
        result.directory
        / "snapshot/evaluation_truth/qualification/simulation_truth/inventory_qualified_windows.json"
    ).is_file()
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        verify_import(result.directory)


def test_curated_1_0_cannot_silently_process_inventory_1_1(fixture, tmp_path):
    result = import_snapshot(fixture / "facts", tmp_path / "import/data/generated")
    with pytest.raises(SnapshotError, match="inventory_curated_contract_not_yet_supported"):
        build_curated(result.directory, tmp_path / "curated/data/generated")
    assert not list((tmp_path / "curated/data/generated").glob("curated*/*"))


@pytest.mark.parametrize(
    "fault",
    [
        "bytes",
        "extra_truth",
        "source_lineage",
        "qualification_lineage",
        "schema",
        "missing_gate",
        "failed_gate",
        "source_false",
        "ready_true",
        "snapshot_id",
        "duplicate_reference",
    ],
)
def test_transport_and_metadata_faults_never_publish(fixture, tmp_path, fault):
    root = Path(shutil.copytree(fixture / "facts", tmp_path / "input"))
    document = json.loads((root / "snapshot_manifest.json").read_text())
    if fault == "bytes":
        path = root / document["tables"][0]["files"][0]["path"]
        path.write_bytes(path.read_bytes() + b"bad")
    elif fault == "extra_truth":
        (root / "evaluation_truth").mkdir()
        (root / "evaluation_truth/labels.json").write_text("[]")
    elif fault == "source_lineage":
        document["source"]["descriptor"]["owner"] = "other"
        reseal(root, document)
    elif fault == "qualification_lineage":
        document["descriptor"]["qualification"]["evaluated_at"] = "2099-01-01T00:00:00+00:00"
        reseal(root, document)
    elif fault == "schema":
        (root / "schemas/inventory_source_tables.v1.schema.json").write_text("{}")
        reseal(root, document)
    elif fault in {"missing_gate", "failed_gate"}:
        path = root / "reports/source_report.json"
        report = json.loads(path.read_text())
        if fault == "missing_gate":
            report["checks"][-1]["check_id"] = "another_gate"
        else:
            report["checks"][-1]["status"] = "failed"
        raw = canonical_json(report) + b"\n"
        path.write_bytes(raw)
        document["source"]["reports"][path.name].update(
            sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw)
        )
        reseal(root, document)
    elif fault == "source_false":
        document["source"]["facts_ready"] = False
        reseal(root, document)
    elif fault == "ready_true":
        document["source"]["inventory_ready"] = True
        reseal(root, document)
    elif fault == "snapshot_id":
        document["snapshot_id"] = "snapshot-sha256-" + "a" * 64
        raw = canonical_json(document) + b"\n"
        (root / "snapshot_manifest.json").write_bytes(raw)
        (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    else:
        document["metadata_files"].append(document["metadata_files"][0])
        reseal(root, document)
    with pytest.raises(SnapshotError):
        import_snapshot(root, tmp_path / "output/data/generated")
    assert not list((tmp_path / "output/data/generated/snapshots").glob("source-*"))


@pytest.mark.parametrize(
    "fault,table,error",
    [
        ("balance", "inventory_daily_snapshots", "known_snapshot_ledger_mismatch"),
        ("quantity", "inventory_sales", "inventory_issue_fact_mismatch"),
        ("refund", "inventory_returns", "inventory_refund_price_mismatch"),
        (
            "receipt",
            "replenishment_receipts",
            "inventory_over_receipt|inventory_issue_fact_mismatch",
        ),
        ("late_route", "inventory_fulfillment_routes", "sale_historical_route_mismatch"),
        ("commercial_quantity", "sales", "commerce_inventory_sale_mismatch"),
    ],
)
def test_self_resealed_semantic_forgery_is_independently_rejected(
    fixture, tmp_path, fault, table, error
):
    root = Path(shutil.copytree(fixture / "facts", tmp_path / "input"))
    document = json.loads((root / "snapshot_manifest.json").read_text())

    def change(rows):
        row = rows[0]
        if fault == "balance":
            row["on_hand"] = row["on_hand"] + 1
        elif fault in {"quantity", "commercial_quantity"}:
            row["quantity"] += 1
        elif fault == "refund":
            row["refund_amount"] += 1
        elif fault == "receipt":
            row["received_quantity"] += 1
        else:
            for row in rows:
                row["available_at"] += timedelta(days=1000)

    change_table(root, document, table, change, tmp_path)
    with pytest.raises(SnapshotError, match=error):
        verify_snapshot(root)


@pytest.mark.parametrize("use_case", ["forecasting", "anomaly", "stockout", "rag", "replay"])
def test_import_does_not_qualify_models(fixture, tmp_path, use_case):
    with pytest.raises(SnapshotError, match="invalid_required_use_cases"):
        import_snapshot(
            fixture / "facts", tmp_path / "data/generated", required_use_cases=(use_case,)
        )


def test_resource_limits_precede_copy(fixture, tmp_path):
    with pytest.raises(SnapshotError, match="snapshot_resource_limit"):
        import_snapshot(fixture / "facts", tmp_path / "data/generated", limits=Limits(max_rows=1))
    assert not (tmp_path / "data/generated").exists()
