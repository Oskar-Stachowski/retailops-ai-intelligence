"""Behavioral curated acceptance and controlled temporal/mapping counterexamples."""

from __future__ import annotations

import hashlib
import json
import selectors
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator

from retailops_ai.curated import builder, reader
from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
from retailops_ai.curated.cli import main
from retailops_ai.curated.contract import (
    Config,
    Digest,
    cell,
    columns_for,
    descriptor_id,
    schema_for,
)
from retailops_ai.curated.transform import Index, Reject, transform
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.importer import import_snapshot
from retailops_ai.source_snapshot.protocol import Limits

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/fixtures/ai-smoke-v1/snapshot"
SOURCE_MANIFEST = json.loads((SOURCE / "snapshot_manifest.json").read_text())


def source_rows(table):
    spec = next(t for t in SOURCE_MANIFEST["tables"] if t["table"] == table)
    return list(iter_rows(SOURCE, spec["files"], 8192))


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("curated-input").resolve()
    return import_snapshot(SOURCE, workspace / "data/generated")


@pytest.fixture(scope="module")
def curated(imported, tmp_path_factory):
    return build_curated(
        imported.directory, tmp_path_factory.mktemp("curated-output").resolve() / "data/generated"
    )


@pytest.fixture
def index(tmp_path):
    result = Index(tmp_path / "mapping.sqlite")
    for table in (
        "products",
        "product_catalog",
        "catalog_categories",
        "stores",
        "selling_locations",
        "stock_locations",
        "channel_assignments",
        "fulfillment_routes",
        "assortment",
    ):
        for row in source_rows(table):
            result.add(table, row)
    observation = next(
        r
        for r in source_rows("daily_demand_observations")
        if r["observation_status"] == "observed_positive"
    )
    version = next(
        r for r in source_rows("daily_demand_versions") if r["observation_id"] == observation["id"]
    )
    result.add("daily_demand_observations", observation)
    result.add("daily_demand_versions", version)
    yield result, observation, version
    result.close()


def test_complete_curated_preserves_source_versions_and_idempotence(curated, imported):
    original, before = hashes(imported.directory), hashes(curated.directory)
    assert curated.status == "published" and curated.manifest["quarantine"]["row_count"] == 0
    assert sum(t["row_count"] for t in curated.manifest["tables"]) == 31171
    assert curated.manifest["descriptor"]["parent_source_dataset_id"] == imported.snapshot.source_id
    assert curated.manifest["descriptor"]["parent_snapshot_id"] == imported.snapshot.snapshot_id
    assert verify_curated(curated.directory) == curated.manifest
    repeated = build_curated(imported.directory, curated.directory.parents[1])
    assert repeated.status == "reused" and hashes(curated.directory) == before
    assert hashes(imported.directory) == original
    assert not list(curated.directory.parents[1].glob(".curated-build-*"))
    assert not any("truth" in p for p in before)
    assert {t["table"]: t["row_count"] for t in imported.snapshot.manifest["tables"]} == {
        t["table"]: t["row_count"] for t in curated.manifest["tables"]
    }


def test_normalizes_currency_units_and_explicit_locations(index):
    mapping, original, _ = index
    row = {**original, "currency": " pln ", "channel": original["channel"].upper()}
    result = transform("daily_demand_observations", row, ["id"], mapping, Config())
    assert result["currency"] == "PLN" and result["mapped_product_id"] == original["product_id"]
    assert result["mapped_selling_location_id"] == original["selling_location_id"]
    assert result["mapped_stock_location_id"] in {r["id"] for r in source_rows("stock_locations")}
    assert (
        result["quantity_unit"] == "pcs" and result["observed_units"] == original["observed_units"]
    )
    assert result["curated_available_at"] == original["available_at"]


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("product", "missing_products_reference"),
        ("location", "missing_selling_locations_reference"),
        ("route", "missing_available_fulfillment_routes_mapping"),
        ("late_route", "missing_available_fulfillment_routes_mapping"),
        ("currency", "unsupported_currency"),
        ("unit", "unsupported_product_unit"),
        ("gap", "source_data_gap"),
        ("negative", "negative_quantity"),
        ("closed_positive", "observation_status_quantity_mismatch"),
        ("ambiguous", "ambiguous_fulfillment_routes_mapping"),
    ],
)
def test_bad_mapping_or_gap_never_receives_random_location_or_zero(index, kind, reason):
    mapping, original, _ = index
    row = dict(original)
    if kind == "product":
        row["product_id"] = "missing"
    if kind == "location":
        row["selling_location_id"] = "missing"
    if kind == "route":
        mapping.db.execute("DELETE FROM intervals WHERE kind='fulfillment_routes'")
    if kind == "late_route":
        mapping.db.execute(
            "UPDATE intervals SET available='2099-01-01T00:00:00.000000+00:00' WHERE kind='fulfillment_routes'"
        )
    if kind == "ambiguous":
        mapping.db.execute(
            "INSERT INTO intervals SELECT * FROM intervals WHERE kind='fulfillment_routes'"
        )
    if kind == "currency":
        row["currency"] = "USD"
    if kind == "unit":
        mapping.db.execute(
            "UPDATE records SET body=json_set(CAST(body AS TEXT),'$.unit_of_measure','kg') WHERE kind='product_catalog'"
        )
    if kind == "gap":
        row["observed_units"], row["source_data_complete"] = None, False
    if kind == "negative":
        row["observed_units"] = -1
    if kind == "closed_positive":
        row["observation_status"] = "closed"
    with pytest.raises(Reject, match=reason):
        transform("daily_demand_observations", row, ["id"], mapping, Config())
    assert original["observed_units"] > 0


def test_known_zero_and_closed_are_preserved(index):
    mapping, original, _ = index
    for status in ("closed", "observed_zero"):
        result = transform(
            "daily_demand_observations",
            {**original, "observed_units": 0, "observation_status": status},
            ["id"],
            mapping,
            Config(),
        )
        assert result["observed_units"] == 0 and result["observation_status"] == status
    with pytest.raises(Reject, match="source_data_gap"):
        transform(
            "daily_demand_observations",
            {**original, "observed_units": None},
            ["id"],
            mapping,
            Config(),
        )


def test_statics_without_availability_are_explicitly_unknown(curated):
    for name in ("products", "stores", "orders", "order_items"):
        spec = next(t for t in curated.manifest["tables"] if t["table"] == name)
        first = next(iter_rows(curated.directory, spec["files"], 128))
        assert (
            first["curated_available_at"] is None and first["availability_status"] == "not_recorded"
        )


def synthetic_reader_package(tmp_path, monkeypatch, table, rows):
    # Typed unit counterexample; deliberately bypass full business qualification.
    digest = Digest(tmp_path / "synthetic.sqlite", columns_for(table), ["id"])
    try:
        for row in rows:
            digest.add(row)
        pq.write_table(
            pa.Table.from_pylist(rows, schema=schema_for(columns_for(table))),
            tmp_path / "rows.parquet",
        )
        spec = {
            "table": table,
            "grain": ["id"],
            **digest.summary(),
            "files": [{"path": "rows.parquet"}],
        }
    finally:
        digest.close()
    monkeypatch.setattr(reader, "verify_curated", lambda *args, **kwargs: {"tables": [spec]})


def test_late_quantity_correction_preserves_old_origin_at_microsecond_boundary(
    index, tmp_path, monkeypatch
):
    mapping, _, version = index
    first = transform("daily_demand_versions", version, ["id"], mapping, Config())
    cutoff = first["curated_available_at"]
    corrected = {
        **version,
        "id": "later-version",
        "version": 2,
        "observed_units": version["observed_units"] + 5,
        "available_at": cutoff + timedelta(microseconds=1),
    }
    mapping.add("daily_demand_versions", corrected)
    second = transform("daily_demand_versions", corrected, ["id"], mapping, Config())
    synthetic_reader_package(tmp_path, monkeypatch, "daily_demand_versions", [first, second])
    assert list(reader.rows_as_of(tmp_path, cutoff - timedelta(microseconds=1))) == []
    historical = list(reader.rows_as_of(tmp_path, cutoff))
    assert (
        historical[0]["version"] == 1
        and historical[0]["observed_units"] == version["observed_units"]
    )
    later = list(reader.rows_as_of(tmp_path, cutoff + timedelta(microseconds=1)))
    assert later[0]["version"] == 2 and later[0]["observed_units"] == corrected["observed_units"]


def test_future_plan_requires_a_known_version_and_half_open_effective_date(
    index, tmp_path, monkeypatch
):
    mapping, _, _ = index
    raw = next(r for r in source_rows("price_plans") if r["scope"] == "global")
    first = transform("price_plans", raw, ["id"], mapping, Config())
    second_raw = {
        **raw,
        "id": "new-price",
        "version": 2,
        "price": raw["price"] + 1,
        "available_at": raw["available_at"] + timedelta(days=1),
    }
    second = transform("price_plans", second_raw, ["id"], mapping, Config())
    synthetic_reader_package(tmp_path, monkeypatch, "price_plans", [first, second])
    before = list(
        reader.rows_as_of(
            tmp_path,
            first["curated_available_at"],
            table="price_plans",
            business_date=raw["effective_from"],
        )
    )
    assert len(before) == 1 and before[0]["price"] == raw["price"]
    after = list(
        reader.rows_as_of(
            tmp_path,
            second["curated_available_at"],
            table="price_plans",
            business_date=raw["effective_from"],
        )
    )
    assert after[0]["price"] == second_raw["price"]
    assert (
        list(
            reader.rows_as_of(
                tmp_path,
                second["curated_available_at"],
                table="price_plans",
                business_date=raw["effective_to"],
            )
        )
        == []
    )


@pytest.mark.parametrize("fault", ["gap", "regression"])
def test_history_versions_must_be_contiguous_and_chronological(index, fault):
    mapping, _, raw = index
    row = {
        **raw,
        "id": "bad-version",
        "version": 3 if fault == "gap" else 2,
        "available_at": raw["available_at"] - timedelta(microseconds=1),
    }
    with pytest.raises(
        Reject, match="version_gap" if fault == "gap" else "availability_regression"
    ):
        transform("daily_demand_versions", row, ["id"], mapping, Config())


def test_exact_decimal_nfc_and_utc_identity_has_no_context_rounding():
    with localcontext() as ctx:
        ctx.prec = 6
        assert (
            cell(Decimal("123456789012345678901234567890.12"))
            == "123456789012345678901234567890.12"
        )
    assert cell("e\u0301") == "é" and cell(Decimal("120.00")) == "120"
    assert cell(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00.000000+00:00"
    assert cell(0) == 0 and cell(None) is None and cell(False) is False
    with pytest.raises(SnapshotError):
        cell(datetime(2026, 1, 1))


def test_quarantine_retains_lineage_and_blocks_ready_publication(imported, tmp_path, monkeypatch):
    target = source_rows("daily_demand_observations")[0]["id"]

    def reject(table, row, grain, index, config):
        if table == "daily_demand_observations" and row["id"] == target:
            raise Reject("controlled_missing_mapping")
        return transform(table, row, grain, index, config)

    monkeypatch.setattr(builder, "transform", reject)
    result = build_curated(imported.directory, tmp_path / "data/generated")
    assert result.status == "quarantined" and result.directory.parent.name == "curated-rejected"
    assert result.manifest["quarantine"]["row_count"] == 1
    assert sum(t["row_count"] for t in result.manifest["tables"]) == 31170
    assert not (tmp_path / "data/generated/curated").exists()
    assert (
        verify_curated(result.directory, require_ready=False)["readiness"]["forecast_source"]
        == "failed"
    )
    with pytest.raises(SnapshotError, match="curated_not_ready"):
        verify_curated(result.directory)
    row = next(iter_rows(result.directory, result.manifest["quarantine"]["files"], 128))
    assert (
        row["source_table"] == "daily_demand_observations"
        and row["reason"] == "controlled_missing_mapping"
    )
    assert json.loads(row["row_json"])["id"] == target and len(row["source_record_sha256"]) == 64


@pytest.mark.parametrize("fault", ["byte", "missing", "extra", "symlink", "unsupported"])
def test_corrupted_curated_never_passes(curated, tmp_path, fault):
    destination = Path(shutil.copytree(curated.directory, tmp_path / "copy"))
    ref = curated.manifest["tables"][0]["files"][0]["path"]
    path = destination / ref
    if fault == "byte":
        path.write_bytes(path.read_bytes() + b"bad")
    if fault == "missing":
        path.unlink()
    if fault == "extra":
        (destination / "extra").write_text("unexpected")
    if fault == "symlink":
        path.unlink()
        path.symlink_to(curated.directory / ref)
    if fault == "unsupported":
        m = json.loads((destination / "curated_manifest.json").read_text())
        m["schema_version"] = "2.0.0"
        (destination / "curated_manifest.json").write_bytes(canonical_json(m))
    with pytest.raises(SnapshotError):
        verify_curated(destination)


def test_forged_byte_hash_does_not_hide_changed_typed_rows(curated, tmp_path):
    destination = Path(shutil.copytree(curated.directory, tmp_path / "copy"))
    m = json.loads((destination / "curated_manifest.json").read_text())
    spec = next(t for t in m["tables"] if t["table"] == "daily_demand_versions")
    ref = spec["files"][0]
    path = destination / ref["path"]
    rows = pq.ParquetFile(path).read().to_pylist()
    rows[0]["observed_units"] += 1
    pq.write_table(pa.Table.from_pylist(rows, schema=schema_for(spec["schema"])), path)
    ref["sha256"], ref["bytes"] = hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size
    raw = canonical_json(m) + b"\n"
    (destination / "curated_manifest.json").write_bytes(raw)
    (destination / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="curated_typed_content_mismatch"):
        verify_curated(destination)


def test_concurrent_builds_publish_once(imported, tmp_path):
    root = tmp_path / "data/generated"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            f.result()
            for f in [pool.submit(build_curated, imported.directory, root) for _ in range(2)]
        ]
    assert sorted(r.status for r in results) == ["published", "reused"]
    assert len(list((root / "curated").iterdir())) == 1 and not list(root.glob(".curated-build-*"))


def test_failure_before_publish_cleans_staging(imported, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("controlled_failure")

    monkeypatch.setattr(builder, "publish_noreplace", fail)
    root = tmp_path / "data/generated"
    with pytest.raises(OSError, match="controlled_failure"):
        build_curated(imported.directory, root)
    assert not list(root.glob(".curated-build-*")) and not list((root / "curated").iterdir())


def test_empty_destination_is_not_overwritten(curated, imported, tmp_path):
    root = tmp_path / "data/generated"
    destination = root / "curated" / curated.manifest["curated_dataset_id"]
    destination.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        build_curated(imported.directory, root)
    assert not list(destination.iterdir())


def test_partitioning_preserves_logical_id(curated, imported, tmp_path):
    result = build_curated(
        imported.directory, tmp_path / "data/generated", limits=Limits(batch_rows=997)
    )
    assert result.manifest["curated_dataset_id"] == curated.manifest["curated_dataset_id"]
    assert result.manifest["tables"] != curated.manifest["tables"]


def test_semantic_config_changes_id_but_preserves_parent(curated, imported, tmp_path):
    result = build_curated(
        imported.directory, tmp_path / "data/generated", config=Config(("EUR", "PLN", "USD"))
    )
    assert result.manifest["curated_dataset_id"] != curated.manifest["curated_dataset_id"]
    assert (
        result.manifest["descriptor"]["parent_source_dataset_id"]
        == curated.manifest["descriptor"]["parent_source_dataset_id"]
    )


def test_cli_and_config_reject_unsafe_scopes_and_origins(curated, imported, tmp_path, capsys):
    assert main(["verify", "--curated-dir", str(curated.directory)]) == 0
    assert (
        main(
            [
                "as-of",
                "--curated-dir",
                str(curated.directory),
                "--origin",
                "2026-07-31T23:59:59",
                "--limit",
                "1",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "as-of",
                "--curated-dir",
                str(curated.directory),
                "--origin",
                "2026-07-31T23:59:59Z",
                "--table",
                "evaluation_truth",
            ]
        )
        == 2
    )
    with pytest.raises(SnapshotError):
        build_curated(imported.directory, tmp_path / "elsewhere")
    with pytest.raises(SnapshotError):
        Config(business_timezone="Europe/Warsaw")


def test_schema_and_identity_are_self_contained(curated):
    schema = json.loads(builder.manifest_schema_bytes())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(curated.manifest)
    d = curated.manifest["descriptor"]
    assert (
        "curated_dataset_id" not in d and descriptor_id(d) == curated.manifest["curated_dataset_id"]
    )
    assert d["config_sha256"] == json_sha256(d["config"])
    assert d["transform"]["code_sha256"] == json_sha256(d["transform"]["code_files"])


def test_sigkill_leaves_only_hidden_private_staging_and_retry_succeeds(imported, tmp_path):
    root = tmp_path / "data/generated"
    script = """
import sys, time
from pathlib import Path
from retailops_ai.curated import builder
def wait_before_publish(source, target):
    print('ready', flush=True)
    time.sleep(60)
builder.publish_noreplace = wait_before_publish
builder.build_curated(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(imported.directory), str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=45), "worker did not reach publication"
            assert process.stdout.readline().strip() == "ready"
        process.kill()
        assert process.wait(timeout=10) != 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    assert not list((root / "curated").iterdir())
    abandoned = list(root.glob(".curated-build-*"))
    assert len(abandoned) == 1 and abandoned[0].stat().st_mode & 0o077 == 0
    result = build_curated(imported.directory, root)
    assert result.status == "published"
    assert verify_curated(result.directory)["readiness"]["forecast_source"] == "passed"


def test_truth_opt_in_keeps_truth_in_parent_import_and_outside_curated(tmp_path):
    from test_source_snapshot_import import add_synthetic_truth

    snapshot = Path(shutil.copytree(SOURCE, tmp_path / "input"))
    add_synthetic_truth(snapshot)
    imported = import_snapshot(snapshot, tmp_path / "data/generated", allow_evaluation_truth=True)
    with pytest.raises(SnapshotError):
        build_curated(imported.directory, tmp_path / "data/generated")
    result = build_curated(
        imported.directory, tmp_path / "data/generated", allow_evaluation_truth=True
    )
    assert len(result.manifest["tables"]) == 25
    assert not any("truth" in p for p in hashes(result.directory))
    assert (imported.directory / "snapshot/evaluation_truth").is_dir()


def test_output_limits_abort_before_publication(imported, tmp_path):
    # Input fits 2 MiB; added curated lineage exceeds this explicit output budget.
    root = tmp_path / "data/generated"
    with pytest.raises(SnapshotError, match="curated_output_resource_limit"):
        build_curated(imported.directory, root, limits=Limits(max_bytes=2 * 1024**2))
    assert not (root / "curated").exists() and not list(root.glob(".curated-build-*"))
