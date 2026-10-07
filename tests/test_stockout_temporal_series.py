"""Actual partition parity, temporal purge, bounded join and protected development inputs."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import datetime, timedelta

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from test_stockout_features import native as native
from test_stockout_split import POLICY, datasets

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.stockout.split import SplitPolicy, build_split
from retailops_ai.stockout.upstream_dataset import build_comparison, build_upstream
from retailops_ai.stockout_history.bundle import build_history_bundle
from retailops_ai.stockout_label_partitions.bundle import build_label_bundle
from retailops_ai.stockout_temporal_series.bundle import (
    TemporalPartitionPolicy,
    TemporalPreparation,
    assemble_partitioned_development,
    build_temporal_bundle,
    verify_temporal_bundle,
)
from retailops_ai.stockout_temporal_series.store import TemporalStore
from retailops_ai.stockout_temporal_storage.schema import schemas
from retailops_ai.stockout_temporal_storage.store import (
    PartitionInputs,
    TemporalStoragePolicy,
    capture,
    physical_key,
)
from retailops_ai.stockout_upstream_series.bundle import build_upstream_bundle


@pytest.fixture(scope="module")
def bounded(native, tmp_path_factory):
    root, curated, features, labels = native
    directory = tmp_path_factory.mktemp("stockout-temporal")
    parent, upstream, label = directory / "features", directory / "upstream", directory / "labels"
    build_history_bundle(curated.directory, parent)
    build_upstream_bundle(curated.directory, parent, upstream)
    build_label_bundle(root / "fixture/private", label, allow_evaluation_truth=True)
    inputs = PartitionInputs(curated.directory, root / "fixture/private", parent, upstream, label)
    policy = SplitPolicy(
        start_at="2026-07-22T00:00:00Z",
        train_until="2026-07-25T00:00:00Z",
        tune_until="2026-07-27T00:00:00Z",
        calibration_until="2026-07-29T00:00:00Z",
        test_until="2026-08-01T00:00:00Z",
        evaluated_at="2026-08-01T00:00:00Z",
    )
    target = directory / "temporal"
    document, _ = build_temporal_bundle(
        inputs,
        target,
        split_policy=policy,
        allow_evaluation_truth=True,
        partition_policy=TemporalPartitionPolicy(batch_rows=7),
    )
    old_comparison = build_comparison(features, build_upstream(curated.directory, features))
    return inputs, target, document, policy, old_comparison, build_split(features, labels, policy)


def decoded(target, doc):
    result = dict(comparison=[], membership=[])
    for spec in doc["descriptor"]["partitions"]:
        for ref in spec["files"]:
            assert pq.read_schema(target / ref["path"]).equals(schemas()[ref["role"]])
            for row in pq.read_table(target / ref["path"]).to_pylist():
                result[ref["role"]].append(
                    {
                        k: v.isoformat().replace("+00:00", "Z") if isinstance(v, datetime) else v
                        for k, v in row.items()
                    }
                )
    return result


def test_actual_parquet_fields_reports_logical_hashes_and_reuse_equal_v1(bounded):
    inputs, target, doc, policy, comparison, split = bounded
    rows = decoded(target, doc)
    assert rows == dict(comparison=comparison["rows"], membership=split["membership"])
    assert doc["report"]["comparison"] == comparison["report"]
    assert doc["report"]["split"] == split["report"]
    assert doc["descriptor"]["rows_sha256"] == {r: json_sha256(v) for r, v in rows.items()}
    assert verify_temporal_bundle(target, inputs, allow_evaluation_truth=True) == doc
    assert build_temporal_bundle(
        inputs,
        target,
        split_policy=policy,
        allow_evaluation_truth=True,
        partition_policy=TemporalPartitionPolicy(batch_rows=7),
    ) == (doc, "reused")
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    assert target.stat().st_mode & 0o777 == 0o700
    assert all(s["keys"] <= 7 for s in doc["descriptor"]["partitions"])
    assert not doc["report"]["full_profile_ready"]
    assert doc["descriptor"]["schema_version"] == "2.1.0"
    assert (
        doc["descriptor"]["parents"]["upstream_bundle_id"]
        == json.loads((inputs.upstream / "manifest.json").read_text())["upstream_bundle_id"]
    )


@pytest.mark.parametrize("action", ["build", "verify", "assemble"])
def test_private_opt_in_is_required_before_loading_or_publishing(bounded, tmp_path, action):
    inputs, target, _, policy, _, _ = bounded
    with pytest.raises(SnapshotError, match="opt_in"):
        if action == "build":
            build_temporal_bundle(inputs, tmp_path / "output", split_policy=policy)
        elif action == "verify":
            verify_temporal_bundle(target, inputs)
        else:
            assemble_partitioned_development(target, inputs)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "field,value",
    [("max_rows", 1), ("max_payload_bytes", 1), ("max_db_bytes", 4096), ("max_batch_bytes", 1)],
)
def test_join_resource_limits_cleanup_without_output(bounded, tmp_path, field, value):
    inputs, _, _, policy, _, _ = bounded
    with pytest.raises(SnapshotError, match="resource"):
        build_temporal_bundle(
            inputs,
            tmp_path / "output",
            split_policy=policy,
            allow_evaluation_truth=True,
            storage_policy=TemporalStoragePolicy.model_validate({field: value}),
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "policy", [{"max_rows": 10001}, {"max_db_bytes": 134217729}, {"max_batch_bytes": 16777217}]
)
def test_caps_cannot_be_raised_silently(policy):
    with pytest.raises(ValidationError):
        TemporalStoragePolicy.model_validate(policy)
    with pytest.raises(ValidationError):
        TemporalPartitionPolicy(batch_rows=257)


@pytest.mark.parametrize("mutation", ["part", "missing", "extra", "report", "resealed_membership"])
def test_full_replay_rejects_tampering_before_development_data_is_returned(
    bounded, tmp_path, mutation
):
    inputs, target, doc, _, _, _ = bounded
    copy = tmp_path / "copy"
    shutil.copytree(target, copy)
    body = deepcopy(doc)
    ref = body["descriptor"]["partitions"][-1]["files"][-1]
    path = copy / ref["path"]
    if mutation == "part":
        path.write_bytes(b"changed")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        (copy / "extra").write_bytes(b"x")
    elif mutation == "report":
        body["report"]["split"]["eligible_by_role"]["train"] += 1
    else:
        table = pq.read_table(path)
        rows = table.to_pylist()
        rows[-1].update(role="train", eligible=True, reason=None)
        import pyarrow as pa

        pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path, compression="zstd")
        raw = path.read_bytes()
        ref.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        body["temporal_bundle_id"] = "temporal-partitions-sha256-" + json_sha256(body["descriptor"])
    (copy / "manifest.json").write_bytes(canonical_json(body) + b"\n")
    with pytest.raises((SnapshotError, OSError)):
        assemble_partitioned_development(copy, inputs, allow_evaluation_truth=True)


def test_valid_small_fixture_cannot_claim_training_has_both_classes(bounded):
    inputs, target, _, _, _, _ = bounded
    with pytest.raises(SnapshotError, match="both_development_classes"):
        assemble_partitioned_development(target, inputs, allow_evaluation_truth=True)


def test_actual_store_seals_queries_normalizes_clock_and_is_disposable(bounded, tmp_path):
    inputs, _, _, _, comparison, _ = bounded
    with TemporalStore(inputs, allow_evaluation_truth=True, scratch=tmp_path) as store:
        path = store.path
        assert {
            k: store.stats[k] for k in ("feature_replays", "upstream_replays", "label_replays")
        } == dict(feature_replays=1, upstream_replays=1, label_replays=1)
        keys = next(store.batches(7))
        assert (
            store.points("features", keys)[0]["product_id"] == comparison["rows"][0]["product_id"]
        )
        assert store.category("missing-product", "2026-07-22T00:00:00Z") == ("__unknown__", None)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        db = store.connection()
        db.execute("PRAGMA query_only=OFF")
        db.execute("DELETE FROM points WHERE kind='labels'")
        db.commit()
        with pytest.raises(SnapshotError, match="database_changed"):
            store.points("labels", keys)
    assert not path.parent.exists()
    with pytest.raises(SnapshotError, match="closed"):
        store.connection()
    with pytest.raises(SnapshotError, match="single_use"):
        store.__enter__()


def test_same_utc_instant_is_one_key_and_other_timezone_is_rejected():
    row = dict(product_id="p", stock_location_id="s", as_of="2026-01-01T00:00:00.000001Z")
    assert physical_key(row) == physical_key({**row, "as_of": "2026-01-01T00:00:00.000001+00:00"})
    with pytest.raises(ValueError, match="utc_timestamp_required"):
        physical_key({**row, "as_of": "2026-01-01T01:00:00+01:00"})


def test_parent_symlink_is_not_accepted_as_a_seal(tmp_path):
    (tmp_path / "target").write_bytes(b"x")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    with pytest.raises(SnapshotError, match="unsafe_parent"):
        capture(tmp_path)


class MemoryPoints:
    """Small decision fixture, without I/O; actual I/O is covered by the native bundle tests."""

    def __init__(self, features, labels):
        self.data = dict(features=features["points"], labels=labels["points"], upstream=[])
        self.policy = TemporalStoragePolicy()
        self.seal = {}
        self.parents = dict(
            features={**features, "feature_bundle_id": "features"},
            labels=dict(label_bundle_id="labels"),
            upstream=dict(
                upstream_bundle_id="upstream", report=dict(upstream_forecast_ready=False)
            ),
        )

    def batches(self, n):
        keys = sorted({physical_key(p) for rows in self.data.values() for p in rows})
        for start in range(0, len(keys), n):
            yield keys[start : start + n]

    def points(self, kind, keys):
        if kind == "upstream":
            from retailops_ai.stockout.upstream_contract import UpstreamPoint

            return [
                UpstreamPoint(
                    product_id=p[0],
                    stock_location_id=p[1],
                    as_of=p[2],
                    forecast_origin=p[2],
                    training_cutoff=p[2],
                    selection_cutoff=p[2],
                    source_available_at=None,
                    upstream_model_version="baseline-sha256-" + "0" * 64,
                    status="insufficient_data",
                    reason="no_routing",
                    forecast_units_7d=None,
                    series=(),
                ).model_dump(mode="json")
                for p in keys
                if any(physical_key(f) == p for f in self.data["features"])
            ]
        return [p for p in self.data[kind] if physical_key(p) in keys]

    def check_database(self):
        pass

    def check_parents(self):
        pass


def decision_points(days):
    features, labels = datasets(days)
    shift = timedelta(hours=23, minutes=59, seconds=59)
    for feature in features["points"]:
        feature["as_of"] = (
            (datetime.fromisoformat(feature["as_of"]) + shift).isoformat().replace("+00:00", "Z")
        )
    for label in labels["points"]:
        for name in ("as_of", "window_end_at", "label_available_at"):
            label[name] = (
                (datetime.fromisoformat(label[name]) + shift).isoformat().replace("+00:00", "Z")
            )
    return features, labels


def test_purge_delayed_availability_and_class_counts_across_batches():
    features, labels = decision_points([1, 2, 22, 23, 31, 32, 52, 53, 61, 62, 82, 83, 91, 92])
    features["descriptor"]["curated_dataset_id"] = "curated"
    for index, label in enumerate(labels["points"]):
        if index % 2:
            label.update(
                incident_stockout=1,
                first_incident_at=(
                    datetime.fromisoformat(label["as_of"]) + timedelta(days=1)
                ).isoformat(),
                first_incident_event_id="incident",
            )
    labels["points"][2]["label_available_at"] = POLICY.train_until.isoformat()
    preparation = TemporalPreparation(
        MemoryPoints(features, labels), POLICY, TemporalPartitionPolicy(batch_rows=2)
    )
    membership = [m for rows, _, _ in preparation.partitions() for m in rows["membership"]]
    expected = build_split(features, labels, POLICY)
    assert membership == expected["membership"]
    assert preparation.manifest()["report"]["split"] == expected["report"]
    assert expected["report"]["temporal_membership_ready"]
    assert expected["report"]["reasons"]["purged_boundary_or_delayed_label"] == 4
    changed = deepcopy(labels)
    changed["points"][-1].update(
        incident_stockout=0, first_incident_at=None, first_incident_event_id=None
    )
    replay = TemporalPreparation(
        MemoryPoints(features, changed), POLICY, TemporalPartitionPolicy(batch_rows=2)
    )
    assert [rows for rows, _, _ in replay.partitions()] == [
        rows
        for rows, _, _ in TemporalPreparation(
            MemoryPoints(features, labels), POLICY, TemporalPartitionPolicy(batch_rows=2)
        ).partitions()
    ]
    assert "test" not in preparation.manifest()["report"]["split"]["development_classes"]


def test_missing_feature_stays_in_membership_with_explicit_reason():
    features, labels = decision_points([1, 2])
    features["points"].pop(0)
    features["descriptor"]["curated_dataset_id"] = "curated"
    preparation = TemporalPreparation(
        MemoryPoints(features, labels), POLICY, TemporalPartitionPolicy(batch_rows=1)
    )
    rows = [m for batch, _, _ in preparation.partitions() for m in batch["membership"]]
    assert rows[0]["reason"] == "feature_missing"
    assert preparation.counts == dict(comparison=1, membership=2)


def test_cli_rejects_missing_opt_in_without_printing_paths(monkeypatch, capsys, bounded):
    from retailops_ai.stockout_temporal_storage.cli import main

    inputs, target, _, _, _, _ = bounded
    argv = ["temporal", "verify", "--output", str(target)]
    for name, path in inputs.roots().items():
        argv.extend(["--" + name, str(path)])
    monkeypatch.setattr("sys.argv", argv)
    assert main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output == dict(status="failed", error="stockout_temporal_build_or_verification_failed")


def test_partition_byte_cap_cleans_staging(bounded, tmp_path):
    inputs, _, _, policy, _, _ = bounded
    with pytest.raises(SnapshotError, match="partition_resource"):
        build_temporal_bundle(
            inputs,
            tmp_path / "output",
            split_policy=policy,
            allow_evaluation_truth=True,
            partition_policy=TemporalPartitionPolicy(max_partition_bytes=1),
        )
    assert not list(tmp_path.iterdir())


def test_changed_campaign_cannot_overwrite_an_existing_bundle(bounded, tmp_path):
    inputs, target, _, policy, _, _ = bounded
    copy = tmp_path / "copy"
    shutil.copytree(target, copy)
    before = capture(copy)
    changed = SplitPolicy.model_validate(
        {**policy.model_dump(), "evaluated_at": policy.evaluated_at + timedelta(days=1)}
    )
    with pytest.raises(SnapshotError, match="immutable_output_conflict"):
        build_temporal_bundle(inputs, copy, split_policy=changed, allow_evaluation_truth=True)
    assert capture(copy) == before
    assert list(tmp_path.iterdir()) == [copy]


def test_parent_replacement_after_open_is_rejected(bounded, tmp_path):
    inputs, _, _, _, _, _ = bounded
    copy = tmp_path / "features"
    shutil.copytree(inputs.features, copy)
    replaced = PartitionInputs(inputs.curated, inputs.private, copy, inputs.upstream, inputs.labels)
    with TemporalStore(replaced, allow_evaluation_truth=True, scratch=tmp_path) as store:
        part = next(copy.rglob("*.parquet"))
        part.write_bytes(b"changed")
        with pytest.raises(SnapshotError, match="parent_changed"):
            store.check_parents()


def test_context_replays_each_real_parent_once(bounded, monkeypatch):
    from retailops_ai.stockout_history.bundle import HistoryPreparation
    from retailops_ai.stockout_label_partitions.bundle import LabelPreparation
    from retailops_ai.stockout_upstream_series.bundle import UpstreamPreparation

    calls = {}
    for kind, cls in (
        ("features", HistoryPreparation),
        ("upstream", UpstreamPreparation),
        ("labels", LabelPreparation),
    ):
        original = cls.partitions

        def counted(self, original=original, kind=kind):
            calls[kind] = calls.get(kind, 0) + 1
            yield from original(self)

        monkeypatch.setattr(cls, "partitions", counted)
    inputs, _, _, _, _, _ = bounded
    with TemporalStore(inputs, allow_evaluation_truth=True) as store:
        assert next(store.batches(1))
    assert calls == dict(features=1, upstream=1, labels=1)


@pytest.mark.parametrize("kind", ["features", "upstream", "labels"])
@pytest.mark.parametrize("mutation", ["last_part", "extra", "manifest", "resealed_last_part"])
def test_all_parent_parts_are_replayed_before_join_becomes_available(
    bounded, tmp_path, kind, mutation
):
    from dataclasses import replace

    inputs, _, _, _, _, _ = bounded
    copy = tmp_path / kind
    shutil.copytree(getattr(inputs, kind), copy)
    document = json.loads((copy / "manifest.json").read_text())
    spec = document["descriptor"]["partitions"][-1]
    ref = spec["files"][0] if kind == "features" else spec
    path = copy / ref["path"]
    if mutation == "extra":
        (copy / "unlisted").write_bytes(b"x")
    elif mutation == "manifest":
        document["report"]["invented"] = True
    else:
        path.write_bytes(b"changed")
        if mutation == "resealed_last_part":
            ref.update(bytes=7, sha256=hashlib.sha256(b"changed").hexdigest())
            id_field = {
                "features": "feature_bundle_id",
                "upstream": "upstream_bundle_id",
                "labels": "label_bundle_id",
            }[kind]
            prefix = document[id_field].split("sha256-")[0] + "sha256-"
            document[id_field] = prefix + json_sha256(document["descriptor"])
    (copy / "manifest.json").write_bytes(canonical_json(document) + b"\n")
    with pytest.raises((SnapshotError, OSError)):
        with TemporalStore(replace(inputs, **{kind: copy}), allow_evaluation_truth=True):
            pytest.fail("partial or resealed parents must not expose a join")


@pytest.mark.parametrize(
    "mutation", ["query_only", "bytes_restore_mtime", "replacement", "symlink"]
)
def test_private_join_change_is_rejected_on_next_read(bounded, tmp_path, mutation):
    import os

    inputs, _, _, _, _, _ = bounded
    with TemporalStore(inputs, allow_evaluation_truth=True, scratch=tmp_path) as store:
        keys = next(store.batches(1))
        path = store.path
        before = path.stat()
        if mutation == "query_only":
            store.connection().execute("PRAGMA query_only=OFF")
        elif mutation == "bytes_restore_mtime":
            with path.open("r+b") as stream:
                stream.seek(-1, 2)
                stream.write(b"x")
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        else:
            replacement = path.parent / "replacement.sqlite"
            replacement.write_bytes(path.read_bytes())
            if mutation == "replacement":
                replacement.replace(path)
            else:
                path.unlink()
                path.symlink_to(replacement)
        with pytest.raises(SnapshotError, match="database_changed"):
            store.points("features", keys)
    assert not list(tmp_path.iterdir())


def test_legacy_upstream_is_rejected_without_losing_legacy_reader(bounded, tmp_path):
    from dataclasses import replace

    from retailops_ai.stockout_upstream_storage.bundle import build_upstream_bundle as legacy_build
    from retailops_ai.stockout_upstream_storage.bundle import (
        verify_upstream_bundle as legacy_verify,
    )

    inputs, _, _, _, _, _ = bounded
    old = tmp_path / "old"
    document, _ = legacy_build(inputs.curated, inputs.features, old)
    with pytest.raises(SnapshotError, match="series_bundle_version_required"):
        with TemporalStore(replace(inputs, upstream=old), allow_evaluation_truth=True):
            pytest.fail("new context requires upstream 2.1")
    assert legacy_verify(old, inputs.curated, inputs.features) == document


@pytest.mark.parametrize("action", ["verify", "assemble"])
def test_earlier_temporal_part_changed_during_replay_is_rejected(
    bounded, tmp_path, monkeypatch, action
):
    inputs, target, _, _, _, _ = bounded
    copy = tmp_path / "temporal"
    shutil.copytree(target, copy)
    original = TemporalPreparation.partitions
    changed = False

    def changing(self):
        nonlocal changed
        for batch in original(self):
            yield batch
            if not changed:
                # The consumer has already checked this part; preserve the manifest.
                (copy / batch[1]["files"][0]["path"]).write_bytes(b"changed after check")
                changed = True

    monkeypatch.setattr(TemporalPreparation, "partitions", changing)
    reader = verify_temporal_bundle if action == "verify" else assemble_partitioned_development
    with pytest.raises(SnapshotError, match="changed_during_development"):
        reader(copy, inputs, allow_evaluation_truth=True)
    assert changed


def test_caller_cannot_supply_a_trusted_parent_cache(bounded):
    inputs, _, _, _, _, _ = bounded
    with pytest.raises(TypeError):
        TemporalStore(inputs, allow_evaluation_truth=True, verified_parents={})


def test_output_file_limit_blocks_publication_and_removes_scratch(bounded, tmp_path, monkeypatch):
    # Reduce the output-only cap to exercise exhaustion with actual sealed parents.
    # Parent verification uses its unchanged independent cap.
    monkeypatch.setattr("retailops_ai.stockout_temporal_series.bundle.MAX_PARENT_FILES", 2)
    inputs, _, _, policy, _, _ = bounded
    with pytest.raises(SnapshotError, match="output_file_resource_limit"):
        build_temporal_bundle(
            inputs,
            tmp_path / "output",
            split_policy=policy,
            allow_evaluation_truth=True,
            partition_policy=TemporalPartitionPolicy(batch_rows=1),
        )
    assert not list(tmp_path.iterdir())
