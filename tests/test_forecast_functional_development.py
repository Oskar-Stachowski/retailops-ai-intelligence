"""Development cache reuses verified parents without allowing test labels or qualification."""

import json
import shutil
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline

from retailops_ai.forecasting import functional_development as development
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.splits import load_split
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture
def cache(artifacts, tmp_path, monkeypatch):
    features, split, _, _ = artifacts
    monkeypatch.setattr(
        development.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3)
    )
    root = development.build_development_cache(features, split, tmp_path / "cache")
    return root, features, split


def test_cache_contains_only_train_validation_and_reuses_verified_bytes(cache, monkeypatch):
    root, features, split = cache

    def forbidden(*args, **kwargs):
        pytest.fail("an experiment must not repeat semantic split verification")

    monkeypatch.setattr(development, "verify_split", forbidden)
    manifest = development.load_development_cache(root, features, split)
    desc = manifest["descriptor"]
    assert desc["model_status"] == "not_ready"
    assert desc["purpose"] == "development_only_not_independent_qualification"
    assert desc["holdout_rows"] == desc["model_fits"] == 0
    assert set(desc["roles"]) == {"train", "validation"}
    rows = []
    for fold in desc["folds"]:
        for role in development.ROLES:
            rows.extend(development.iter_development_rows(root, manifest, fold, role))
    assert len(rows) == desc["counts"]["rows"] == 28
    assert {r["role"] for r in rows} == {"train", "validation"}
    assert all("rolling_mean_28" in r["feature_values"] for r in rows)
    assert all(set(r["baseline_bands"]) == {"history7", "history28", "weekday28"} for r in rows)
    assert all(len(r["vector"]) == len(desc["folds"][r["fold"]]["output_columns"]) for r in rows)
    with pytest.raises(SnapshotError, match="holdout_role_forbidden"):
        list(development.iter_development_rows(root, manifest, "fold-a", "development_holdout"))
    next(features.rglob("*.parquet")).write_bytes(b"changed")
    with pytest.raises(SnapshotError, match="identity_code_or_parent"):
        development.load_development_cache(root, features, split)


def test_cache_consumption_rejects_modified_bytes_and_temporal_leaks(cache):
    root, features, split = cache
    manifest = development.load_development_cache(root, features, split)
    row = next(
        development.iter_development_rows(root, manifest, "fold-a", "train", eligible_only=True)
    )
    meta = manifest["descriptor"]["folds"]["fold-a"]
    fold = FoldPlan.model_validate_json(json.dumps(meta["plan"]))
    columns = len(meta["output_columns"])
    for mutation, reason in (
        ({"role": "development_holdout"}, "only_train_validation_origin"),
        (
            {"origin": fold.development_holdout.start.isoformat() + "T23:59:59+00:00"},
            "only_train_validation_origin",
        ),
        (
            {"label_available_at": (fold.training_cutoff + timedelta(days=1)).isoformat()},
            "label_not_available",
        ),
        (
            {"feature_available_at": (fold.training_cutoff + timedelta(days=1)).isoformat()},
            "future_feature",
        ),
        ({"eligible": False, "reasons": ["closed_target"]}, "excluded_label_must_be_absent"),
    ):
        with pytest.raises(SnapshotError, match=reason):
            development.validate_row(row | mutation, fold, columns)
    path = root / "rows/fold-a/train.jsonl"
    path.write_bytes(path.read_bytes() + b"{}\n")
    with pytest.raises(SnapshotError, match="cache_file_checksum"):
        list(development.iter_development_rows(root, manifest, "fold-a", "train"))


def test_replay_shortcut_requires_exact_parent_bytes_and_completed_receipt(
    artifacts, tmp_path, monkeypatch
):
    features, split, _, _ = artifacts
    expected = development._parents(features, split)
    run = tmp_path / "replayed-run"
    shutil.copytree(features, run / "features")
    shutil.copytree(split, run / "split")
    (run / "run_manifest.json").write_text("{}")
    report = tmp_path / "replay.json"
    body = {
        "status": "completed",
        "step": "replayed_and_exported",
        "independent_replay": "passed",
        "run_id": "run-1",
        "campaign_id": "campaign-1",
    }
    report.write_text(json.dumps(body))
    monkeypatch.setattr(
        development,
        "load_run",
        lambda _: {"run_id": "run-1", "descriptor": {"replay_status": "passed"}},
    )
    manifest = load_split(split)
    campaign = {
        "campaign_id": "campaign-1",
        "descriptor": {
            "split_id": manifest.split_id,
            "freeze": {},
            "policy": development.FunctionalPolicy().model_dump(mode="json"),
        },
    }
    monkeypatch.setattr(development, "load_campaign", lambda _: deepcopy(campaign))
    monkeypatch.setattr(development, "validate_freeze", lambda *args: None)

    def forbidden(*args, **kwargs):
        pytest.fail("exact independently replayed parents must not replay again")

    monkeypatch.setattr(development, "verify_split", forbidden)
    _, receipt = development._verify_parents(features, split, expected, run, report)
    assert receipt["method"] == "exact_parent_bytes_from_trusted_local_independent_replay_receipt"
    report.write_text(json.dumps(body | {"independent_replay": "not_ready"}))
    with pytest.raises(SnapshotError, match="replayed_parent_receipt_mismatch"):
        development._verify_parents(features, split, expected, run, report)
    report.write_text(json.dumps(body))
    next((run / "features").rglob("*.parquet")).write_bytes(b"different source bytes")
    with pytest.raises(SnapshotError, match="replayed_parent_receipt_mismatch"):
        development._verify_parents(features, split, expected, run, report)
