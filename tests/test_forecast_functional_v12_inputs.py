"""Compact projection retains the original source mapping, full panel and temporal contracts."""

import gzip
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from test_curated import SOURCE
from test_curated import curated as curated
from test_curated import imported as imported
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import encoded
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting import functional_v12_inputs as compact
from retailops_ai.forecasting.features_contract import TABLES
from retailops_ai.forecasting.manifest_io import iter_table, key
from retailops_ai.forecasting.manifests import feature_key, input_models
from retailops_ai.forecasting.splits import load_split
from retailops_ai.source_snapshot.files import SnapshotError


def test_projection_exactly_matches_eight_tables_from_full_curated_and_requires_factory(
    curated, tmp_path
):
    verified = compact.seal_snapshot(SOURCE, tmp_path / "seal.json")
    with pytest.raises(SnapshotError, match="not_verified_or_changed"):
        compact.project_forecast_tables(replace(verified, capability=object()))
    projection = compact.project_forecast_tables(verified)
    assert set(projection.tables) == set(TABLES)
    assert len(compact.DEPENDENCIES) == 15
    assert projection.manifest["descriptor"]["rejected_rows"] == 0
    for table in curated.manifest["tables"]:
        name = table["table"]
        if name in TABLES:
            assert sorted(encoded(r) for r in projection.tables[name]) == sorted(
                encoded(r) for r in iter_rows(curated.directory, table["files"], 8192)
            )
            for field in ("row_count", "content_sha256", "field_ranges"):
                assert projection.manifest["descriptor"]["tables"][name][field] == table[field]
    altered = deepcopy(verified.seal)
    altered["descriptor"]["files"]["snapshot_manifest.json"]["sha256"] = "0" * 64
    with pytest.raises(SnapshotError, match="not_verified_or_changed"):
        replace(verified, seal=altered).verify_bytes()
    copied = tmp_path / "copied-source"
    shutil.copytree(SOURCE, copied)
    duplicate = replace(verified, root=copied)
    duplicate.verify_bytes()
    target = next(copied.rglob("*.parquet"))
    target.write_bytes(target.read_bytes() + b"changed-after-verification")
    with pytest.raises(SnapshotError, match="bytes_changed_after_verification"):
        duplicate.verify_bytes()


def test_compact_all_features_histories_labels_and_memberships_match_original_pipeline(
    artifacts, timeline, tmp_path
):
    features, split_dir, _, calendar = artifacts
    split = load_split(split_dir)
    root = tmp_path / "compact"
    root.mkdir()
    report = compact._write_projection_inputs(
        compact.ForecastProjection({name: timeline[name] for name in TABLES}, {}),
        root,
        split.descriptor.resolved_policy,
        calendar.descriptor.origin_window,
        split.descriptor.feature_policy,
    )
    manifest = {
        "descriptor": {
            **report,
            "split_policy": split.descriptor.resolved_policy.model_dump(mode="json"),
        }
    }
    expected_features = {feature_key(r).decode(): r for r in input_models(features, "features")}
    expected_histories = {r.content_sha256(): r for r in input_models(features, "history")}
    from collections import Counter

    labels = {
        key(r).decode(): r
        for r in iter_table(split_dir, "labels", split.tables["labels"], Counter())
    }
    memberships = {
        key(r).decode(): r
        for r in iter_table(split_dir, "memberships", split.tables["memberships"], Counter())
    }
    found = set()
    for role in compact.ROLES:
        for row in compact.iter_compact_rows(root, manifest, "fold-a", role):
            found.add(row["key"])
            original = expected_features[row["feature_key"]]
            assert row["input_row_sha256"] == canonical_sha256(original.model_dump(mode="json"))
            assert row["membership"] == memberships[row["key"]].model_dump(mode="json")
            assert row["label"] == (
                labels[row["key"]].model_dump(mode="json") if row["key"] in labels else None
            )
            assert row["feature_values"] == {v.name: v.value for v in original.values}
            assert (
                row["baseline_points"]
                == compact.compact_feature(
                    original, expected_histories[row["history_context_sha256"]]
                )["baseline_points"]
            )
    assert found == set(memberships)
    histories = {}
    for parts in report["origins"].values():
        for body in compact._read_records(
            root, parts["histories"], report["files"][parts["histories"]]
        ):
            history = compact.HistoryContext.model_validate_json(json.dumps(body))
            histories[history.content_sha256()] = history
    assert histories == expected_histories
    assert report["counts"]["features"] == len(expected_features)
    assert report["counts"]["memberships"] == len(memberships)
    assert report["counts"]["labels"] == len(labels)
    assert report["split_qualification_status"] == split.descriptor.qualification_status
    part = root / "memberships/fold-a/train.jsonl.gz"
    part.write_bytes(part.read_bytes() + b"tampered")
    with pytest.raises(SnapshotError, match="file_checksum"):
        list(compact.iter_compact_rows(root, manifest, "fold-a", "train"))


def test_missing_targets_and_future_source_versions_are_excluded_without_zero_imputation(
    timeline, tmp_path
):
    from test_forecast_manifests import DAY, plan

    from retailops_ai.forecasting.contract import OriginWindow
    from retailops_ai.forecasting.manifest_contract import FeaturePolicy, SplitPolicy

    changed = deepcopy(timeline)
    changed["daily_demand_versions"] = [
        r for r in changed["daily_demand_versions"] if r["business_date"] != DAY + timedelta(days=1)
    ]
    late = next(r for r in changed["daily_demand_versions"] if r["business_date"] == DAY)
    late["curated_available_at"] = plan().selection_cutoff + timedelta(days=1)
    late["observed_units"] = 999999
    root = tmp_path / "compact-missing"
    root.mkdir()
    split = SplitPolicy(folds=(plan(),))
    report = compact._write_projection_inputs(
        compact.ForecastProjection({name: changed[name] for name in TABLES}, {}),
        root,
        split,
        OriginWindow(start=DAY, end=plan().development_holdout.end),
        FeaturePolicy(),
    )
    manifest = {"descriptor": {**report, "split_policy": split.model_dump(mode="json")}}
    train = list(compact.iter_compact_rows(root, manifest, "fold-a", "train"))
    missing = next(row for row in train if row["horizon"] == 1)
    assert missing["actual"] is None and "censored_label" in missing["reasons"]
    assert missing["label"]["observed_sales_units"] is None
    assert all(row["feature_values"]["origin_lag_1_units"] is None for row in train)
    assert all(row["feature_values"]["rolling_mean_28"] < 999999 for row in train)
    assert all(row["pit"]["max_available_at"] <= row["origin"] for row in train)


def test_deterministic_gzip_preserves_logical_rows_and_bounds_record_reads(tmp_path):
    from collections import Counter

    row = {"observed": 0, "nullable": None, "negative_is_not_an_absent_value": False}
    reports = []
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        writer = compact._Writer(root, "part.jsonl.gz", Counter())
        writer.add(row)
        ref = writer.summary()
        reports.append(ref)
        assert list(compact._read_records(root, "part.jsonl.gz", ref)) == [row]
        assert json.loads(gzip.decompress((root / "part.jsonl.gz").read_bytes())) == row
    assert reports[0] == reports[1]
