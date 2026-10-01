"""Chronological plans, pooled denominators and complete independent multi-fold runs."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_forecast_baselines import metric
from test_forecast_features import DAY, SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_forecast_models import small_policy

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.backtest import (
    build_backtest,
    load_backtest,
    pool_metrics,
    verify_backtest,
)
from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.contract import OriginWindow, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.model_contract import ModelPipeline
from retailops_ai.forecasting.models import load_comparison
from retailops_ai.forecasting.splits import label_point
from retailops_ai.source_snapshot.files import SnapshotError


def window(days=60):
    start = date(2026, 5, 19)
    return OriginWindow(start=start, end=start + timedelta(days=days - 1))


@pytest.mark.parametrize("mode", ["expanding", "rolling"])
def test_plans_have_ordered_cutoffs_disjoint_evaluation_and_explicit_train_windows(mode):
    policy = BacktestPolicy(mode=mode)
    plan = plan_backtest(window(), policy)
    assert [fold.validation.start for fold in plan.folds] == [
        date(2026, 6, 9),
        date(2026, 6, 15),
        date(2026, 6, 21),
    ]
    assert plan.folds[-1].development_holdout.end == date(2026, 7, 17)
    assert [(fold.train.end - fold.train.start).days + 1 for fold in plan.folds] == (
        [6, 12, 18] if mode == "expanding" else [6, 6, 6]
    )
    for role in ("validation", "development_holdout"):
        assert all(
            getattr(first, role).end < getattr(second, role).start
            for first, second in zip(plan.folds, plan.folds[1:], strict=False)
        )
    for fold in plan.folds:
        assert fold.training_cutoff.date() < fold.validation.start
        assert fold.selection_cutoff.date() < fold.development_holdout.start
        assert (fold.validation.start - fold.train.end).days == 16
    assert plan.portfolio_final_test == "not_included_not_opened"
    assert policy.development_holdout_use.endswith("not_fresh_final_test")


@pytest.mark.parametrize("kwargs", [{"step_days": 5}, {"purge_days": 14}, {"folds": 1}])
def test_repeated_evaluation_origins_short_maturity_gap_and_single_fold_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        BacktestPolicy(**kwargs)


def test_too_short_calendar_cannot_silently_shrink_windows():
    with pytest.raises(ValueError, match="insufficient_origin_window"):
        plan_backtest(window(59), BacktestPolicy())
    assert plan_backtest(window(61), BacktestPolicy()) == plan_backtest(window(), BacktestPolicy())
    assert plan_backtest(window(), BacktestPolicy(mode="rolling")) != plan_backtest(
        window(), BacktestPolicy()
    )


def test_pooled_metrics_use_sums_keep_zero_errors_and_block_incomplete_folds():
    zero = metric([0], [100.0])
    positive = metric([900, 100], [990.0, 80.0])
    pooled = pool_metrics([zero, positive])
    assert pooled.eligible_rows == 3 and pooled.mae == 70.0 and pooled.wape == 0.21
    all_zero = pool_metrics([zero, metric([0], [10.0])])
    assert all_zero.mae == 55.0 and all_zero.wape is None
    assert all_zero.wape_status == "zero_denominator"
    incomplete = pool_metrics([positive, metric([100], [None])])
    assert incomplete.status == "incomplete" and incomplete.mae is None
    assert incomplete.eligible_rows == 3 and incomplete.predicted_rows == 2
    empty = pool_metrics([positive, metric([], [])])
    assert empty.status == "selection_not_ready" and empty.wape is None


def test_late_correction_changes_only_training_fold_that_can_know_it(timeline):
    policy = BacktestPolicy(
        folds=2,
        initial_train_days=1,
        validation_days=1,
        development_holdout_days=1,
        step_days=1,
        model=small_policy(),
    )
    plan = plan_backtest(OriginWindow(start=DAY, end=DAY + timedelta(days=33)), policy)
    view = OriginFeatures(timeline, make_origin(DAY))
    row = view.targets(view.history(SERIES))[0]
    before = label_point(row, plan.folds[0], timeline["daily_demand_versions"])
    correction = deepcopy(
        next(r for r in timeline["daily_demand_versions"] if r["business_date"] == row.target_date)
    )
    correction.update(
        id="late-correction",
        version=2,
        observed_units=9999,
        curated_available_at=plan.folds[0].training_cutoff + timedelta(microseconds=1),
        source_record_sha256="f" * 64,
    )
    changed = [*timeline["daily_demand_versions"], correction]
    assert label_point(row, plan.folds[0], changed) == before
    known_later = label_point(row, plan.folds[1], changed)
    assert known_later.observed_sales_units == 9999
    assert known_later.label_available_at <= plan.folds[1].training_cutoff


def hashes(directory):
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize("artifacts", [34], indirect=True)
def test_full_two_fold_training_source_rebuild_keys_immutable_rerun_and_forged_report(
    artifacts, tmp_path, monkeypatch
):
    features, _, curated, _ = artifacts
    from retailops_ai.forecasting import models

    observed = []
    original = models.fit_worker

    def record(x, y, family, policy):
        observed.append((family, len(y)))
        return original(x, y, family, policy)

    monkeypatch.setattr(models, "fit_worker", record)
    policy = BacktestPolicy(
        folds=2,
        initial_train_days=1,
        validation_days=1,
        development_holdout_days=1,
        step_days=1,
        model=small_policy(),
    )
    before = hashes(features)
    directory = build_backtest(features, curated, tmp_path / "backtests", policy)
    manifest = verify_backtest(directory, features, curated)
    output_before = hashes(directory)
    assert build_backtest(features, curated, tmp_path / "backtests", policy) == directory
    assert output_before == hashes(directory) and before == hashes(features)
    assert manifest.descriptor.status == "passed" and manifest.forecast_model_status == "not_ready"
    assert [audit.eligible_train_rows for audit in manifest.descriptor.folds] == [14, 28]
    assert [count for _, count in observed] == [14, 14, 28, 28] * 3
    comparison = load_comparison(directory / "comparison")
    assert comparison.predictions.row_count == 34 * 14 * 2 * 5
    assert (
        manifest.descriptor.pooled_metrics["development_holdout:validation_selected"].eligible_rows
        == 28
    )
    states = []
    for audit in manifest.descriptor.folds:
        assert audit.common_prediction_keys == 34 * 14
        assert audit.latest_train_label_available_at <= audit.plan.training_cutoff
        path = comparison.pipelines[audit.plan.name + ":random_forest"].path
        states.append(
            ModelPipeline.model_validate_json(
                (directory / "comparison" / path).read_bytes()
            ).descriptor.preprocessing
        )
    assert states[0].train_content_sha256 != states[1].train_content_sha256
    assert states[0].numeric != states[1].numeric
    forged = tmp_path / "forged"
    shutil.copytree(directory, forged)
    payload = json.loads((forged / "backtest_manifest.json").read_bytes())
    metric_name = "development_holdout:hist_gradient_boosting"
    value = payload["descriptor"]["pooled_metrics"][metric_name]
    value["absolute_error_sum"] += value["eligible_rows"]
    value["mae"] = value["absolute_error_sum"] / value["eligible_rows"]
    value["wape"] = value["absolute_error_sum"] / value["absolute_actual_sum"]
    payload["backtest_id"] = "forecast-backtest-sha256-" + canonical_sha256(payload["descriptor"])
    (forged / "backtest_manifest.json").write_text(json.dumps(payload))
    (forged / "metrics.json").write_bytes(
        canonical_bytes(payload["descriptor"]["pooled_metrics"]) + b"\n"
    )
    with pytest.raises(SnapshotError, match="report_replay_mismatch"):
        load_backtest(forged)


def test_versioned_backtest_config_and_schemas():
    from jsonschema import Draft202012Validator

    contracts = Path(__file__).resolve().parents[1] / "contracts/forecast/v1"
    assert (
        BacktestPolicy.model_validate_json((contracts / "backtest.default.json").read_bytes())
        == BacktestPolicy()
    )
    for name in ("backtest_policy", "backtest_manifest"):
        Draft202012Validator.check_schema(
            json.loads((contracts / (name + ".schema.json")).read_bytes())
        )


def test_cli_rejects_overlapping_policy_before_reading_parents_or_writing_output(tmp_path, capsys):
    from retailops_ai.forecasting.cli import main

    config = tmp_path / "invalid.json"
    payload = BacktestPolicy().model_dump(mode="json")
    payload["step_days"] = 5
    config.write_text(json.dumps(payload))
    output = tmp_path / "output"
    assert (
        main(
            [
                "backtest-run",
                "--feature-dir",
                str(tmp_path / "missing_features"),
                "--curated-dir",
                str(tmp_path / "missing_curated"),
                "--config",
                str(config),
                "--output-root",
                str(output),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "forecast_rejected" in captured.err and str(tmp_path) not in captured.err
    assert captured.out == "" and not output.exists()
