"""Sealed global origin panels, full upstream parity and bounded failed publication."""

import hashlib
import shutil
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_stockout_features import native as native
from test_stockout_upstream import AS_OF
from test_stockout_upstream import routed as routed
from test_stockout_upstream import tables as tables

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_json
from retailops_ai.stockout.feature_dataset import load_features_input, read_sealed_tables
from retailops_ai.stockout.upstream import UPSTREAM_TABLES, upstream_point
from retailops_ai.stockout.upstream_contract import DEFAULT_UPSTREAM_POLICY, UpstreamPoint
from retailops_ai.stockout.upstream_dataset import build_upstream
from retailops_ai.stockout_history.bundle import build_history_bundle
from retailops_ai.stockout_upstream_storage import bundle, store
from retailops_ai.stockout_upstream_storage.bundle import (
    DEFAULT_PARTITION_POLICY,
    UpstreamPartitionPolicy,
    UpstreamPreparation,
    build_upstream_bundle,
    iter_verified_upstream,
    verify_upstream_bundle,
)
from retailops_ai.stockout_upstream_storage.store import UpstreamFacts, UpstreamStoragePolicy


@pytest.fixture(scope="module")
def bounded(native, tmp_path_factory):
    _, curated, features, _ = native
    parent = tmp_path_factory.mktemp("upstream-feature-parent") / "features"
    build_history_bundle(curated.directory, parent)
    target = parent.parent / "upstream"
    document, _ = build_upstream_bundle(curated.directory, parent, target)
    return curated.directory, parent, target, document, build_upstream(curated.directory, features)


def json_points(target, document):
    import pyarrow.parquet as pq

    points = []
    for part in document["descriptor"]["partitions"]:
        for p in pq.read_table(target / part["path"]).to_pylist():
            p["series"] = tuple({**s, "daily_units": tuple(s["daily_units"])} for s in p["series"])
            points.append(UpstreamPoint.model_validate(p).model_dump(mode="json"))
    return points


def physical(points):
    return sorted(points, key=lambda p: (p["product_id"], p["stock_location_id"], p["as_of"]))


def test_all_native_fields_and_lineage_equal_v1_and_replay_reuses(bounded):
    curated, parent, target, doc, old = bounded
    points = json_points(target, doc)
    assert physical(points) == old["points"]
    assert doc["descriptor"]["points_sha256"] == hashlib.sha256(canonical_json(points)).hexdigest()
    assert verify_upstream_bundle(target, curated, parent) == doc
    assert build_upstream_bundle(curated, parent, target) == (doc, "reused")
    assert [
        p.model_dump(mode="json") for p in iter_verified_upstream(target, curated, parent)
    ] == points
    for key in old["report"]:
        assert doc["report"][key] == old["report"][key]
    assert (
        doc["descriptor"]["feature_bundle_id"]
        == read_json(parent, "manifest.json")["feature_bundle_id"]
    )
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())


def test_each_table_at_exact_knowledge_boundaries_matches_full_facts(native, tmp_path):
    _, curated, _, _ = native
    doc, _ = load_features_input(curated.directory)
    records = read_sealed_tables(curated.directory, doc, UPSTREAM_TABLES)
    with UpstreamFacts(curated.directory, scratch=tmp_path) as facts:
        for table in UPSTREAM_TABLES:
            row = next(r for r in records[table] if r["curated_available_at"] is not None)
            for offset in (-1, 0, 1):
                cutoff = row["curated_available_at"] + timedelta(microseconds=offset)
                assert facts.known(cutoff) == {
                    n: [
                        r
                        for r in rows
                        if r["curated_available_at"] is not None
                        and r["curated_available_at"] <= cutoff
                    ]
                    for n, rows in records.items()
                }
        assert facts.path.stat().st_mode & 0o777 == 0o600
        assert facts.path.parent.stat().st_mode & 0o777 == 0o700
        path = facts.path
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed"):
        facts.known(cutoff)
    with pytest.raises(SnapshotError, match="single_use"):
        facts.__enter__()


@pytest.mark.parametrize(
    "field,value", [("max_selected_rows", 1), ("max_selected_bytes", 1), ("max_db_bytes", 4096)]
)
def test_resource_limits_remove_scratch_and_do_not_publish(bounded, tmp_path, field, value):
    curated, parent, _, _, _ = bounded
    with pytest.raises(SnapshotError, match="resource"):
        build_upstream_bundle(
            curated,
            parent,
            tmp_path / "output",
            storage_policy=UpstreamStoragePolicy.model_validate({field: value}),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "policy",
    [
        {"max_selected_rows": 20001},
        {"max_db_bytes": 134217729},
        {"batch_rows": 513},
        {"cache_kib": 16384},
    ],
)
def test_policy_cannot_silently_raise_limits(policy):
    with pytest.raises(ValidationError):
        UpstreamStoragePolicy.model_validate(policy)


@pytest.mark.parametrize("mutation", ["payload", "index"])
def test_private_database_corruption_is_rejected(native, tmp_path, mutation):
    _, curated, _, _ = native
    with UpstreamFacts(curated.directory, scratch=tmp_path) as facts:
        db = facts._db
        db.execute("PRAGMA query_only=OFF")
        if mutation == "payload":
            db.execute(
                "UPDATE facts SET payload=? WHERE table_name='product_catalog'", (b"corrupt",)
            )
        else:
            db.execute(
                "UPDATE facts SET ready='2000-01-01T00:00:00.000000+00:00' WHERE table_name='product_catalog'"
            )
        db.commit()
        with pytest.raises(SnapshotError, match="checksum|index_mismatch"):
            facts.known(datetime(2099, 4, 1, tzinfo=AS_OF.tzinfo))


@pytest.mark.parametrize("mutation", ["part", "missing", "extra", "manifest", "resealed"])
def test_full_replay_rejects_changed_output_before_reader_first_yield(bounded, tmp_path, mutation):
    curated, parent, target, doc, _ = bounded
    copy = tmp_path / "copy"
    shutil.copytree(target, copy)
    last = copy / doc["descriptor"]["partitions"][-1]["path"]
    if mutation == "missing":
        last.unlink()
    elif mutation == "extra":
        (copy / "extra").write_bytes(b"x")
    elif mutation == "part":
        last.write_bytes(b"damaged")
    else:
        body = deepcopy(doc)
        body["report"]["base_eligible_with_forecast"] += 1
        if mutation == "resealed":
            body["descriptor"]["upstream_model_version"] = "baseline-sha256-" + "0" * 64
            body["upstream_bundle_id"] = (
                "upstream-partitions-sha256-"
                + hashlib.sha256(canonical_json(body["descriptor"])).hexdigest()
            )
        (copy / "manifest.json").write_bytes(canonical_json(body))
    with pytest.raises((SnapshotError, OSError)):
        next(iter_verified_upstream(copy, curated, parent))


@pytest.mark.parametrize("mutation", ["manifest", "part", "feature_parent"])
def test_second_pass_rechecks_before_next_part(bounded, tmp_path, mutation):
    curated, parent, target, doc, _ = bounded
    copy, feature_copy = tmp_path / "copy", tmp_path / "features"
    shutil.copytree(target, copy)
    shutil.copytree(parent, feature_copy)
    reader = iter_verified_upstream(copy, curated, feature_copy)
    first_rows = doc["descriptor"]["partitions"][0]["rows"]
    for _ in range(first_rows):
        next(reader)
    if mutation == "manifest":
        (copy / "manifest.json").write_bytes(b"{}")
    elif mutation == "part":
        (copy / doc["descriptor"]["partitions"][1]["path"]).write_bytes(b"changed")
    else:
        part = read_json(feature_copy, "manifest.json")["descriptor"]["partitions"][0]["files"][0]
        (feature_copy / part["path"]).write_bytes(b"changed")
    with pytest.raises(SnapshotError):
        next(reader)
    reader.close()


@pytest.mark.parametrize("mutation", ["missing", "extra", "manifest", "part"])
def test_feature_parent_full_replay_precedes_output(bounded, tmp_path, mutation):
    curated, parent, _, _, _ = bounded
    copy = tmp_path / "features"
    shutil.copytree(parent, copy)
    ref = read_json(copy, "manifest.json")["descriptor"]["partitions"][-1]["files"][0]
    if mutation == "missing":
        (copy / ref["path"]).unlink()
    elif mutation == "extra":
        (copy / "extra").write_bytes(b"x")
    elif mutation == "part":
        (copy / ref["path"]).write_bytes(b"wrong")
    else:
        raw = read_json(copy, "manifest.json")
        raw["descriptor"]["points_sha256"] = "0" * 64
        (copy / "manifest.json").write_bytes(canonical_json(raw))
    with pytest.raises((SnapshotError, OSError)):
        build_upstream_bundle(curated, copy, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def preparation(routed):
    facts = SimpleNamespace(
        document={"curated_dataset_id": "curated"},
        known=lambda origin: {
            n: [r for r in rows if r["curated_available_at"] <= origin]
            for n, rows in routed.items()
        },
    )
    return UpstreamPreparation(
        facts,
        {"descriptor": {"curated_dataset_id": "curated"}},
        [("p-1", "stock", AS_OF, True)],
        DEFAULT_UPSTREAM_POLICY,
        DEFAULT_PARTITION_POLICY,
    )


def test_known_future_calendar_and_late_revision_keep_frozen_origin(routed):
    before = upstream_point(routed, product="p-1", stock="stock", as_of=AS_OF)
    routed["daily_demand_versions"].append(
        {
            **routed["daily_demand_versions"][0],
            "version": 99,
            "observed_units": 999999,
            "curated_available_at": AS_OF,
        }
    )
    prepared = preparation(routed)
    points, _, _ = next(prepared.partitions())
    assert points == [before]
    assert points[0].forecast_units_7d is not None
    with pytest.raises(SnapshotError, match="single_use"):
        next(prepared.partitions())


def test_foreign_routing_ambiguity_is_not_hidden_by_physical_filter(routed):
    routed["fulfillment_routes"].append(
        {**routed["fulfillment_routes"][0], "route_key": "foreign", "stock_location_id": "other"}
    )
    with pytest.raises(SnapshotError, match="ambiguous_physical"):
        next(preparation(routed).partitions())


def test_batch_and_total_output_limits_fail_without_publishing(bounded, tmp_path, monkeypatch):
    curated, parent, _, _, _ = bounded
    with pytest.raises(SnapshotError, match="partition_byte_limit"):
        build_upstream_bundle(
            curated,
            parent,
            tmp_path / "out",
            partition_policy=UpstreamPartitionPolicy(max_partition_bytes=1),
        )
    monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", 1)
    with pytest.raises(SnapshotError, match="bundle_byte_limit"):
        build_upstream_bundle(curated, parent, tmp_path / "out")
    assert list(tmp_path.iterdir()) == []


def test_input_changed_after_verification_blocks_database_seal(native, tmp_path, monkeypatch):
    _, curated, _, _ = native
    copy = tmp_path / "curated"
    shutil.copytree(curated.directory, copy)
    original = store.iter_rows
    changed = False

    def changing(*args):
        nonlocal changed
        yield from original(*args)
        if not changed:
            changed = True
            (copy / "extra").write_bytes(b"x")

    monkeypatch.setattr(store, "iter_rows", changing)
    with pytest.raises(SnapshotError):
        with UpstreamFacts(copy):
            pass
