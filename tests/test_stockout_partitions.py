"""Native parity, causal index boundaries and failed immutable publication."""

import hashlib
from copy import deepcopy
from datetime import timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from test_stockout_features import ORIGIN, row
from test_stockout_features import native as native
from test_stockout_features import records as records

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256, read_json
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.feature_dataset import feature_implementation, load_features_input
from retailops_ai.stockout.features import feature_point
from retailops_ai.stockout_preparation import bundle
from retailops_ai.stockout_preparation.bundle import (
    PartitionPolicy,
    build_bundle,
    iter_verified_points,
    verify_bundle,
)
from retailops_ai.stockout_preparation.index import FactIndex


def indexed(records, origin=ORIGIN):
    return feature_point(
        FactIndex(records).known("product", "stock", origin),
        product="product",
        stock="stock",
        as_of=origin,
    )


def original(records, origin=ORIGIN):
    return feature_point(records, product="product", stock="stock", as_of=origin)


def reseal(target, document):
    for spec in document["descriptor"]["partitions"]:
        for file in spec["files"]:
            raw = (target / file["path"]).read_bytes()
            file.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    document["feature_bundle_id"] = "feature-partitions-sha256-" + json_sha256(
        document["descriptor"]
    )
    (target / "manifest.json").write_bytes(canonical_json(document))


def decoded_partition(target, spec):
    tables = {
        file["role"]: pq.read_table(target / file["path"]).to_pylist() for file in spec["files"]
    }
    refs = {
        role: {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in tables[role]}
        for role in ("history", "lineage")
    }
    result = []
    for point in tables["points"]:
        for role in refs:
            point[role] = tuple(refs[role][key] for key in point.pop(role + "_refs"))
        result.append(FeaturePoint.model_validate(point).model_dump(mode="json"))
    return result


def test_native_normalized_partitions_roundtrip_and_v1_identity(native, tmp_path):
    _, curated, features, _ = native
    before = feature_implementation()
    target = tmp_path / "bundle"
    doc, status = build_bundle(
        curated.directory, target, partition_policy=PartitionPolicy(batch_origins=7)
    )
    assert status == "published"
    assert verify_bundle(target, curated.directory) == doc
    actual = [
        p for spec in doc["descriptor"]["partitions"] for p in decoded_partition(target, spec)
    ]
    assert actual == features["points"]
    assert [
        p.model_dump(mode="json") for p in iter_verified_points(target, curated.directory)
    ] == actual
    assert doc["descriptor"]["points_sha256"] == features["descriptor"]["points_sha256"]
    assert doc["report"]["statuses"] == features["report"]["statuses"]
    assert feature_implementation() == before
    for spec in doc["descriptor"]["partitions"]:
        assert 0 < spec["rows"] <= 7
        assert sum(f["bytes"] for f in spec["files"]) <= bundle.MAX_PARTITION_BYTES
        assert all((target / f["path"]).stat().st_mode & 0o777 == 0o600 for f in spec["files"])
        history = pq.read_table(
            target / next(f["path"] for f in spec["files"] if f["role"] == "history")
        )
        assert history.num_rows < spec["rows"] * 28
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "manifest.json").stat().st_mode & 0o777 == 0o600


def test_native_index_matches_every_origin_in_reverse_order(native):
    _, curated, features, _ = native
    _, records = load_features_input(curated.directory)
    index = FactIndex(records)
    for body in reversed(features["points"]):
        point = FeaturePoint.model_validate_json(canonical_json(body))
        result = feature_point(
            index.known(point.product_id, point.stock_location_id, point.as_of),
            product=point.product_id,
            stock=point.stock_location_id,
            as_of=point.as_of,
        )
        assert result == point


def test_late_correction_keeps_all_known_versions_and_never_rewrites_past(records):
    correction = {**records["daily_demand_versions"][-2], "version": 2, "observed_units": 17}
    correction = row(
        available=ORIGIN + timedelta(seconds=1),
        **{
            k: v
            for k, v in correction.items()
            if k not in {"curated_available_at", "source_record_sha256"}
        },
    )
    old = indexed(records)
    records["daily_demand_versions"].append(correction)
    assert indexed(records) == old
    future = ORIGIN + timedelta(seconds=1)
    assert indexed(records, future) == original(records, future)
    assert indexed(records, future).values.observed_sales_mean > old.values.observed_sales_mean
    lineage = {r.table: r for r in indexed(records, future).lineage}
    assert lineage["daily_demand_versions"].rows == len(records["daily_demand_versions"])


@pytest.mark.parametrize(
    "gate",
    ["curated_available_at", "occurred_at", "snapshot_at", "ordered_at", "received_at", "known_at"],
)
def test_each_future_timestamp_is_excluded_then_admitted_at_boundary(records, gate):
    incoming = row(
        product_id="product",
        stock_location_id="stock",
        replenishment_order_id="another",
        ordered_quantity=5,
        ordered_at=ORIGIN,
        expected_delivery_at=ORIGIN + timedelta(days=2),
    )
    incoming[gate] = ORIGIN + timedelta(seconds=1)
    records["replenishment_orders"].append(incoming)
    assert indexed(records) == original(records)
    assert indexed(records, ORIGIN + timedelta(seconds=1)) == original(
        records, ORIGIN + timedelta(seconds=1)
    )


def test_missing_availability_future_business_day_and_other_series_are_ignored(records):
    before = indexed(records)
    records["inventory_daily_snapshots"].extend(
        [
            {**records["inventory_daily_snapshots"][0], "curated_available_at": None},
            {
                **records["inventory_daily_snapshots"][0],
                "product_id": "other",
                "available_qty": 999,
            },
            {
                **records["inventory_daily_snapshots"][0],
                "stock_location_id": "other",
                "available_qty": 999,
            },
        ]
    )
    records["daily_demand_versions"].append(
        {**records["daily_demand_versions"][0], "business_date": ORIGIN.date() + timedelta(days=1)}
    )
    assert indexed(records) == original(records) == before


def test_stale_snapshot_and_future_supply_versions_keep_v1_semantics(records):
    records["inventory_daily_snapshots"][0]["snapshot_at"] -= timedelta(days=1, seconds=1)
    assert indexed(records) == original(records)
    assert indexed(records).reason == "stale_inventory_snapshot"
    plan = {
        "replenishment_order_id": "order",
        "expected_delivery_at": ORIGIN + timedelta(days=3),
        "source_record_sha256": "0" * 64,
        "version": 2,
        "curated_available_at": ORIGIN + timedelta(days=1),
    }
    records["delivery_plan_versions"].append(plan)
    assert indexed(records) == original(records)


def test_ambiguous_known_revision_still_blocks(records):
    records["daily_demand_versions"].append(dict(records["daily_demand_versions"][0]))
    with pytest.raises(ValueError, match="ambiguous"):
        indexed(records)


def test_index_owns_rows_and_does_not_expose_mutable_state(records):
    index = FactIndex(records)
    first = index.known("product", "stock", ORIGIN)
    expected = deepcopy(first)
    records["inventory_daily_snapshots"][0]["available_qty"] = 999
    first["inventory_daily_snapshots"][0]["available_qty"] = 555
    assert index.known("product", "stock", ORIGIN) == expected


@pytest.mark.parametrize(
    "case", ["missing", "extra", "duplicate", "report", "corrupt", "dangling_reference"]
)
def test_broken_or_resealed_bundle_cannot_pass_full_replay(native, tmp_path, case):
    _, curated, _, _ = native
    target = tmp_path / "bundle"
    doc, _ = build_bundle(curated.directory, target)
    file = doc["descriptor"]["partitions"][-1]["files"][-1]
    if case == "missing":
        (target / file["path"]).unlink()
    elif case == "extra":
        (target / "extra").write_bytes(b"unexpected")
    elif case == "duplicate":
        doc["descriptor"]["partitions"].append(deepcopy(doc["descriptor"]["partitions"][0]))
        reseal(target, doc)
    elif case == "report":
        doc["report"]["model_ready"] = True
        reseal(target, doc)
    elif case == "corrupt":
        (target / file["path"]).write_bytes(b"corrupt")
        reseal(target, doc)
    else:
        table = pq.read_table(target / file["path"])
        rows = table.to_pylist()
        rows[0]["history_refs"][0] = "0" * 64
        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), target / file["path"])
        reseal(target, doc)
    with pytest.raises((SnapshotError, OSError)):
        verify_bundle(target, curated.directory)
    with pytest.raises((SnapshotError, OSError)):
        next(iter_verified_points(target, curated.directory))


def test_failed_partial_write_is_unpublished_and_retry_reuses_complete_bundle(
    native, tmp_path, monkeypatch
):
    _, curated, _, _ = native
    target = tmp_path / "bundle"
    real = bundle.write_private
    calls = 0

    def interrupted(path, raw):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated_interruption")
        real(path, raw)

    monkeypatch.setattr(bundle, "write_private", interrupted)
    with pytest.raises(OSError, match="simulated"):
        build_bundle(curated.directory, target)
    assert not target.exists() and list(tmp_path.iterdir()) == []
    monkeypatch.setattr(bundle, "write_private", real)
    doc, status = build_bundle(curated.directory, target)
    assert status == "published"
    assert build_bundle(curated.directory, target) == (doc, "reused")
    with pytest.raises(SnapshotError):
        build_bundle(curated.directory, target, partition_policy=PartitionPolicy(batch_origins=7))
    assert read_json(target, "manifest.json") == doc


@pytest.mark.parametrize("limit", ["partition", "bundle", "points", "manifest"])
def test_bounds_fail_before_publication(native, tmp_path, monkeypatch, limit):
    _, curated, _, _ = native
    target = tmp_path / "bundle"
    policy = PartitionPolicy()
    if limit == "partition":
        policy = PartitionPolicy(max_partition_bytes=1)
    else:
        monkeypatch.setattr(
            bundle,
            {
                "bundle": "MAX_BUNDLE_BYTES",
                "points": "MAX_FEATURE_POINTS",
                "manifest": "MAX_METADATA_BYTES",
            }[limit],
            1,
        )
    with pytest.raises(SnapshotError, match="limit"):
        build_bundle(curated.directory, target, partition_policy=policy)
    assert not target.exists() and list(tmp_path.iterdir()) == []


def test_symlinks_unsafe_parent_and_output_inside_input_are_rejected(native, tmp_path):
    _, curated, _, _ = native
    target = tmp_path / "bundle"
    target.symlink_to(curated.directory, target_is_directory=True)
    with pytest.raises((SnapshotError, OSError)):
        build_bundle(curated.directory, target)
    target.unlink()
    parent = tmp_path / "link"
    parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SnapshotError):
        build_bundle(curated.directory, parent / "bundle")
    with pytest.raises(SnapshotError):
        build_bundle(curated.directory, curated.directory / "bundle")
    assert not (curated.directory / "bundle").exists()


def test_partition_changed_after_verify_is_rechecked_before_first_yield(
    native, tmp_path, monkeypatch
):
    _, curated, _, _ = native
    target = tmp_path / "bundle"
    doc, _ = build_bundle(curated.directory, target)
    real = bundle.verify_bundle

    def changed(path, parent):
        result = real(path, parent)
        file = doc["descriptor"]["partitions"][0]["files"][0]
        (target / file["path"]).write_bytes(b"changed")
        return result

    monkeypatch.setattr(bundle, "verify_bundle", changed)
    with pytest.raises(SnapshotError, match="full_replay"):
        next(iter_verified_points(target, curated.directory))


@pytest.mark.parametrize(
    "values",
    [
        {"batch_origins": 0},
        {"batch_origins": 257},
        {"batch_origins": True},
        {"max_partition_bytes": bundle.MAX_PARTITION_BYTES + 1},
    ],
)
def test_unbounded_or_noninteger_policy_is_rejected(values):
    with pytest.raises(ValidationError):
        PartitionPolicy.model_validate(values)
