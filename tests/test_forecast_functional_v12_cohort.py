"""Cohort orchestration preserves role boundaries, exact receipts and no-fit replay."""

import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_forecast_features import tables as tables
from test_forecast_functional_v12_recipe import fixture
from test_forecast_manifests import DAY
from test_forecast_manifests import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting import functional_v12_cohort as cohort
from retailops_ai.forecasting import functional_v12_inputs as compact
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.features_contract import TABLES
from retailops_ai.forecasting.functional_v12_recipe import FunctionalV12Policy
from retailops_ai.forecasting.manifest_contract import FeaturePolicy, SplitPolicy
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture
def prepared_fixture(timeline, monkeypatch, tmp_path):
    """Use real compact/fit/replay; source verification/mapping have separate parity tests."""
    fold, _ = fixture()
    split = SplitPolicy(
        folds=tuple(fold.model_copy(update={"name": f"fold-{i}"}) for i in range(3))
    )
    window = OriginWindow(start=DAY, end=fold.development_holdout.end)
    changed = deepcopy(timeline)
    changed["daily_demand_versions"] = [
        r for r in changed["daily_demand_versions"] if r["business_date"] != DAY + timedelta(days=2)
    ]
    seal_descriptor = {
        "source_schema_version": "1.0.0",
        "source_dataset_id": "fixture-source",
        "snapshot_id": "fixture-snapshot",
        "files": {},
        "full_source_verification": "passed",
    }
    seal = {
        "seal_id": "forecast-source-seal-sha256-" + canonical_sha256(seal_descriptor),
        "descriptor": seal_descriptor,
    }
    checks = []

    def verify(snapshot, receipt):
        checks.append("full-source-verify")
        receipt.write_bytes(canonical_bytes(seal))
        return SimpleNamespace(seal=seal, verify_bytes=lambda: checks.append("bytes"))

    def build(verified, output, resolved, *, origin_window, feature_policy, progress):
        root = output / "fixture-compact"
        root.mkdir(parents=True)
        projection_descriptor = {
            "source_seal_id": seal["seal_id"],
            "code": compact.compact_code("1.0.0"),
        }
        projection = {
            "projection_id": "forecast-projection-sha256-"
            + canonical_sha256(projection_descriptor),
            "descriptor": projection_descriptor,
        }
        report = compact._write_projection_inputs(
            compact.ForecastProjection({name: changed[name] for name in TABLES}, projection),
            root,
            resolved,
            origin_window,
            feature_policy or FeaturePolicy(),
        )
        descriptor = {
            **report,
            "source_seal": seal,
            "projection": projection,
            "split_policy": resolved.model_dump(mode="json"),
            "origin_window": origin_window.model_dump(mode="json"),
            "roles": list(compact.ROLES),
            "daily_horizons": list(range(1, 15)),
            "forecast_model_status": "not_ready",
            "holdout_metrics_evaluated": False,
            "code": compact.compact_code("1.0.0"),
        }
        manifest = {
            "inputs_id": "forecast-compact-inputs-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
        }
        (root / "compact_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
        return root

    monkeypatch.setattr(cohort, "seal_snapshot", verify)
    monkeypatch.setattr(cohort, "build_compact_inputs", build)
    opened = []
    original_iterator = cohort.iter_compact_rows

    def spy(root, manifest, fold_name, role):
        assert role in {"train", "validation"}, "fitter opened holdout/purged role"
        opened.append((fold_name, role))
        yield from original_iterator(root, manifest, fold_name, role)

    monkeypatch.setattr(cohort, "iter_compact_rows", spy)
    root = cohort.prepare_cohort(
        tmp_path / "unused-fixture-source",
        "cohort-001",
        split,
        window,
        FunctionalV12Policy(mean_variant="zero_only", zero_estimation="train_validation_pooled"),
        tmp_path / "prepared",
    )
    return root, opened, checks


def test_prepare_three_folds_reads_each_development_role_once_and_replay_never_fits(
    prepared_fixture,
    monkeypatch,
):
    root, opened, checks = prepared_fixture
    assert checks.count("full-source-verify") == 1
    assert opened == [(f"fold-{i}", role) for i in range(3) for role in ("train", "validation")]
    manifest = cohort.load_cohort(root)
    descriptor = manifest["descriptor"]
    assert descriptor["forecast_model_status"] == "not_ready"
    assert descriptor["holdout_iterator_opened_during_fit"] is False
    assert descriptor["compact_parent"]["semantic_replay"] == "required_separate_campaign_receipt"
    for fold in descriptor["recipes"].values():
        assert fold["roles"]["train"]["total"] == 14
        assert fold["roles"]["train"]["excluded"] >= 1
        assert fold["roles"]["train"]["rows"] == 14
        assert fold["roles"]["validation"]["total"] == 28

    def forbidden(*args, **kwargs):
        raise AssertionError("replay refitted the recipe")

    monkeypatch.setattr(cohort, "fit_recipe_v12", forbidden)
    replay = cohort.replay_cohort(root)
    assert replay["status"] == "passed"
    assert replay["fit_calls"] == 0
    assert replay["roles_opened"] == ["train", "validation"]
    assert len(opened) == 12
    assert set(role for _, role in opened) == {"train", "validation"}


def test_code_drift_and_changed_recipe_bytes_block_loading(prepared_fixture, monkeypatch):
    root, _, _ = prepared_fixture
    real_code = cohort.cohort_code()
    with monkeypatch.context() as changed:
        changed.setattr(cohort, "cohort_code", lambda: real_code | {"code_sha256": "0" * 64})
        with pytest.raises(SnapshotError, match="identity_code_or_scope"):
            cohort.load_cohort(root)
    recipe_path = root / "recipes/fold-0.json"
    recipe = json.loads(recipe_path.read_bytes())
    recipe["offsets"]["global"]["offset"] += 100
    recipe_path.write_bytes(canonical_bytes(recipe) + b"\n")
    with pytest.raises(SnapshotError, match="file_checksum"):
        cohort.load_cohort(root)


def test_resource_budget_precedes_iteration_and_never_relaxes_quality(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("over-budget role was opened")

    monkeypatch.setattr(cohort, "iter_compact_rows", forbidden)
    manifest = {"descriptor": {"counts": {"fold:train:total": 100000}}}
    with pytest.raises(SnapshotError, match="role_resource_budget"):
        cohort._read_role(tmp_path, manifest, "cohort", "fold", "train")
    with pytest.raises(SnapshotError, match="fitting_role_forbidden"):
        cohort._read_role(tmp_path, manifest, "cohort", "fold", "development_holdout")


def test_hgb_requires_explicit_pinned_provider_before_any_source_read(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsupported HGB policy accessed the source")

    monkeypatch.setattr(cohort, "seal_snapshot", forbidden)
    fold, _ = fixture()
    with pytest.raises(SnapshotError, match="hgb_requires_explicit_pinned_model_provider"):
        cohort.prepare_cohort(
            tmp_path / "source",
            "cohort",
            SplitPolicy(folds=(fold,)),
            OriginWindow(start=fold.train.start, end=fold.development_holdout.end),
            FunctionalV12Policy(mean_variant="hgb_blend"),
            tmp_path / "out",
        )


def test_excluded_label_cannot_be_reintroduced_by_compact_adapter():
    with pytest.raises(SnapshotError, match="eligibility_binding"):
        cohort.observation_from_compact(
            {
                "eligible": False,
                "reasons": ["missing_label"],
                "actual": 0,
                "label_available_at": None,
            },
            "cohort",
        )
