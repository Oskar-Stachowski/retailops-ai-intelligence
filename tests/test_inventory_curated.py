"""Inventory handoff acceptance: native grains, causal time and immutable isolation."""

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

from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, descriptor_id
from retailops_ai.curated.reader import rows_as_of
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.importer import import_snapshot


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp("curated11").resolve()
    with ZipFile(Path(__file__).parents[1] / "data/fixtures/inventory-v1_1.zip") as archive:
        archive.extractall(root / "fixture")
    imported = import_snapshot(root / "fixture/facts", root / "facts/data/generated")
    curated = build_curated(imported.directory, root / "facts/data/generated")
    return root, imported, curated


def table_rows(curated, name):
    spec = next(t for t in curated.manifest["tables"] if t["table"] == name)
    return list(iter_rows(curated.directory, spec["files"], 8192))


def test_curated_rebuild_native_grain_and_isolation(prepared):
    root, imported, curated = prepared
    before = {p: p.read_bytes() for p in curated.directory.rglob("*") if p.is_file()}
    repeated = build_curated(imported.directory, root / "facts/data/generated")
    assert repeated.status == "reused"
    assert before == {p: p.read_bytes() for p in curated.directory.rglob("*") if p.is_file()}
    private = import_snapshot(
        root / "fixture/private", root / "private/data/generated", allow_evaluation_truth=True
    )
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        build_curated(private.directory, root / "private/data/generated")
    truth_curated = build_curated(
        private.directory, root / "private/data/generated", allow_evaluation_truth=True
    )
    assert curated.manifest["tables"] == truth_curated.manifest["tables"]
    assert curated.manifest["curated_dataset_id"] != truth_curated.manifest["curated_dataset_id"]
    assert not any(
        "truth" in str(p.relative_to(truth_curated.directory))
        for p in truth_curated.directory.rglob("*")
    )
    assert curated.manifest["readiness"] == {
        "forecast_source": "passed",
        "forecast_model": "not_ready",
        "anomaly": "not_ready",
        "stockout": "not_ready",
        "replay": "not_ready",
        "rag": "not_applicable",
        "inventory_ready": True,
    }
    native = next(
        t for t in curated.manifest["tables"] if t["table"] == "inventory_daily_snapshots"
    )
    assert native["grain"] == ["product_id", "stock_location_id", "business_date"]
    assert (
        curated.manifest["descriptor"]["parent_qualification_id"]
        == imported.snapshot.manifest["descriptor"]["parent_qualification_id"]
    )


def test_sales_and_returns_use_causal_availability_and_original_stock(prepared):
    _, _, curated = prepared
    native = {r["sale_id"]: r for r in table_rows(curated, "inventory_sales")}
    for row in table_rows(curated, "sales"):
        sale = native[row["id"]]
        assert row["curated_available_at"] >= sale["available_at"] > row["ingested_at"]
        assert row["mapped_stock_location_id"] == sale["stock_location_id"]
    for row in table_rows(curated, "return_events"):
        sale = native[row["sale_id"]]
        assert row["mapped_stock_location_id"] == sale["stock_location_id"]
        assert row["curated_available_at"] >= max(row["available_at"], sale["available_at"])
    for name in ("inventory_scope", "inventory_reorder_rules", "inventory_products"):
        assert all(r["curated_available_at"] is None for r in table_rows(curated, name))


def test_inventory_snapshot_is_unavailable_before_its_cutoff(prepared):
    _, _, curated = prepared
    snapshots = table_rows(curated, "inventory_daily_snapshots")
    first = min(r["snapshot_at"] for r in snapshots)
    assert (
        list(
            rows_as_of(
                curated.directory,
                first - timedelta(microseconds=1),
                table="inventory_daily_snapshots",
            )
        )
        == []
    )
    selected = list(rows_as_of(curated.directory, first, table="inventory_daily_snapshots"))
    expected = [r for r in snapshots if r["snapshot_at"] == first]
    assert {r["snapshot_id"] for r in selected} == {r["snapshot_id"] for r in expected}
    assert len(selected) == len({(r["product_id"], r["stock_location_id"]) for r in selected})
    for row in selected:
        assert row["curated_available_at"] == first
        assert row["source_available_at"] <= first
    assert (
        list(
            rows_as_of(
                curated.directory,
                first,
                table="inventory_daily_snapshots",
                business_date=first.date() + timedelta(days=1),
            )
        )
        == []
    )


def test_known_delivery_plan_is_visible_before_actual_delivery(prepared):
    _, _, curated = prepared
    plans = table_rows(curated, "delivery_plan_versions")
    first = min(plans, key=lambda r: r["curated_available_at"])
    before = list(
        rows_as_of(
            curated.directory,
            first["curated_available_at"] - timedelta(microseconds=1),
            table="delivery_plan_versions",
        )
    )
    assert all(r["replenishment_order_id"] != first["replenishment_order_id"] for r in before)
    selected = list(
        rows_as_of(curated.directory, first["curated_available_at"], table="delivery_plan_versions")
    )
    assert first["plan_version_id"] in {r["plan_version_id"] for r in selected}
    assert first["expected_delivery_at"] > first["curated_available_at"]
    receipts = list(
        rows_as_of(curated.directory, first["curated_available_at"], table="replenishment_receipts")
    )
    assert all(r["replenishment_order_id"] != first["replenishment_order_id"] for r in receipts)


@pytest.mark.parametrize("fault", ["early_snapshot", "early_sale", "wrong_stock", "lineage"])
def test_resealed_curated_semantic_corruption_rejected(prepared, tmp_path, fault):
    _, _, curated = prepared
    root = Path(shutil.copytree(curated.directory, tmp_path / "corrupt"))
    manifest = json.loads((root / "curated_manifest.json").read_text())
    name = "inventory_daily_snapshots" if fault == "early_snapshot" else "sales"
    table = next(t for t in manifest["tables"] if t["table"] == name)
    ref = table["files"][0]
    path = root / ref["path"]
    arrow = pq.ParquetFile(path).read()
    rows = arrow.to_pylist()
    if fault == "early_snapshot":
        rows[0]["curated_available_at"] -= timedelta(microseconds=1)
    elif fault == "early_sale":
        rows[0]["curated_available_at"] = rows[0]["ingested_at"]
    elif fault == "wrong_stock":
        rows[0]["mapped_stock_location_id"] = "other-stock"
    else:
        rows[0]["source_record_sha256"] = "0" * 64
    pq.write_table(pa.Table.from_pylist(rows, schema=arrow.schema), path)
    ref.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    digest = Digest(tmp_path / "digest.sqlite", table["schema"], table["grain"])
    try:
        for row in iter_rows(root, table["files"], 8192):
            digest.add(row)
        table.update(digest.summary())
    finally:
        digest.close()
    manifest["descriptor"]["tables"] = [
        {k: v for k, v in t.items() if k != "files"} for t in manifest["tables"]
    ]
    manifest["curated_dataset_id"] = descriptor_id(manifest["descriptor"])
    raw = canonical_json(manifest) + b"\n"
    (root / "curated_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="causal_projection"):
        verify_curated(root)


def test_reusable_reader_rejects_table_changed_after_verification(prepared, tmp_path):
    from retailops_ai.curated.reader import CuratedReader

    _, _, curated = prepared
    root = Path(shutil.copytree(curated.directory, tmp_path / "mutable"))
    reader = CuratedReader(root)
    spec = next(t for t in curated.manifest["tables"] if t["table"] == "inventory_daily_snapshots")
    path = root / spec["files"][0]["path"]
    arrow = pq.ParquetFile(path).read()
    records = arrow.to_pylist()
    origin = max(r["snapshot_at"] for r in records)
    records[0]["on_hand"] += 1
    pq.write_table(pa.Table.from_pylist(records, schema=arrow.schema), path)
    with pytest.raises(SnapshotError, match="changed_during_as_of_read"):
        list(reader.rows(origin, table="inventory_daily_snapshots"))


def test_delivery_revision_does_not_rewrite_earlier_origin(prepared, tmp_path, monkeypatch):
    # Isolated reader counterexample; the full source qualification is tested separately.
    from retailops_ai.curated import reader
    from retailops_ai.curated.contract import columns_for, schema_for

    _, _, curated = prepared
    first = table_rows(curated, "delivery_plan_versions")[0]
    cutoff = first["curated_available_at"] + timedelta(microseconds=1)
    second = {
        **first,
        "plan_version_id": "revised",
        "version": 2,
        "known_at": cutoff,
        "ingested_at": cutoff,
        "available_at": cutoff,
        "curated_available_at": cutoff,
        "expected_delivery_at": first["expected_delivery_at"] + timedelta(days=3),
    }
    columns = columns_for("delivery_plan_versions", "1.1.0")
    digest = Digest(tmp_path / "index.sqlite", columns, ["plan_version_id"])
    try:
        for row in (first, second):
            digest.add(row)
        spec = {
            "table": "delivery_plan_versions",
            "grain": ["plan_version_id"],
            **digest.summary(),
            "files": [{"path": "rows.parquet"}],
        }
    finally:
        digest.close()
    pq.write_table(
        pa.Table.from_pylist([first, second], schema=schema_for(columns, "1.1.0")),
        tmp_path / "rows.parquet",
    )
    monkeypatch.setattr(
        reader, "verify_curated", lambda *a, **kw: {"schema_version": "1.1.0", "tables": [spec]}
    )
    view = reader.CuratedReader(tmp_path)
    assert (
        list(view.rows(cutoff - timedelta(microseconds=1), table="delivery_plan_versions"))[0][
            "expected_delivery_at"
        ]
        == first["expected_delivery_at"]
    )
    assert (
        list(view.rows(cutoff, table="delivery_plan_versions"))[0]["expected_delivery_at"]
        == second["expected_delivery_at"]
    )
