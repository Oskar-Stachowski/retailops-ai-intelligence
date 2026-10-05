"""Bounded reads, complete native parity and failed sealed input/publication."""

import shutil
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from test_stockout_features import native as native
from test_stockout_partitions import decoded_partition, reseal

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.feature_dataset import feature_implementation, load_features_input
from retailops_ai.stockout.features import feature_point
from retailops_ai.stockout_preparation.bundle import build_bundle, implementation, verify_bundle
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_storage import bundle, store
from retailops_ai.stockout_storage.bundle import DiskIndex, build_disk_bundle, verify_disk_bundle
from retailops_ai.stockout_storage.store import DiskFacts, StoragePolicy, keys


def test_native_disk_queries_and_single_series_cache_match_all_v1_origins(native, tmp_path):
    _, curated, features, _ = native
    _, records = load_features_input(curated.directory)
    expected = FactIndex(records)
    with DiskFacts(curated.directory, scratch=tmp_path) as facts:
        origins = facts.origins(10000)
        cached = DiskIndex(facts, {(p, s): t for p, s, t in origins})
        for body in reversed(features["points"]):
            point = FeaturePoint.model_validate_json(canonical_json(body))
            args = point.product_id, point.stock_location_id, point.as_of
            assert facts.known(*args) == expected.known(*args)
            assert cached.known(*args) == expected.known(*args)
            assert (
                feature_point(cached.known(*args), product=args[0], stock=args[1], as_of=args[2])
                == point
            )
        assert facts.stats["maximum_selected_rows"] < facts.stats["stored_rows"]
        assert facts.stats["maximum_selected_bytes"] <= facts.policy.max_selected_bytes
        assert facts.path.stat().st_mode & 0o777 == 0o600
        assert facts.path.parent.stat().st_mode & 0o777 == 0o700
        path = facts.path
        with pytest.raises(SnapshotError, match="origin_outside"):
            cached.known(args[0], args[1], origins[-1][2] + timedelta(days=100))
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed"):
        facts.known(*args)
    with pytest.raises(SnapshotError, match="single_use"):
        facts.__enter__()


@pytest.mark.parametrize(
    "table",
    [
        "daily_demand_versions",
        "delivery_plan_versions",
        "inventory_ledger",
        "replenishment_orders",
        "replenishment_receipts",
        "inventory_daily_snapshots",
    ],
)
def test_exact_knowledge_boundaries_include_all_historical_versions(native, tmp_path, table):
    _, curated, _, _ = native
    _, records = load_features_input(curated.directory)
    expected = FactIndex(records)
    with DiskFacts(curated.directory, scratch=tmp_path) as facts:
        p, s, _ = facts.origins(10000)[0]
        relevant = [r for r in records[table] if keys(table, r)[2] is not None]
        assert relevant
        for row in relevant[:4]:
            cutoff = datetime.fromisoformat(keys(table, row)[2])
            for delta in (-1, 0, 1):
                origin = cutoff + timedelta(microseconds=delta)
                assert facts.known(p, s, origin) == expected.known(p, s, origin)


def test_disk_bundle_layout_matches_v2_and_retains_old_code_identities(native, tmp_path):
    _, curated, features, _ = native
    old = (feature_implementation(), implementation())
    previous = tmp_path / "v2"
    doc2, _ = build_bundle(curated.directory, previous)
    target = tmp_path / "disk"
    doc, status = build_disk_bundle(curated.directory, target)
    assert status == "published" and doc["descriptor"]["schema_version"] == "2.1.0"
    assert verify_disk_bundle(target, curated.directory) == doc
    assert build_disk_bundle(curated.directory, target) == (doc, "reused")
    assert [
        p for spec in doc["descriptor"]["partitions"] for p in decoded_partition(target, spec)
    ] == features["points"]
    assert doc["descriptor"]["points_sha256"] == features["descriptor"]["points_sha256"]
    assert doc["descriptor"]["partitions"] == doc2["descriptor"]["partitions"]
    assert doc["feature_bundle_id"] != doc2["feature_bundle_id"]
    assert (feature_implementation(), implementation()) == old
    assert verify_bundle(previous, curated.directory) == doc2
    with pytest.raises(SnapshotError, match="version_required"):
        verify_disk_bundle(previous, curated.directory)
    assert not list(tmp_path.glob(".stockout-*"))


@pytest.mark.parametrize(
    "field,value", [("max_selected_rows", 1), ("max_selected_bytes", 1), ("max_db_bytes", 4096)]
)
def test_tighter_resource_bound_refuses_output_and_removes_private_scratch(
    native, tmp_path, field, value
):
    _, curated, _, _ = native
    with pytest.raises(SnapshotError, match="resource"):
        build_disk_bundle(
            curated.directory,
            tmp_path / "out",
            storage_policy=StoragePolicy.model_validate({field: value}),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["payload", "index"])
def test_private_row_or_rehashed_index_mutation_is_rejected(native, tmp_path, mutation):
    _, curated, _, _ = native
    with DiskFacts(curated.directory, scratch=tmp_path) as facts:
        p, s, t = facts.origins(10000)[-1]
        db = facts._db
        db.execute("PRAGMA query_only=OFF")
        if mutation == "payload":
            db.execute(
                "UPDATE facts SET payload=? WHERE table_name='product_catalog' AND product=?",
                (b"corrupt", p),
            )
        else:
            # Payload checksum can remain correct; the stored index must also match it.
            db.execute(
                "UPDATE facts SET ready=? WHERE table_name='product_catalog' AND product=?",
                ("0000", p),
            )
        with pytest.raises(SnapshotError, match="mismatch"):
            facts.known(p, s, t)


def test_cursor_uses_knowledge_prefix_index_without_sorting_all_rows(native, tmp_path):
    _, curated, _, _ = native
    with DiskFacts(curated.directory, scratch=tmp_path) as facts:
        p, s, t = facts.origins(10000)[-1]
        plan = list(
            facts._db.execute(
                "EXPLAIN QUERY PLAN SELECT position,ready,payload,checksum FROM facts WHERE table_name=? AND product IS ? AND stock IS ? AND ready<=? ORDER BY ready,position LIMIT ?",
                (
                    "inventory_ledger",
                    p,
                    s,
                    t.isoformat(timespec="microseconds"),
                    facts.policy.max_selected_rows + 1,
                ),
            )
        )
        assert any("USING INDEX facts_lookup" in line[-1] for line in plan)
        assert not any("TEMP B-TREE" in line[-1] for line in plan)


def test_read_changed_between_verification_and_table_seal_is_rejected(
    native, tmp_path, monkeypatch
):
    _, curated, _, _ = native
    real = store.iter_rows

    def changed(root, files, batch):
        for row in real(root, files, batch):
            if "/inventory_daily_snapshots/" in files[0]["path"]:
                row = {**row, "on_hand": row["on_hand"] + 1}
            yield row

    monkeypatch.setattr(store, "iter_rows", changed)
    with pytest.raises(SnapshotError, match="changed_during_read"):
        build_disk_bundle(curated.directory, tmp_path / "out")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mutation", ["unused_table", "manifest", "extra_file"])
def test_full_parent_recheck_catches_changes_outside_feature_projection(
    native, tmp_path, monkeypatch, mutation
):
    _, curated, _, _ = native
    source = tmp_path / "curated"
    shutil.copytree(curated.directory, source)
    real = store.iter_rows

    def changed(root, files, batch):
        yield from real(root, files, batch)
        if "/replenishment_receipts/" in files[0]["path"]:
            if mutation == "unused_table":
                path = next((source / "curated/sales").glob("*.parquet"))
                path.write_bytes(path.read_bytes() + b"changed")
            elif mutation == "manifest":
                path = source / "curated_manifest.json"
                path.write_bytes(path.read_bytes() + b" ")
            else:
                (source / "unexpected").write_bytes(b"extra")

    monkeypatch.setattr(store, "iter_rows", changed)
    with pytest.raises(SnapshotError):
        build_disk_bundle(source, tmp_path / "out")
    assert not (tmp_path / "out").exists() and not list(tmp_path.glob(".stockout-*"))


def test_failed_partition_write_and_retry_keep_both_input_and_output_immutable(
    native, tmp_path, monkeypatch
):
    _, curated, _, _ = native
    target = tmp_path / "out"
    real = bundle.write_private

    def failed(path, raw):
        if path.name == "points.parquet":
            raise OSError("interrupted")
        real(path, raw)

    monkeypatch.setattr(bundle, "write_private", failed)
    with pytest.raises(OSError, match="interrupted"):
        build_disk_bundle(curated.directory, target)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(bundle, "write_private", real)
    document, status = build_disk_bundle(curated.directory, target)
    assert status == "published"
    with pytest.raises(SnapshotError):
        build_disk_bundle(curated.directory, target, storage_policy=StoragePolicy(batch_rows=1))
    assert verify_disk_bundle(target, curated.directory) == document


@pytest.mark.parametrize(
    "mutation", ["resealed_file", "resealed_seal", "duplicate_partition", "missing", "extra"]
)
def test_resealed_or_incomplete_disk_bundle_is_rejected(native, tmp_path, mutation):
    _, curated, _, _ = native
    target = tmp_path / "out"
    document, _ = build_disk_bundle(curated.directory, target)
    file = document["descriptor"]["partitions"][-1]["files"][-1]
    if mutation == "resealed_file":
        (target / file["path"]).write_bytes(b"corrupt")
    elif mutation == "resealed_seal":
        document["descriptor"]["storage"]["input_seal"]["ordered_rows_sha256"] = "0" * 64
    elif mutation == "duplicate_partition":
        document["descriptor"]["partitions"].append(document["descriptor"]["partitions"][0])
    elif mutation == "missing":
        (target / file["path"]).unlink()
    else:
        (target / "unexpected").write_bytes(b"extra")
    if mutation.startswith("resealed") or mutation == "duplicate_partition":
        reseal(target, document)
    with pytest.raises((SnapshotError, OSError)):
        verify_disk_bundle(target, curated.directory)


def test_closed_input_symlinks_and_scratch_inside_input_are_rejected(native, tmp_path):
    _, curated, _, _ = native
    with pytest.raises(SnapshotError, match="scratch_inside"):
        with DiskFacts(curated.directory, scratch=curated.directory):
            pass
    link = tmp_path / "link"
    link.symlink_to(curated.directory, target_is_directory=True)
    with pytest.raises(SnapshotError, match="symlink"):
        build_disk_bundle(link, tmp_path / "out")
    with pytest.raises(SnapshotError):
        build_disk_bundle(curated.directory, curated.directory / "out")
    assert not (curated.directory / "out").exists()


@pytest.mark.parametrize(
    "values",
    [
        {"batch_rows": 513},
        {"batch_rows": True},
        {"max_selected_rows": 20001},
        {"max_selected_bytes": store.MAX_SELECTED_BYTES + 1},
        {"max_db_bytes": store.MAX_DB_BYTES + 1},
    ],
)
def test_policy_cannot_silently_increase_budgets(values):
    with pytest.raises(ValidationError):
        StoragePolicy.model_validate(values)
