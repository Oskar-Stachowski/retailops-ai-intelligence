"""Physical label parity, private bounded storage and complete verified consumption."""

import hashlib
import shutil
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from test_stockout_labels import native as native

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256, read_json
from retailops_ai.stockout.contract import DEFAULT_POLICY, LabelPoint, LabelPolicy
from retailops_ai.stockout.dataset import implementation, verify_labels, write_labels
from retailops_ai.stockout_label_partitions import bundle, store
from retailops_ai.stockout_label_partitions.bundle import (
    LabelPartitionPolicy,
    LabelPreparation,
    build_label_bundle,
    iter_verified_labels,
    verify_label_bundle,
)
from retailops_ai.stockout_label_partitions.schema import schema
from retailops_ai.stockout_label_partitions.store import LabelFacts, LabelStoragePolicy, window_keys


def test_window_order_uses_utc_instant_instead_of_clock_spelling():
    clocks = [
        "2026-01-02T00:00:00.000001Z",
        "2026-01-02T00:00:00Z",
        "2026-01-02T00:00:00.1+00:00",
        "2026-01-02T00:00:00+00:00",
        "2026-01-02T00:00:00.000001+00:00",
    ]
    windows = [
        dict(product_id="product", stock_location_id="stock", origin=clock) for clock in clocks
    ]
    assert sorted(windows, key=window_keys) == sorted(
        windows, key=lambda w: datetime.fromisoformat(w["origin"])
    )
    assert window_keys(windows[0]) == window_keys(windows[-1])
    assert window_keys(windows[1]) == window_keys(windows[3])
    with pytest.raises(ValueError, match="utc_timestamp_required"):
        window_keys({**windows[0], "origin": "2026-01-02T01:00:00+01:00"})


def decoded(target, document):
    return [
        LabelPoint.model_validate(row).model_dump(mode="json")
        for spec in document["descriptor"]["partitions"]
        for row in pq.read_table(target / spec["path"]).to_pylist()
    ]


def reseal(target, document):
    for spec in document["descriptor"]["partitions"]:
        path = target / spec["path"]
        if path.is_file():
            raw = path.read_bytes()
            spec.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    document["label_bundle_id"] = "label-partitions-sha256-" + json_sha256(document["descriptor"])
    (target / bundle.MANIFEST).write_bytes(canonical_json(document) + b"\n")


@pytest.fixture(scope="module")
def prepared(native, tmp_path_factory):
    root, old = native
    target = tmp_path_factory.mktemp("labels-partitioned") / "bundle"
    document, status = build_label_bundle(
        root / "private",
        target,
        allow_evaluation_truth=True,
        partition_policy=LabelPartitionPolicy(batch_origins=7),
    )
    assert status == "published"
    return target, document, root / "private", old


def test_native_all_label_fields_and_old_identity_match(prepared, tmp_path):
    target, document, source, old = prepared
    identity = implementation()
    assert decoded(target, document) == old["points"]
    assert document["descriptor"]["points_sha256"] == old["descriptor"]["points_sha256"]
    assert document["report"]["statuses"] == old["report"]["statuses"]
    assert document["report"]["positive_labels"] == old["report"]["positive_labels"]
    assert document["report"]["negative_labels"] == old["report"]["negative_labels"]
    assert verify_label_bundle(target, source, allow_evaluation_truth=True) == document
    assert build_label_bundle(
        source,
        target,
        allow_evaluation_truth=True,
        partition_policy=LabelPartitionPolicy(batch_origins=7),
    ) == (document, "reused")
    assert [
        p.model_dump(mode="json")
        for p in iter_verified_labels(target, source, allow_evaluation_truth=True)
    ] == old["points"]
    legacy = tmp_path / "labels.json"
    write_labels(old, legacy)
    assert verify_labels(legacy, source, allow_evaluation_truth=True) == old
    assert implementation() == identity
    assert document["report"]["model_ready"] is False
    assert document["report"]["full_profile_ready"] is False
    assert target.stat().st_mode & 0o777 == 0o700
    for path in target.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    for spec in document["descriptor"]["partitions"]:
        assert spec["rows"] <= 7
        assert pq.read_schema(target / spec["path"]).equals(schema(), check_metadata=True)


def test_private_fact_selection_single_use_and_cleanup(native, tmp_path):
    root, old = native
    with LabelFacts(
        root / "private", DEFAULT_POLICY, allow_evaluation_truth=True, scratch=tmp_path
    ) as facts:
        preparation = LabelPreparation(facts, LabelPartitionPolicy(batch_origins=3))
        with pytest.raises(SnapshotError, match="incomplete"):
            preparation.manifest()
        actual = [
            p.model_dump(mode="json") for points, _, _ in preparation.partitions() for p in points
        ]
        assert actual == old["points"]
        assert facts.stats["maximum_selected_rows"] < facts.stats["stored_rows"]
        assert facts.stats["maximum_selected_bytes"] <= facts.policy.max_selected_bytes
        assert facts.path.stat().st_mode & 0o777 == 0o600
        assert facts.path.parent.stat().st_mode & 0o777 == 0o700
        path = facts.path
        with pytest.raises(SnapshotError, match="single_use"):
            list(preparation.partitions())
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed"):
        facts.ledger("product", "stock")
    with pytest.raises(SnapshotError, match="single_use"):
        facts.__enter__()


@pytest.mark.parametrize(
    "field,value", [("max_selected_rows", 1), ("max_selected_bytes", 1), ("max_db_bytes", 4096)]
)
def test_storage_resource_failure_removes_all_own_scratch(native, tmp_path, field, value):
    root, _ = native
    with pytest.raises(SnapshotError, match="resource"):
        build_label_bundle(
            root / "private",
            tmp_path / "bundle",
            allow_evaluation_truth=True,
            storage_policy=LabelStoragePolicy.model_validate({field: value}),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["max_windows", "max_ledger_rows"])
def test_original_label_policy_caps_still_apply(native, tmp_path, field):
    root, _ = native
    with pytest.raises(SnapshotError, match="window_limit|ledger_row_limit"):
        build_label_bundle(
            root / "private",
            tmp_path / "bundle",
            allow_evaluation_truth=True,
            label_policy=LabelPolicy.model_validate({field: 1}),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "field,value", [("batch_origins", True), ("batch_origins", 257), ("max_partition_bytes", 0)]
)
def test_partition_policy_is_strict_and_bounded(field, value):
    with pytest.raises(ValidationError):
        LabelPartitionPolicy.model_validate({field: value})


def test_private_opt_in_required_on_build_verify_and_iterator(prepared, tmp_path):
    target, _, source, _ = prepared
    with pytest.raises(SnapshotError, match="opt_in"):
        build_label_bundle(source, tmp_path / "out")
    with pytest.raises(SnapshotError, match="opt_in"):
        verify_label_bundle(target, source)
    with pytest.raises(SnapshotError, match="opt_in"):
        next(iter_verified_labels(target, source))
    with pytest.raises(SnapshotError, match="private_snapshot"):
        build_label_bundle(source.parent / "facts", tmp_path / "out", allow_evaluation_truth=True)


@pytest.mark.parametrize(
    "mutation",
    ["label", "missing_last", "extra", "duplicate", "range", "seal", "report", "version"],
)
def test_resealed_or_incomplete_bundle_fails_before_first_label(prepared, tmp_path, mutation):
    original, _, source, _ = prepared
    target = tmp_path / "changed"
    shutil.copytree(original, target)
    doc = read_json(target, bundle.MANIFEST)
    spec = doc["descriptor"]["partitions"][-1]
    if mutation == "label":
        path = target / spec["path"]
        table = pq.read_table(path)
        values = table.column("incident_stockout").to_pylist()
        values[0] = 1 if values[0] != 1 else 0
        table = table.set_column(
            table.schema.get_field_index("incident_stockout"),
            table.schema.field("incident_stockout"),
            pa.array(values, type=pa.int64()),
        )
        pq.write_table(table, path, compression="zstd", version="2.6")
    elif mutation == "missing_last":
        (target / spec["path"]).unlink()
    elif mutation == "extra":
        (target / "extra.parquet").write_bytes(b"extra")
    elif mutation == "duplicate":
        doc["descriptor"]["partitions"].append(dict(spec))
    elif mutation == "range":
        spec["first_as_of"] = doc["descriptor"]["partitions"][0]["first_as_of"]
    elif mutation == "seal":
        doc["descriptor"]["input_seal"]["windows_sha256"] = "0" * 64
    elif mutation == "report":
        doc["report"]["positive_labels"] += 1
    else:
        doc["descriptor"]["schema_version"] = "1.0.0"
    reseal(target, doc)
    with pytest.raises((SnapshotError, OSError)):
        next(iter_verified_labels(target, source, allow_evaluation_truth=True))


@pytest.mark.parametrize(
    "mutation",
    ["ledger_payload", "ledger_index", "snapshot_index", "window_payload", "window_index"],
)
def test_ephemeral_payload_and_index_checks(native, tmp_path, mutation):
    root, _ = native
    with LabelFacts(
        root / "private", DEFAULT_POLICY, allow_evaluation_truth=True, scratch=tmp_path
    ) as facts:
        window = next(facts.windows())
        key = window["product_id"], window["stock_location_id"]
        db = facts._db
        db.execute("PRAGMA query_only=OFF")
        if mutation == "ledger_payload":
            db.execute("UPDATE facts SET payload=? WHERE name='inventory_ledger'", (b"corrupt",))
        elif mutation == "ledger_index":
            db.execute("UPDATE facts SET stamp=? WHERE name='inventory_ledger'", ("invalid",))
        elif mutation == "snapshot_index":
            db.execute(
                "UPDATE facts SET grain=? WHERE name='inventory_daily_snapshots' AND rowid=(SELECT MIN(rowid) FROM facts WHERE name='inventory_daily_snapshots' AND product=? AND stock=?)",
                (b"forged", *key),
            )
        elif mutation == "window_payload":
            db.execute("UPDATE windows SET payload=?", (b"corrupt",))
        else:
            db.execute(
                "UPDATE windows SET origin=? WHERE rowid=(SELECT MIN(rowid) FROM windows)",
                ("invalid",),
            )
        with pytest.raises(SnapshotError, match="checksum|index"):
            if mutation.startswith("ledger"):
                facts.ledger(*key)
            elif mutation.startswith("snapshot"):
                preparation = LabelPreparation(facts, LabelPartitionPolicy())
                preparation.point(window, facts.ledger(*key))
            else:
                list(facts.windows())


@pytest.mark.parametrize("mutation", ["facts", "qualification"])
def test_changed_parent_during_stream_is_rejected_and_cleaned(
    native, tmp_path, monkeypatch, mutation
):
    root, _ = native
    if mutation == "facts":
        original = store.rows

        def changed(*args):
            for index, row in enumerate(original(*args)):
                yield (
                    {**row, "quantity_delta": row["quantity_delta"] + 1}
                    if args[1]["table"] == "inventory_ledger" and index == 0
                    else row
                )

        monkeypatch.setattr(store, "rows", changed)
    else:
        original = store.read_windows

        def changed(*args):
            rows = original(*args)
            rows[0]["reason"] = "forged"
            return rows

        monkeypatch.setattr(store, "read_windows", changed)
    with pytest.raises(SnapshotError, match="changed_during_read"):
        build_label_bundle(root / "private", tmp_path / "out", allow_evaluation_truth=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("limit", ["partition", "bundle"])
def test_output_limits_refuse_publication(native, tmp_path, monkeypatch, limit):
    root, _ = native
    if limit == "bundle":
        monkeypatch.setattr(bundle, "MAX_BUNDLE_BYTES", 1)
    with pytest.raises(SnapshotError, match="byte_limit"):
        build_label_bundle(
            root / "private",
            tmp_path / "out",
            allow_evaluation_truth=True,
            partition_policy=LabelPartitionPolicy(
                max_partition_bytes=1 if limit == "partition" else bundle.MAX_PARTITION_BYTES
            ),
        )
    assert list(tmp_path.iterdir()) == []


def test_interrupted_write_retry_and_immutable_policy_conflict(native, tmp_path, monkeypatch):
    root, _ = native
    source, target = root / "private", tmp_path / "out"
    original = bundle.write_private
    count = 0

    def interrupted(path, raw):
        nonlocal count
        count += 1
        original(path, raw)
        if count == 2:
            raise OSError("simulated interrupted output")

    monkeypatch.setattr(bundle, "write_private", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        build_label_bundle(source, target, allow_evaluation_truth=True)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(bundle, "write_private", original)
    doc, status = build_label_bundle(source, target, allow_evaluation_truth=True)
    assert status == "published"
    before = {p.name: p.read_bytes() for p in target.iterdir()}
    with pytest.raises(SnapshotError, match="immutable_output_conflict"):
        build_label_bundle(
            source,
            target,
            allow_evaluation_truth=True,
            partition_policy=LabelPartitionPolicy(batch_origins=1),
        )
    assert before == {p.name: p.read_bytes() for p in target.iterdir()}
    assert verify_label_bundle(target, source, allow_evaluation_truth=True) == doc


def test_changed_bundle_between_verification_and_iteration_is_refused(
    prepared, tmp_path, monkeypatch
):
    original, _, source, _ = prepared
    target = tmp_path / "changed"
    shutil.copytree(original, target)
    verify = bundle.verify_label_bundle

    def changed(*args, **kwargs):
        doc = verify(*args, **kwargs)
        (target / doc["descriptor"]["partitions"][0]["path"]).write_bytes(b"changed after verify")
        return doc

    monkeypatch.setattr(bundle, "verify_label_bundle", changed)
    with pytest.raises(SnapshotError, match="full_replay_mismatch"):
        next(iter_verified_labels(target, source, allow_evaluation_truth=True))
