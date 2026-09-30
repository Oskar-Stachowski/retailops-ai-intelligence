"""Disjoint validation fitting, late-label rejection, frozen gates and report replay."""

import json
import shutil
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_forecast_models import small_policy

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.backtest import build_backtest
from retailops_ai.forecasting.backtest_contract import BacktestPolicy
from retailops_ai.forecasting.remediation import (
    METHODS,
    build_remediation,
    calibrate,
    fit_recipe,
    interval,
    load_remediation,
    observations,
    predict,
    residual_quantiles,
    verify_remediation,
)
from retailops_ai.forecasting.remediation_contract import RemediationPolicy
from retailops_ai.source_snapshot.files import SnapshotError


def validation_db():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE features(key BLOB,category TEXT,volume TEXT);
        CREATE TABLE members(key BLOB,feature_key BLOB,fold TEXT,role TEXT,eligible INTEGER,body BLOB);
        CREATE TABLE labels(key BLOB,actual INTEGER,available TEXT);
        CREATE TABLE predictions(key BLOB,method TEXT,units REAL);
    """)
    # Two separate validation origin blocks and an unrelated later holdout.
    for origin, role in (
        ("2026-06-01", "validation"),
        ("2026-06-02", "validation"),
        ("2026-07-01", "development_holdout"),
    ):
        for index in range(60):
            key = canonical_bytes([origin, index])
            member = {
                "forecast_origin": origin + "T23:59:59+00:00",
                "horizon_days": 1,
                "channel": "store",
                "eligible": True,
                "reasons": [],
            }
            db.execute("INSERT INTO features VALUES (?,'cat','low')", (key,))
            db.execute(
                "INSERT INTO members VALUES (?,?,'fold',?,1,?)",
                (key, key, role, canonical_bytes(member)),
            )
            db.execute("INSERT INTO labels VALUES (?,10,'2026-06-03T00:00:00+00:00')", (key,))
            for method in (*METHODS, "validation_baseline", "validation_selected"):
                units = 5.0 if method == "random_forest" else 12.0
                db.execute("INSERT INTO predictions VALUES (?,?,?)", (key, method, units))
    return db


def parent():
    return SimpleNamespace(
        descriptor=SimpleNamespace(
            folds=(
                SimpleNamespace(
                    plan=SimpleNamespace(
                        name="fold", selection_cutoff=end_of_day(date(2026, 6, 30))
                    )
                ),
            )
        )
    )


def test_fit_and_calibration_ignore_holdout_and_use_distinct_validation_origins():
    policy = RemediationPolicy()
    choices = {"fold": SimpleNamespace(baseline="seasonal_naive7")}
    with validation_db() as db:
        recipe, sample = fit_recipe(db, parent(), choices, policy)
        cell = recipe["fold", "low", "cat"]
        assert cell["fit_rows"] == cell["selection_rows"] == 60
        assert cell["fit_last_origin"] < cell["selection_first_origin"]
        assert cell["corrected"] and cell["correction"]["ratio"] > 0
        calibration = calibrate(sample, recipe, choices, policy)
        db.execute(
            "UPDATE labels SET actual=999999 WHERE key IN (SELECT key FROM members WHERE role='development_holdout')"
        )
        second, sample2 = fit_recipe(db, parent(), choices, policy)
        assert second == recipe and calibrate(sample2, second, choices, policy) == calibration
        db.execute(
            "UPDATE labels SET available='2026-07-01T00:00:00+00:00' WHERE key IN (SELECT key FROM members WHERE role='validation')"
        )
        with pytest.raises(SnapshotError, match="unavailable_at_selection"):
            fit_recipe(db, parent(), choices, policy)


def test_signed_quantiles_clip_zero_and_audited_sparse_group_fallback():
    policy = RemediationPolicy()
    assert residual_quantiles([0.0] * 49, policy) is None
    assert residual_quantiles([-0.2] * 95 + [0.8] * 5, policy) == (-0.2, 0.8)
    choices = {"fold": SimpleNamespace(baseline="seasonal_naive7")}
    with validation_db() as db:
        recipe, sample = fit_recipe(db, parent(), choices, policy)
        calibration = calibrate(sample, recipe, choices, policy)
        row = next(observations(db, validation_only=True))
        exact = calibration["fold", "remediated", "low", "cat"]
        exact["status"] = "not_ready"
        band, used = interval(row, "remediated", 10.0, calibration)
        assert band == (10.0, 10.0) and used["category"] == "*"
        for cell in calibration.values():
            cell["status"] = "not_ready"
        assert interval(row, "remediated", 10.0, calibration) == (None, None)


def test_perfect_validation_baseline_retains_original_predictions():
    choices = {"fold": SimpleNamespace(baseline="seasonal_naive7")}
    with validation_db() as db:
        db.execute("UPDATE predictions SET units=10.0")
        recipe, _ = fit_recipe(db, parent(), choices, RemediationPolicy())
        assert all(not cell["corrected"] for cell in recipe.values())
        assert all(cell["selected"] == cell["baseline"] for cell in recipe.values())
        assert all(cell["status"] == "passed" for cell in recipe.values())
        for row in observations(db):
            assert predict(row, recipe, choices) == row.predictions["validation_baseline"]


def test_original_quality_thresholds_cannot_be_relaxed():
    for name, value in (
        ("minimum_segment_rows", 1),
        ("maximum_absolute_normalized_bias", 0.9),
        ("maximum_mean_width_to_mean_actual", 10),
        ("minimum_empirical_coverage", 0.1),
    ):
        payload = RemediationPolicy().model_dump(mode="json")
        payload["quality"][name] = value
        with pytest.raises(ValidationError, match="preserve_original_quality_thresholds"):
            RemediationPolicy.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("artifacts", [37], indirect=True)
def test_preflight_reads_features_only_and_blocks_missing_critical_volume(artifacts, monkeypatch):
    from retailops_ai.forecasting import remediation_preflight

    features, _, _, _ = artifacts
    original = remediation_preflight.input_models
    requested = []

    def features_only(directory, name):
        requested.append(name)
        assert name == "features", "preflight must not read target labels"
        return original(directory, name)

    monkeypatch.setattr(remediation_preflight, "input_models", features_only)
    report = remediation_preflight.preflight(
        features,
        BacktestPolicy(
            folds=2,
            initial_train_days=1,
            validation_days=2,
            development_holdout_days=2,
            step_days=2,
        ),
    )
    assert requested == ["features"]
    assert report["status"] == "not_ready"
    assert report["model_fits"] == 0 and not report["target_outcomes_read"]
    assert not report["model_quality_qualified"]
    assert len(report["gates"]) == 3 * 2 * 4
    assert all(g["minimum_rows"] == 30 for g in report["gates"])
    assert all(
        g["status"] == "not_ready" and g["feature_eligible_rows"] == 0
        for g in report["gates"]
        if g["volume"] == "zero"
    )


@pytest.mark.parametrize("artifacts", [37], indirect=True)
def test_source_sample_bounds_dominate_actual_features_and_never_read_target_outcomes(
    artifacts, monkeypatch
):
    from retailops_ai.forecasting.features import OriginFeatures
    from retailops_ai.forecasting.remediation_preflight import preflight
    from retailops_ai.forecasting.remediation_source_preflight import source_preflight

    features, _, curated, calendar = artifacts
    policy = BacktestPolicy(
        folds=2,
        initial_train_days=1,
        validation_days=2,
        development_holdout_days=2,
        step_days=2,
    )
    actual = preflight(features, policy)

    def prohibit_targets(*args, **kwargs):
        raise AssertionError("Source sample bounds must not build or inspect target rows")

    monkeypatch.setattr(OriginFeatures, "targets", prohibit_targets)
    bounded = source_preflight(curated, calendar, policy)
    assert bounded["status"] == "not_ready"
    assert not bounded["target_outcomes_evaluated"] and not bounded["features_materialized"]
    assert not bounded["target_labels_materialized"]
    assert bounded["history_rows_selected_only_as_of_origin"]
    assert bounded["full_feature_preflight_required_if_not_rejected"]
    assert bounded["model_fits"] == 0 and not bounded["model_quality_qualified"]
    for upper, measured in zip(bounded["gates"], actual["gates"], strict=True):
        assert (upper["fold"], upper["role"], upper["volume"]) == (
            measured["fold"],
            measured["role"],
            measured["volume"],
        )
        assert upper["potential_feature_rows_upper_bound"] >= measured["feature_eligible_rows"]
        if upper["volume"] == "zero":
            assert upper["potential_feature_rows_upper_bound"] == 0


@pytest.mark.parametrize("artifacts", [37], indirect=True)
def test_immutable_remediation_parent_replay_and_rehashed_forgery(artifacts, tmp_path, capsys):
    from retailops_ai.forecasting.cli import main

    features, _, curated, _ = artifacts
    policy = BacktestPolicy(
        folds=2,
        initial_train_days=1,
        validation_days=2,
        development_holdout_days=2,
        step_days=2,
        model=small_policy(),
    )
    backtest = build_backtest(features, curated, tmp_path / "backtests", policy)
    directory = build_remediation(features, backtest, tmp_path / "remediation")
    manifest = verify_remediation(directory, features, backtest)
    assert manifest.descriptor.quality_status == "not_ready"
    before = (directory / "remediation_manifest.json").read_bytes()
    assert build_remediation(features, backtest, tmp_path / "remediation") == directory
    assert (directory / "remediation_manifest.json").read_bytes() == before
    assert (
        main(
            [
                "remediation-verify",
                "--remediation-dir",
                str(directory),
                "--feature-dir",
                str(features),
                "--backtest-dir",
                str(backtest),
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["quality_status"] == "not_ready"
    forged = tmp_path / "forged"
    shutil.copytree(directory, forged)
    metrics = json.loads((forged / "segments.json").read_bytes())
    next(m for m in metrics["rows"] if m["point"]["status"] == "passed")["bias"] = 999.0
    raw = canonical_bytes(metrics) + b"\n"
    (forged / "segments.json").write_bytes(raw)
    import hashlib

    payload = json.loads((forged / "remediation_manifest.json").read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    payload["receipts"]["segments.json"].update(sha256=digest, size_bytes=len(raw))
    payload["descriptor"]["report_sha256"]["segments.json"] = digest
    payload["remediation_id"] = "forecast-remediation-sha256-" + canonical_sha256(
        payload["descriptor"]
    )
    (forged / "remediation_manifest.json").write_bytes(canonical_bytes(payload) + b"\n")
    load_remediation(forged)
    with pytest.raises(SnapshotError, match="source_replay_mismatch"):
        verify_remediation(forged, features, backtest)
