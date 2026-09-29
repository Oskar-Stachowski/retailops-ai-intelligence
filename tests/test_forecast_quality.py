"""Quality zeros, common coverage, calibration isolation and parent-backed report replay."""

import hashlib
import json
import math
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path
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
from retailops_ai.forecasting.quality import (
    REPORT_METHODS,
    _calibrate,
    build_quality,
    load_quality,
    verify_quality,
)
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import (
    SegmentAccumulator,
    assess_segment,
    interval_bounds,
    residual_rank,
    volume_bin,
)
from retailops_ai.source_snapshot.files import SnapshotError


def stats(actuals, predictions, bands=None, *, method="validation_selected"):
    accumulator = SegmentAccumulator(0.9)
    for actual, prediction, band in zip(
        actuals, predictions, bands or [None] * len(actuals), strict=True
    ):
        accumulator.add(actual, prediction, (), band)
    return accumulator.result("fold", "development_holdout", method, "global", "all")


def test_extended_statistics_keep_zero_errors_signed_bias_positive_mape_and_interval_width():
    result = stats([0, 10, 20], [100.0, 8.0, 22.0], [(0.0, 110.0), (7.0, 12.0), (19.0, 23.0)])
    assert result.point.mae == 104 / 3 and result.point.wape == 104 / 30
    assert result.rmse == math.sqrt(10008 / 3) and result.bias == 100 / 3
    assert result.normalized_bias == 100 / 30
    assert result.underforecast_units == 2 and result.overforecast_units == 102
    assert result.underforecast_rows == 1 and result.overforecast_rows == 2
    assert result.mape_positive_actuals == pytest.approx(0.15) and result.mape_coverage == 2 / 3
    assert result.zero_actual_rows == 1 and result.zero_actual_excess_forecast_units == 100
    assert result.interval.empirical_coverage == 1 and result.interval.mean_width == 119 / 3


def test_all_zero_actuals_cannot_pass_through_wape_zero_or_mape_exclusion():
    candidate = stats([0], [100.0], [(0.0, 200.0)])
    baseline = stats([0], [0.0], [(0.0, 200.0)], method="validation_baseline")
    gate = assess_segment(candidate, baseline, QualityPolicy(minimum_global_rows=1), retained=False)
    assert candidate.point.wape is None and candidate.point.wape_status == "zero_denominator"
    assert candidate.point.mae == 100 and candidate.rmse == 100 and candidate.bias == 100
    assert candidate.mape_positive_actuals is None and candidate.mape_coverage == 0
    assert (
        gate["status"] == "not_ready"
        and "zero_or_missing_actual_denominator" in gate["not_ready_reasons"]
    )


def test_incomplete_predictions_intervals_and_exclusions_do_not_become_partial_success():
    result = stats([5, 20], [None, 21.0], [None, (19.0, 22.0)])
    assert result.point.status == "incomplete" and result.rmse is None and result.bias is None
    assert result.interval.status == "incomplete" and result.interval.empirical_coverage is None
    accumulator = SegmentAccumulator(0.9)
    accumulator.add(None, None, ("closed_target", "insufficient_history"), None)
    excluded = accumulator.result("fold", "validation", "random_forest", "channel", "store")
    assert excluded.total_rows == 1 and excluded.excluded_rows == 1
    assert excluded.point.status == "not_evaluable" and excluded.eligibility_coverage == 0
    assert excluded.exclusion_counts == {"closed_target": 1, "insufficient_history": 1}


def test_missing_critical_segment_and_bias_regression_coverage_width_gates():
    empty = stats([], [])
    empty_baseline = stats([], [], method="validation_baseline")
    gate = assess_segment(empty, empty_baseline, QualityPolicy(), retained=True)
    assert gate["status"] == "not_ready" and "insufficient_sample" in gate["not_ready_reasons"]
    candidate = stats([10] * 100, [30.0] * 100, [(30.0, 100.0)] * 100)
    baseline = stats([10] * 100, [11.0] * 100, [(0.0, 100.0)] * 100, method="validation_baseline")
    gate = assess_segment(candidate, baseline, QualityPolicy(), retained=False)
    assert gate["status"] == "failed"
    assert set(gate["failed_reasons"]) == {
        "absolute_normalized_bias_exceeded",
        "global_mae_improvement_below_minimum",
        "empirical_interval_coverage_below_minimum",
        "interval_width_exceeded",
    }
    retained = stats([10] * 100, [10.0] * 100, [(9.0, 11.0)] * 100)
    retained_baseline = stats(
        [10] * 100, [10.0] * 100, [(9.0, 11.0)] * 100, method="validation_baseline"
    )
    assert (
        assess_segment(retained, retained_baseline, QualityPolicy(), retained=True)["status"]
        == "passed"
    )
    with pytest.raises(ValueError, match="common_coverage"):
        assess_segment(retained, empty_baseline, QualityPolicy(), retained=True)


def test_volume_thresholds_quantile_rank_and_policy_validation_are_explicit():
    policy = QualityPolicy()
    assert [volume_bin(n, policy) for n in (None, 0, 4.99, 5, 19.99, 20)] == [
        "unknown",
        "zero",
        "low",
        "medium",
        "medium",
        "high",
    ]
    assert residual_rank(49, policy) is None and residual_rank(50, policy) == 46
    assert residual_rank(1, QualityPolicy(minimum_calibration_rows=1)) is None
    assert interval_bounds(1.0, 3.0) == (0.0, 4.0) and interval_bounds(1.0, None) is None
    with pytest.raises(ValueError):
        volume_bin(float("nan"), policy)
    with pytest.raises(ValidationError):
        QualityPolicy(low_volume_upper_exclusive=20.0, medium_volume_upper_exclusive=5.0)
    with pytest.raises(ValidationError):
        QualityPolicy(nominal_coverage=0.5, minimum_empirical_coverage=0.9)


def test_calibration_never_uses_holdout_outcomes_and_blocks_late_labels():
    cutoff = end_of_day(date(2026, 6, 29))
    parent = SimpleNamespace(
        descriptor=SimpleNamespace(
            folds=(SimpleNamespace(plan=SimpleNamespace(name="fold", selection_cutoff=cutoff)),)
        )
    )
    policy = QualityPolicy(
        minimum_calibration_rows=1, nominal_coverage=0.5, minimum_empirical_coverage=0.5
    )
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE features (key BLOB,horizon INTEGER);
            CREATE TABLE members (key BLOB,feature_key BLOB,fold TEXT,role TEXT,eligible INTEGER);
            CREATE TABLE labels (key BLOB,actual INTEGER,available TEXT);
            CREATE TABLE predictions (key BLOB,method TEXT,units REAL);
        """)
        for role, actual in (("validation", 10), ("development_holdout", 9000)):
            grain = canonical_bytes([role])
            db.execute("INSERT INTO features VALUES (?,1)", (grain,))
            db.execute("INSERT INTO members VALUES (?,?,'fold',?,1)", (grain, grain, role))
            db.execute("INSERT INTO labels VALUES (?,?,?)", (grain, actual, cutoff.isoformat()))
            for method in REPORT_METHODS:
                db.execute("INSERT INTO predictions VALUES (?,?,12.0)", (grain, method))
        first = _calibrate(db, parent, policy)
        assert first[0]["residual_quantile_units"] == 2 and first[0]["eligible_rows"] == 1
        assert first[1]["status"] == "not_ready"  # Missing horizon remains visible.
        db.execute(
            "UPDATE labels SET actual=999999 WHERE key=?",
            (canonical_bytes(["development_holdout"]),),
        )
        assert _calibrate(db, parent, policy) == first
        db.execute(
            "UPDATE labels SET available=? WHERE key=?",
            ((cutoff + timedelta(microseconds=1)).isoformat(), canonical_bytes(["validation"])),
        )
        with pytest.raises(SnapshotError, match="unavailable_at_selection"):
            _calibrate(db, parent, policy)


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("artifacts", [34], indirect=True)
def test_two_fold_quality_replay_immutable_parents_sparse_segments_and_rehashed_forgery(
    artifacts, tmp_path, capsys
):
    from retailops_ai.forecasting.cli import main

    features, _, curated, _ = artifacts
    policy = BacktestPolicy(
        folds=2,
        initial_train_days=1,
        validation_days=1,
        development_holdout_days=1,
        step_days=1,
        model=small_policy(),
    )
    backtest = build_backtest(features, curated, tmp_path / "backtests", policy)
    before = {"features": hashes(features), "backtest": hashes(backtest)}
    directory = build_quality(features, backtest, tmp_path / "quality")
    manifest = verify_quality(directory, features, backtest)
    output_before = hashes(directory)
    assert build_quality(features, backtest, tmp_path / "quality") == directory
    assert output_before == hashes(directory) and before == {
        "features": hashes(features),
        "backtest": hashes(backtest),
    }
    assert (
        manifest.descriptor.quality_status == "not_ready"
        and manifest.forecast_model_status == "not_ready"
    )
    assert manifest.descriptor.calibration_status_counts == {"not_ready": 2 * 7 * 14}
    segments = json.loads((directory / "segments.json").read_bytes())["rows"]
    pooled = [m for m in segments if m["fold"] == "pooled" and m["dimension"] == "global"]
    assert len(pooled) == 2 * 7 and {m["point"]["eligible_rows"] for m in pooled} == {28}
    assert any(
        m["dimension"] == "volume" and m["value"] == "zero" and m["total_rows"] == 0
        for m in segments
    )
    intervals = [
        json.loads(line) for line in (directory / "intervals.jsonl").read_bytes().splitlines()
    ]
    assert len(intervals) == 28 * 2 * 7 and all(row["lower_units"] is None for row in intervals)
    assert (
        main(
            [
                "quality-verify",
                "--quality-dir",
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
    changed = QualityPolicy(
        maximum_absolute_normalized_bias=0.11,
        minimum_calibration_rows=1,
        nominal_coverage=0.5,
        minimum_empirical_coverage=0.5,
    )
    calibrated = build_quality(features, backtest, tmp_path / "quality", changed)
    assert calibrated != directory
    assert load_quality(calibrated).descriptor.calibration_status_counts == {"passed": 2 * 7 * 14}
    ready_bands = [
        json.loads(line) for line in (calibrated / "intervals.jsonl").read_bytes().splitlines()
    ]
    assert len(ready_bands) == len(intervals) and all(
        0 <= row["lower_units"] <= row["predicted_units"] <= row["upper_units"]
        for row in ready_bands
    )
    assert all(
        row["forecast_origin"] > row["calibration_cutoff"]
        for row in ready_bands
        if row["role"] == "development_holdout"
    )
    forged = tmp_path / "forged"
    shutil.copytree(directory, forged)
    segments[0]["bias"] = 99.0 if segments[0]["bias"] is not None else None
    # Pick an actual nonempty metric to forge, preserving all count invariants.
    next(m for m in segments if m["point"]["status"] == "passed")["bias"] = 999.0
    raw = canonical_bytes({"rows": segments}) + b"\n"
    (forged / "segments.json").write_bytes(raw)
    payload = json.loads((forged / "quality_manifest.json").read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    payload["receipts"]["segments.json"].update(sha256=digest, size_bytes=len(raw))
    payload["descriptor"]["report_sha256"]["segments.json"] = digest
    payload["quality_id"] = "forecast-quality-sha256-" + canonical_sha256(payload["descriptor"])
    (forged / "quality_manifest.json").write_text(json.dumps(payload))
    load_quality(forged)  # Receipt integrity alone is explicitly not semantic verification.
    with pytest.raises(SnapshotError, match="source_replay_mismatch"):
        verify_quality(forged, features, backtest)


def test_quality_config_schema_and_invalid_cli_policy_no_output(tmp_path, capsys):
    from jsonschema import Draft202012Validator

    from retailops_ai.forecasting.cli import main

    contracts = Path(__file__).resolve().parents[1] / "contracts/forecast/v1"
    assert (
        QualityPolicy.model_validate_json((contracts / "quality.default.json").read_bytes())
        == QualityPolicy()
    )
    for name in ("quality_policy", "quality_manifest", "segment_metric"):
        Draft202012Validator.check_schema(
            json.loads((contracts / (name + ".schema.json")).read_bytes())
        )
    config = tmp_path / "invalid.json"
    config.write_text('{"minimum_prediction_coverage":0.5}')
    output = tmp_path / "output"
    assert (
        main(
            [
                "quality-evaluate",
                "--feature-dir",
                "missing",
                "--backtest-dir",
                "missing",
                "--config",
                str(config),
                "--output-root",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists() and "forecast_rejected" in capsys.readouterr().err
