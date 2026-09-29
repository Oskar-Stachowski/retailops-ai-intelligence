"""Cross-repository acceptance, corruption, typed forgery and immutable publication."""

from __future__ import annotations

import hashlib
import json
import selectors
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from retailops_ai.source_snapshot import importer
from retailops_ai.source_snapshot.canonical import multiset_digest
from retailops_ai.source_snapshot.cli import main
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.importer import import_snapshot, verify_import, verify_snapshot
from retailops_ai.source_snapshot.protocol import Limits
from retailops_ai.source_snapshot.publish import publish_noreplace
from retailops_ai.source_snapshot.tables import arrow_schema, verify_table

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/ai-smoke-v1/snapshot"


def raw_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def json_hash(value: object) -> str:
    return hashlib.sha256(raw_json(value)).hexdigest()


def tree_bytes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def manifest(root: Path) -> dict:
    return json.loads((root / "snapshot_manifest.json").read_text())


def reseal(root: Path, document: dict) -> None:
    """Refresh physical transport and lineage while preserving original logical hashes."""
    source = document["source"]
    for ref in source["reports"]:
        content = (root / "reports" / ref["path"]).read_bytes()
        ref["sha256"], ref["size_bytes"] = hashlib.sha256(content).hexdigest(), len(content)
    source_id = "source-sha256-" + json_hash(source["descriptor"])
    source["dataset_id"] = document["source_dataset_id"] = source_id
    document["descriptor"]["parent_source_dataset_id"] = source_id
    (root / "manifests/dataset_manifest.v2.json").write_bytes(raw_json(source) + b"\n")
    for ref in [*document["metadata_files"], *(r for t in document["tables"] for r in t["files"])]:
        content = (root / ref["path"]).read_bytes()
        ref["sha256"], ref["bytes"] = hashlib.sha256(content).hexdigest(), len(content)
    document["descriptor"]["source_qualification_sha256"] = next(
        r["sha256"] for r in document["metadata_files"] if r["path"] == "reports/source_report.json"
    )
    document["snapshot_id"] = "snapshot-sha256-" + json_hash(document["descriptor"])
    raw = raw_json(document) + b"\n"
    (root / "snapshot_manifest.json").write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


@pytest.fixture
def copied(tmp_path: Path) -> Path:
    return Path(shutil.copytree(FIXTURE, tmp_path / "input"))


def test_complete_import_preserves_source_and_reuses_identical_bytes(tmp_path: Path) -> None:
    before = tree_bytes(FIXTURE)
    result = import_snapshot(FIXTURE, tmp_path / "data/generated")
    assert result.status == "published"
    assert result.directory.name == result.snapshot.source_id
    assert len(result.snapshot.manifest["tables"]) == 25
    assert sum(t["row_count"] for t in result.snapshot.manifest["tables"]) == 31171
    assert tree_bytes(result.directory / "snapshot") == before
    assert not (result.directory / "snapshot/evaluation_truth").exists()
    assert verify_import(result.directory).snapshot_id == result.snapshot.snapshot_id
    published = tree_bytes(result.directory)
    repeated = import_snapshot(FIXTURE, tmp_path / "data/generated")
    assert repeated.status == "reused"
    assert tree_bytes(result.directory) == published
    assert tree_bytes(FIXTURE) == before
    assert not list(result.directory.parent.glob(".snapshot-import-*"))


def test_multiset_preserves_duplicates_order_types_and_decimal_context(tmp_path: Path) -> None:
    columns = ["text", "money", "instant", "day", "quantity", "closed", "unknown"]
    row = {
        "text": "e\u0301",
        "money": Decimal("12345678901234567890.1234567890123"),
        "instant": datetime(2026, 7, 2, 0, 0, 0, 123456, tzinfo=UTC),
        "day": date(2026, 7, 2),
        "quantity": 0,
        "closed": False,
        "unknown": None,
    }
    expected = {
        "text": "é",
        "money": "12345678901234567890.12345679",
        "instant": "2026-07-02T00:00:00.123456+00:00",
        "day": "2026-07-02",
        "quantity": 0,
        "closed": False,
        "unknown": None,
    }
    other = dict(row, quantity=1)
    expected_other = dict(expected, quantity=1)
    records = sorted([raw_json(expected), raw_json(expected), raw_json(expected_other)])
    digest = hashlib.sha256(
        raw_json(columns) + b"\n" + b"".join(r + b"\n" for r in records)
    ).hexdigest()
    with localcontext() as context:
        context.prec = 6
        assert multiset_digest([other, row, row], columns, tmp_path / "rows.sqlite") == digest
    assert multiset_digest([row, other], columns, tmp_path / "without-duplicate.sqlite") != digest


@pytest.mark.parametrize("alteration", ["byte", "missing", "extra", "directory", "symlink", "fifo"])
def test_bad_files_never_publish(copied: Path, tmp_path: Path, alteration: str) -> None:
    import os

    path = copied / "facts/products/part-000000.parquet"
    if alteration == "byte":
        path.write_bytes(path.read_bytes() + b"corrupt")
    elif alteration == "missing":
        path.unlink()
    elif alteration == "extra":
        (copied / "secret.txt").write_text("not a declared artifact")
    elif alteration == "directory":
        (copied / "undeclared").mkdir()
    elif alteration == "symlink":
        content = tmp_path / "outside.parquet"
        content.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(content)
    else:
        path.unlink()
        os.mkfifo(path)
    with pytest.raises((SnapshotError, OSError)):
        import_snapshot(copied, tmp_path / "data/generated")
    publications = tmp_path / "data/generated/snapshots"
    assert not publications.exists() or not list(publications.iterdir())


@pytest.mark.parametrize(
    "version_field,version", [("schema_version", "2.0.0"), ("source", "3.0.0")]
)
def test_unsupported_versions(copied: Path, version_field: str, version: str) -> None:
    document = manifest(copied)
    if version_field == "source":
        document["source"]["schema_version"] = version
    else:
        document[version_field] = version
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="unsupported_snapshot_or_source_version"):
        verify_snapshot(copied)


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "facts//products/part-000000.parquet",
        "facts/./products/part-000000.parquet",
        "facts\\products\\part-000000.parquet",
        "C:escape",
    ],
)
def test_unsafe_declared_paths(copied: Path, path: str) -> None:
    document = manifest(copied)
    reseal(copied, document)
    document["tables"][0]["files"][0]["path"] = path
    raw = raw_json(document) + b"\n"
    (copied / "snapshot_manifest.json").write_bytes(raw)
    (copied / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(SnapshotError, match="unsafe_artifact_path"):
        verify_snapshot(copied)


def test_forged_physical_checksum_does_not_hide_changed_typed_rows(copied: Path) -> None:
    document = manifest(copied)
    path = copied / document["tables"][0]["files"][0]["path"]
    table = pq.ParquetFile(path).read()
    rows = table.to_pylist()
    rows[0]["brand"] = "changed but physically resealed"
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="typed_logical_content_mismatch"):
        verify_snapshot(copied)


@pytest.mark.parametrize("variant", ["metadata", "type", "empty"])
def test_exact_arrow_schema_and_required_values(copied: Path, variant: str) -> None:
    document = manifest(copied)
    path = copied / document["tables"][0]["files"][0]["path"]
    table = pq.ParquetFile(path).read()
    if variant == "metadata":
        table = table.replace_schema_metadata(None)
    elif variant == "type":
        table = table.set_column(
            table.schema.get_field_index("status"),
            "status",
            pa.array([1] * table.num_rows, type=pa.int64()),
        )
    else:
        rows = table.to_pylist()
        rows[0]["brand"] = ""
        table = pa.Table.from_pylist(rows, schema=table.schema)
    pq.write_table(table, path)
    reseal(copied, document)
    with pytest.raises(
        SnapshotError, match="typed_arrow_schema_mismatch|nonnullable_value_missing"
    ):
        verify_snapshot(copied)


def test_file_count_mismatch(copied: Path) -> None:
    document = manifest(copied)
    document["tables"][0]["files"][0]["row_count"] += 1
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="parquet_file_row_count_mismatch"):
        verify_snapshot(copied)


def test_duplicate_grain_rejected_before_hash(copied: Path) -> None:
    document = manifest(copied)
    path = copied / document["tables"][0]["files"][0]["path"]
    table = pq.ParquetFile(path).read()
    rows = table.to_pylist()
    rows[1]["id"] = rows[0]["id"]
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="duplicate_grain"):
        verify_snapshot(copied)


@pytest.mark.parametrize("kind", ["status", "value", "duplicate"])
def test_failed_or_forged_gates(copied: Path, kind: str) -> None:
    document = manifest(copied)
    path = copied / "reports/source_report.json"
    report = json.loads(path.read_text())
    if kind == "duplicate":
        report["checks"][1] = report["checks"][0]
    else:
        report["checks"][0][kind] = "failed" if kind == "status" else 1
    path.write_bytes(raw_json(report) + b"\n")
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="failed_source_hard_gate"):
        verify_snapshot(copied)


def test_required_not_ready_use_case_blocks_publication(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="required_use_case_not_ready"):
        import_snapshot(FIXTURE, tmp_path / "data/generated", required_use_cases=("stockout",))
    assert not list((tmp_path / "data/generated/snapshots").iterdir())


def test_duplicate_references_and_fake_id(copied: Path) -> None:
    document = manifest(copied)
    document["metadata_files"].append(document["metadata_files"][0])
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="duplicate_file_reference"):
        verify_snapshot(copied)
    document["metadata_files"].pop()
    reseal(copied, document)
    document["snapshot_id"] = "snapshot-sha256-" + "a" * 64
    (copied / "snapshot_manifest.json").write_bytes(raw_json(document))
    with pytest.raises(SnapshotError, match="identity_or_lineage_mismatch"):
        verify_snapshot(copied)


def test_same_source_id_with_another_snapshot_never_overwrites(
    copied: Path, tmp_path: Path
) -> None:
    original = import_snapshot(FIXTURE, tmp_path / "data/generated")
    before = tree_bytes(original.directory)
    document = manifest(copied)
    document["exporter"]["dependency_sha256"] = document["descriptor"]["dependency_sha256"] = (
        "a" * 64
    )
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="immutable_source_id_conflict"):
        import_snapshot(copied, tmp_path / "data/generated")
    assert tree_bytes(original.directory) == before


@pytest.mark.parametrize("kind", ["empty", "corrupted"])
def test_existing_bad_destination_is_not_replaced(tmp_path: Path, kind: str) -> None:
    source_id = manifest(FIXTURE)["source_dataset_id"]
    destination = tmp_path / "data/generated/snapshots" / source_id
    if kind == "empty":
        destination.mkdir(parents=True)
    else:
        result = import_snapshot(FIXTURE, tmp_path / "data/generated")
        (result.directory / "import_manifest.json").write_text("corruption")
    before = tree_bytes(destination)
    with pytest.raises(SnapshotError):
        import_snapshot(FIXTURE, tmp_path / "data/generated")
    assert tree_bytes(destination) == before


def test_concurrent_imports_publish_once(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(import_snapshot, FIXTURE, tmp_path / "data/generated") for _ in range(2)
        ]
        results = [task.result() for task in tasks]
    assert sorted(r.status for r in results) == ["published", "reused"]
    assert results[0].directory == results[1].directory
    assert not list(results[0].directory.parent.glob(".snapshot-import-*"))


def test_controlled_failure_has_no_partial_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stop(source: Path, destination: Path) -> None:
        raise OSError("controlled failure before publication")

    monkeypatch.setattr(importer, "publish_noreplace", stop)
    with pytest.raises(OSError, match="controlled failure"):
        import_snapshot(FIXTURE, tmp_path / "data/generated")
    assert not list((tmp_path / "data/generated/snapshots").iterdir())


def test_sigkill_keeps_staging_private_and_retry_publishes(tmp_path: Path) -> None:
    generated = tmp_path / "data/generated"
    code = """import sys
from pathlib import Path
from retailops_ai.source_snapshot import importer
def wait_before_publication(source, destination):
    print('sealed_before_publish', flush=True)
    sys.stdin.buffer.read(1)
importer.publish_noreplace = wait_before_publication
importer.import_snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(FIXTURE), str(generated)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=30), "worker did not reach sealed staging"
            assert process.stdout.readline().strip() == "sealed_before_publish"
        process.kill()
        process.wait(timeout=10)
        parent = generated / "snapshots"
        source_id = manifest(FIXTURE)["source_dataset_id"]
        assert not (parent / source_id).exists()
        orphans = list(parent.glob(".snapshot-import-*"))
        assert len(orphans) == 1 and orphans[0].stat().st_mode & 0o777 == 0o700
        assert import_snapshot(FIXTURE, generated).status == "published"
        assert verify_import(parent / source_id).source_id == source_id
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def test_no_replace_syscall_refuses_empty_directory(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "complete").write_text("payload")
    with pytest.raises(FileExistsError):
        publish_noreplace(source, destination)
    assert (source / "complete").exists() and not list(destination.iterdir())
    destination.rmdir()
    publish_noreplace(source, destination)
    assert (destination / "complete").read_text() == "payload"


def test_generated_root_and_input_relationship_are_guarded(copied: Path, tmp_path: Path) -> None:
    before = tree_bytes(copied)
    with pytest.raises(SnapshotError, match="output_requires_data_generated_root"):
        import_snapshot(copied, tmp_path / "data/fixtures")
    with pytest.raises(SnapshotError, match="output_must_be_outside_snapshot_input"):
        import_snapshot(copied, copied / "data/generated")
    assert tree_bytes(copied) == before


def test_cli_requires_opt_in_and_valid_limits(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["verify", "--snapshot-dir", str(FIXTURE), "--max-rows", "1"]) == 2
    assert "snapshot_resource_limit" in capsys.readouterr().err
    assert main(["verify", "--snapshot-dir", str(FIXTURE), "--batch-rows", "0"]) == 2
    assert "invalid_import_limits" in capsys.readouterr().err
    with pytest.raises(SnapshotError, match="snapshot_resource_limit"):
        verify_snapshot(FIXTURE, limits=Limits(max_bytes=1024))


def test_transport_metadata_changes_reuse_logical_snapshot(copied: Path, tmp_path: Path) -> None:
    result = import_snapshot(FIXTURE, tmp_path / "data/generated")
    before = tree_bytes(result.directory)
    document = manifest(copied)
    document["generated_at"] = "2026-09-29T00:00:00+00:00"
    reseal(copied, document)
    assert document["snapshot_id"] == result.snapshot.snapshot_id
    assert import_snapshot(copied, tmp_path / "data/generated").status == "reused"
    assert tree_bytes(result.directory) == before


def test_detached_isolated_consumer_has_no_generator_or_database(tmp_path: Path) -> None:
    detached = tmp_path / "consumer"
    package = detached / "retailops_ai"
    package.mkdir(parents=True)
    shutil.copy(ROOT / "src/retailops_ai/__init__.py", package)
    shutil.copytree(ROOT / "src/retailops_ai/source_snapshot", package / "source_snapshot")
    for name in ["contract.json", "snapshot_manifest.schema.json"]:
        shutil.copy(
            ROOT / "contracts/source_snapshot/v1" / name, package / "source_snapshot" / name
        )
    shutil.copy(ROOT / "uv.lock", package / "source_snapshot/dependencies.lock")
    shutil.copytree(FIXTURE, detached / "input")
    code = """import json,sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from retailops_ai.source_snapshot.cli import main
assert main(['import','--snapshot-dir','input']) == 0
assert not any(n.startswith(('data.generator','sqlalchemy','retailops_ai.api')) for n in sys.modules)
"""
    outputs = []
    for _ in range(2):
        run = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=detached,
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        outputs.append(json.loads(run.stdout))
    assert [o["status"] for o in outputs] == ["published", "reused"]
    assert outputs[0]["source_dataset_id"] == outputs[1]["source_dataset_id"]


def test_repartitioned_typed_rows_keep_the_same_identity(copied: Path, tmp_path: Path) -> None:
    document = manifest(copied)
    products = document["tables"][0]
    path = copied / products["files"][0]["path"]
    table = pq.ParquetFile(path).read()
    second = path.with_name("part-000001.parquet")
    pq.write_table(table.slice(0, 10), path)
    pq.write_table(table.slice(10), second)
    products["files"][0]["row_count"] = 10
    products["files"].append(
        dict(products["files"][0], path=second.relative_to(copied).as_posix(), row_count=10)
    )
    reseal(copied, document)
    assert verify_snapshot(copied).snapshot_id == manifest(FIXTURE)["snapshot_id"]
    original = import_snapshot(FIXTURE, tmp_path / "data/generated")
    before = tree_bytes(original.directory)
    assert import_snapshot(copied, tmp_path / "data/generated").status == "reused"
    assert tree_bytes(original.directory) == before


@pytest.mark.parametrize("wrong_partition", [False, True])
def test_daily_partitions_are_verified_against_decoded_rows(
    copied: Path, wrong_partition: bool
) -> None:
    document = manifest(copied)
    versions = next(t for t in document["tables"] if t["table"] == "daily_demand_versions")
    original = copied / versions["files"][0]["path"]
    table = pq.ParquetFile(original).read()
    original.unlink()
    groups = {}
    for row in table.to_pylist():
        groups.setdefault(row["business_date"].isoformat(), []).append(row)
    versions["files"] = []
    versions["partition_source_field"] = "business_date"
    for ordinal, (day, rows) in enumerate(sorted(groups.items())):
        folder = copied / "facts/daily_demand_versions" / ("business_date=" + day)
        folder.mkdir()
        output = folder / "part-000000.parquet"
        if wrong_partition and ordinal == 0:
            rows[0] = dict(rows[0], business_date=date(2026, 7, 31))
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), output)
        versions["files"].append(
            {
                "path": output.relative_to(copied).as_posix(),
                "row_count": len(rows),
                "bytes": 0,
                "sha256": "a" * 64,
            }
        )
    reseal(copied, document)
    if wrong_partition:
        with pytest.raises(SnapshotError, match="partition_row_mismatch"):
            verify_snapshot(copied)
    else:
        assert verify_snapshot(copied).snapshot_id == manifest(FIXTURE)["snapshot_id"]


def test_recomputed_date_ranges_are_not_trusted_metadata(copied: Path) -> None:
    document = manifest(copied)
    table = next(t for t in document["tables"] if t["table"] == "orders")
    artifact = next(a for a in document["source"]["artifacts"] if a["table"] == "orders")
    table["field_ranges"]["ordered_at"]["value_count"] += 1
    artifact["field_ranges"] = table["field_ranges"]
    table["date_range"]["value_count"] += 1
    artifact["date_range"] = table["date_range"]
    document["descriptor"]["tables"] = [
        {
            key: t[key]
            for key in [
                "table",
                "data_class",
                "row_count",
                "content_sha256",
                "grain",
                "date_range",
                "field_ranges",
                "schema",
            ]
        }
        for t in document["tables"]
    ]
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="typed_date_range_mismatch"):
        verify_snapshot(copied)


@pytest.mark.parametrize("name", ["product_catalog", "daily_demand_exclusions"])
@pytest.mark.parametrize("tampered", [False, True])
def test_null_temporal_columns_and_empty_tables_keep_zero_ranges(
    tmp_path: Path, name: str, tampered: bool
) -> None:
    table = next(t for t in manifest(FIXTURE)["tables"] if t["table"] == name)
    schema = arrow_schema(table["schema"])
    rows = []
    if name == "product_catalog":
        rows = pq.ParquetFile(FIXTURE / table["files"][0]["path"]).read().to_pylist()
        for row in rows:
            row["discontinue_date"] = None
        table["date_range"]["value_count"] -= table["field_ranges"]["discontinue_date"][
            "value_count"
        ]
        table["field_ranges"]["discontinue_date"] = {
            "date_start": None,
            "date_end": None,
            "value_count": 0,
        }
        # The fixture spans availability on July 1 and launches through July 9.
        table["date_range"].update(
            date_start="2026-07-01",
            date_end="2026-07-09",
        )
    else:
        table["date_range"] = {"date_start": None, "date_end": None, "value_count": 0}
        table["field_ranges"] = {"business_date": dict(table["date_range"])}
    table["row_count"] = len(rows)
    table["partition_source_field"] = None
    table["content_sha256"] = multiset_digest(rows, schema.names, tmp_path / "hash.sqlite")
    output = tmp_path / "facts" / name / "part-000000.parquet"
    output.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), output)
    table["files"] = [{"path": output.relative_to(tmp_path).as_posix(), "row_count": len(rows)}]
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / (name + ".arrow.json")).write_bytes(
        raw_json({"table": name, "schema": table["schema"]})
    )
    if tampered:
        key = "discontinue_date" if name == "product_catalog" else "business_date"
        table["field_ranges"][key]["value_count"] = 1
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    with sqlite3.connect(tmp_path / "dates.sqlite") as dates:
        if tampered:
            with pytest.raises(SnapshotError, match="typed_date_range_mismatch"):
                verify_table(tmp_path, table, scratch, dates, Limits())
        else:
            verify_table(tmp_path, table, scratch, dates, Limits())


def test_ai_output_cannot_masquerade_as_a_fact(copied: Path) -> None:
    document = manifest(copied)
    table = document["tables"][0]
    source = copied / table["files"][0]["path"]
    output = copied / "facts/forecasts/part-000000.parquet"
    output.parent.mkdir()
    source.rename(output)
    source.parent.rmdir()
    table["files"][0]["path"] = output.relative_to(copied).as_posix()
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="parquet_namespace_or_partition_path_mismatch"):
        verify_snapshot(copied)


def test_unsupported_canonicalization_and_provenance(copied: Path) -> None:
    document = manifest(copied)
    document["source"]["descriptor"]["versions"]["canonicalization"] = "other-1.0.0"
    reseal(copied, document)
    with pytest.raises(
        SnapshotError,
        match="unsupported_policy_or_unqualified_source|invalid_snapshot_manifest_schema",
    ):
        verify_snapshot(copied)
    document["source"]["descriptor"]["versions"]["canonicalization"] = (
        "typed-csv-nfc-utc-multiset-1.6.0"
    )
    document["source"]["descriptor"]["code_sha256"] = "a" * 64
    reseal(copied, document)
    with pytest.raises(SnapshotError, match="source_provenance_mismatch"):
        verify_snapshot(copied)


@pytest.mark.parametrize(
    "raw",
    [b'{"schema_version":"1.0.0","schema_version":"2.0.0"}', b'{"schema_version":NaN}', b"[]"],
)
def test_invalid_json_is_rejected(copied: Path, raw: bytes) -> None:
    (copied / "snapshot_manifest.json").write_bytes(raw)
    with pytest.raises(SnapshotError):
        verify_snapshot(copied)


def add_synthetic_truth(root: Path) -> None:
    """Four schema-valid rows exercise namespace isolation, not business qualification."""
    document = manifest(root)
    contract = json.loads((ROOT / "contracts/source_snapshot/v1/contract.json").read_text())
    document["descriptor"]["include_evaluation_truth"] = True
    truth = contract["evaluation_truth_tables"]
    source = document["source"]
    for name, spec in truth.items():
        row = {}
        for column in spec["schema"]:
            kind, key = column["type"], column["name"]
            row[key] = (
                date(2026, 7, 2)
                if kind.startswith("date")
                else datetime(2026, 7, 2, tzinfo=UTC)
                if kind.startswith("timestamp")
                else Decimal("1")
                if kind.startswith("decimal")
                else 1
                if kind == "int64"
                else True
                if kind == "bool"
                else "synthetic-" + key
            )
        schema = arrow_schema(spec["schema"])
        output = root / "evaluation_truth" / name / "part-000000.parquet"
        output.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([row], schema=schema), output)
        digest = multiset_digest([row], schema.names, root.parent / (name + ".sqlite"))
        ranges = {
            key: {"date_start": "2026-07-02", "date_end": "2026-07-02", "value_count": 1}
            for key, value in row.items()
            if isinstance(value, (date, datetime))
        }
        date_range = {
            "date_start": "2026-07-02" if ranges else None,
            "date_end": "2026-07-02" if ranges else None,
            "value_count": len(ranges),
        }
        logical = dict(
            table=name,
            data_class=spec["data_class"],
            grain=spec["grain"],
            schema=spec["schema"],
            row_count=1,
            content_sha256=digest,
            date_range=date_range,
            field_ranges=ranges,
        )
        document["tables"].append(
            dict(
                logical,
                partition_source_field=None,
                files=[
                    {
                        "path": output.relative_to(root).as_posix(),
                        "row_count": 1,
                        "bytes": 0,
                        "sha256": "a" * 64,
                    }
                ],
            )
        )
        artifact = next(a for a in source["artifacts"] if a["table"] == name)
        artifact.update({key: value for key, value in logical.items() if key not in {"schema"}})
        source["descriptor"]["tables"][name].update(row_count=1, content_sha256=digest)
        arrow = root / "schemas" / (name + ".arrow.json")
        arrow.write_bytes(raw_json({"table": name, "schema": spec["schema"]}) + b"\n")
        document["metadata_files"].append(
            {
                "path": arrow.relative_to(root).as_posix(),
                "bytes": arrow.stat().st_size,
                "sha256": hashlib.sha256(arrow.read_bytes()).hexdigest(),
            }
        )
    simulation = root / "schemas/retail_simulation.v1.schema.json"
    shutil.copy(ROOT / "contracts/source_snapshot/v1/retail_simulation.v1.schema.json", simulation)
    document["metadata_files"].append(
        {
            "path": simulation.relative_to(root).as_posix(),
            "bytes": simulation.stat().st_size,
            "sha256": hashlib.sha256(simulation.read_bytes()).hexdigest(),
        }
    )
    tables = {t["table"]: t for t in document["tables"]}
    document["tables"] = [tables[a["table"]] for a in source["artifacts"] if a["table"] in tables]
    document["descriptor"]["tables"] = [
        {
            key: t[key]
            for key in [
                "table",
                "data_class",
                "row_count",
                "content_sha256",
                "grain",
                "date_range",
                "field_ranges",
                "schema",
            ]
        }
        for t in document["tables"]
    ]
    document["descriptor"]["schemas"] = {
        r["path"]: r["sha256"]
        for r in document["metadata_files"]
        if r["path"].startswith("schemas/")
    }
    reseal(root, document)


def test_truth_requires_explicit_opt_in_and_stays_private(copied: Path, tmp_path: Path) -> None:
    add_synthetic_truth(copied)
    with pytest.raises(SnapshotError, match="evaluation_truth_requires_explicit_opt_in"):
        import_snapshot(copied, tmp_path / "data/generated")
    result = import_snapshot(copied, tmp_path / "data/generated", allow_evaluation_truth=True)
    assert len(result.snapshot.manifest["tables"]) == 29
    truth = result.directory / "snapshot/evaluation_truth"
    assert truth.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in truth.rglob("*.parquet"))
    with pytest.raises(SnapshotError, match="evaluation_truth_requires_explicit_opt_in"):
        verify_import(result.directory)
    assert not (result.directory / "snapshot/facts/demand_components_truth").exists()
