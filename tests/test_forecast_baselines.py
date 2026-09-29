"""Analytical baseline answers, common-key scoring and immutable artifact rejection."""

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, rows
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.baselines import predict
from retailops_ai.forecasting.cli import main
from retailops_ai.forecasting.evaluation import (
    MetricAccumulator,
    build_evaluation,
    load_evaluation,
    select_validation,
    verify_evaluation,
)
from retailops_ai.forecasting.evaluation_contract import BaselinePolicy, BaselinePrediction
from retailops_ai.forecasting.manifest_io import key
from retailops_ai.source_snapshot.files import SnapshotError


def metric(actuals, predictions):
    result = MetricAccumulator()
    for actual, prediction in zip(actuals, predictions, strict=True):
        result.add(actual, prediction)
    return result.result()


def test_fixed_origin_analytical_baselines_and_horizons_eight_to_fourteen(tables):
    history, output = rows(tables)
    policy = BaselinePolicy()
    # D-1=39 is the last known value; D is not yet available. MA7 knows six dates.
    assert predict("last_observed", output[0], history, policy).predicted_units == 39.0
    average = predict("moving_average", output[0], history, policy)
    assert average.predicted_units == 36.5
    assert average.history_dates == tuple(DAY - timedelta(days=n) for n in range(6, 0, -1))
    assert predict("seasonal_naive7", output[0], history, policy).predicted_units == 34.0
    for first, second in zip(output[:7], output[7:], strict=True):
        one = predict("seasonal_naive7", first, history, policy)
        two = predict("seasonal_naive7", second, history, policy)
        assert one == two
        assert all(day <= DAY for day in two.history_dates)
    # Future facts/corrections cannot change the forecast context or any prediction.
    changed = deepcopy(tables)
    for row in changed["daily_demand_versions"]:
        if row["business_date"] >= DAY:
            row["observed_units"] = 999999
    later_history, later_output = rows(changed)
    assert later_history == history
    for name in policy.candidates:
        assert [predict(name, row, history, policy) for row in output] == [
            predict(name, row, later_history, policy) for row in later_output
        ]


def test_zero_and_closed_history_are_known_missing_is_not_zero(tables):
    # Remove D-1 entirely; confirm D-2 closed with a real zero observation.
    tables["daily_demand_versions"] = [
        row
        for row in tables["daily_demand_versions"]
        if row["business_date"] != DAY - timedelta(days=1)
    ]
    for row in tables["daily_demand_versions"]:
        if row["business_date"] == DAY - timedelta(days=2):
            row.update(observed_units=0, observation_status="closed")
    for row in tables["business_calendar"]:
        if row["business_date"] == DAY - timedelta(days=2):
            row["location_open"] = False
    history, output = rows(tables)
    assert history.points[-3].status == "closed"
    assert predict("last_observed", output[0], history, BaselinePolicy()).predicted_units == 0.0
    average = predict("moving_average", output[0], history, BaselinePolicy())
    assert average.predicted_units == (34 + 35 + 36 + 37 + 0) / 5
    assert len(average.history_dates) == 5


def test_seasonal_missing_searches_older_calendar_week_and_never_uses_other_weekday(tables):
    target_weekday = (DAY + timedelta(days=1)).weekday()
    expected = DAY - timedelta(days=13)
    tables["daily_demand_versions"] = [
        row
        for row in tables["daily_demand_versions"]
        if row["business_date"] != DAY - timedelta(days=6)
    ]
    history, output = rows(tables)
    estimate = predict("seasonal_naive7", output[0], history, BaselinePolicy())
    assert estimate.history_dates == (expected,)
    assert estimate.predicted_units == 27.0
    tables["daily_demand_versions"] = [
        row
        for row in tables["daily_demand_versions"]
        if row["business_date"].weekday() != target_weekday
    ]
    history, output = rows(tables)
    estimate = predict("seasonal_naive7", output[0], history, BaselinePolicy())
    assert estimate.predicted_units is None and estimate.reason == "no_known_same_weekday"


def test_window_is_calendar_days_and_no_history_has_no_prediction(tables):
    tables["daily_demand_versions"] = [
        row
        for row in tables["daily_demand_versions"]
        if row["business_date"] < DAY - timedelta(days=6)
    ]
    history, output = rows(tables)
    assert (
        predict("moving_average", output[0], history, BaselinePolicy()).reason
        == "insufficient_calendar_window"
    )
    assert predict("last_observed", output[0], history, BaselinePolicy()).predicted_units == 33.0
    tables["daily_demand_versions"] = []
    history, output = rows(tables)
    for name in BaselinePolicy().candidates:
        estimate = predict(name, output[0], history, BaselinePolicy())
        assert estimate.predicted_units is None and not estimate.history_dates


def test_wrong_history_and_forged_future_availability_are_rejected(tables):
    history, output = rows(tables)
    wrong = history.model_copy(update={"product_id": "other"})
    with pytest.raises((SnapshotError, ValidationError)):
        predict("last_observed", output[0], wrong, BaselinePolicy())
    point = history.points[-2].model_copy(
        update={"source_available_at": output[0].forecast_origin + timedelta(microseconds=1)}
    )
    forged = history.model_copy(
        update={"points": (*history.points[:-2], point, history.points[-1])}
    )
    with pytest.raises(ValidationError):
        predict("last_observed", output[0], forged, BaselinePolicy())


@pytest.mark.parametrize(
    "actuals,predictions,mae,wape",
    [
        ([0], [100.0], 100.0, None),
        ([0, 10], [100.0, 10.0], 50.0, 10.0),
        ([1, 100], [2.0, 90.0], 5.5, 11 / 101),
        ([0, 0], [0.0, 0.0], 0.0, None),
    ],
)
def test_mae_wape_use_global_denominator_and_include_zero_actuals(actuals, predictions, mae, wape):
    result = metric(actuals, predictions)
    assert result.mae == mae and result.wape == wape
    assert result.status == "passed"
    assert result.wape_status == ("zero_denominator" if wape is None else "passed")


def test_no_rows_and_missing_prediction_never_produce_partial_scores():
    assert metric([], []).status == "not_evaluable"
    result = metric([10, 100], [10.0, None])
    assert result.eligible_rows == 2 and result.predicted_rows == 1
    assert result.status == "incomplete"
    assert result.mae is result.wape is result.absolute_error_sum is None


@pytest.mark.parametrize("prediction", [-1.0, float("nan"), float("inf"), True])
def test_invalid_predictions_fail_instead_of_entering_metrics(prediction):
    with pytest.raises(SnapshotError):
        metric([1], [prediction])


def test_selection_is_validation_only_with_frozen_tie_order_and_full_common_coverage():
    policy = BaselinePolicy()
    values = {name: metric([10], [10.0]) for name in policy.candidates}
    options = dict(
        fold="fold-a",
        feature_set_id="features-sha256-" + "a" * 64,
        split_id="split-sha256-" + "b" * 64,
    )
    selected = select_validation(policy, values, "c" * 64, **options)
    assert selected.model == "last_observed"
    # Extreme holdout scores have no input path into selection, nor into its hash.
    metric([999999], [0.0])
    assert select_validation(policy, values, "c" * 64, **options) == selected
    values["last_observed"] = metric([10], [12.0])
    assert select_validation(policy, values, "c" * 64, **options).model == "moving_average"
    values["seasonal_naive7"] = metric([10], [None])
    blocked = select_validation(policy, values, "c" * 64, **options)
    assert blocked.status == "not_ready" and blocked.model is None
    del values["seasonal_naive7"]
    with pytest.raises(SnapshotError):
        select_validation(policy, values, "c" * 64, **options)


def test_real_artifact_common_keys_replay_incomplete_policy_and_rehashed_forgery(
    artifacts, tmp_path, capsys
):
    features, split, *_ = artifacts
    directory = build_evaluation(features, split, tmp_path / "evaluation")
    manifest = verify_evaluation(directory, features, split)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    assert build_evaluation(features, split, tmp_path / "evaluation") == directory
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert manifest.descriptor.status == "passed"
    assert manifest.predictions.row_count == 462 * 3
    assert manifest.descriptor.selections[0].model == "last_observed"
    records = [
        BaselinePrediction.model_validate_json(line)
        for line in before["predictions.jsonl"].splitlines()
    ]
    sets = {
        name: {key(row) for row in records if row.model == name}
        for name in BaselinePolicy().candidates
    }
    assert len(sets["last_observed"]) == 462
    assert len({frozenset(keys) for keys in sets.values()}) == 1
    assert sum(row.role == "purged" for row in records) == 420 * 3
    assert all(row.estimate is None for row in records if not row.eligible)
    assert (
        main(
            [
                "evaluation-verify",
                "--evaluation-dir",
                str(directory),
                "--feature-dir",
                str(features),
                "--split-dir",
                str(split),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
    incomplete = build_evaluation(
        features,
        split,
        tmp_path / "incomplete",
        BaselinePolicy(moving_average_minimum_known_days=7),
    )
    blocked = load_evaluation(incomplete)
    assert blocked.descriptor.status == "not_ready"
    assert blocked.descriptor.selections[0].model is None
    assert blocked.descriptor.metrics["fold-a:validation:moving_average"].predicted_rows == 0
    for name in BaselinePolicy().candidates:
        held_out = blocked.descriptor.metrics["fold-a:development_holdout:" + name]
        assert held_out.status == "selection_not_ready" and held_out.mae is None
    assert blocked.predictions.row_count == manifest.predictions.row_count
    assert (
        main(
            [
                "baselines-evaluate",
                "--feature-dir",
                str(features),
                "--split-dir",
                str(split),
                "--config",
                str(tmp_path / "missing-config"),
            ]
        )
        == 2
    )
    assert "forecast_rejected" in capsys.readouterr().err
    forged = tmp_path / "forged"
    shutil.copytree(directory, forged)
    payload = json.loads((forged / "evaluation_manifest.json").read_bytes())
    rows_json = [json.loads(line) for line in before["predictions.jsonl"].splitlines()]
    prediction = next(row for row in rows_json if row["eligible"])
    prediction["estimate"]["predicted_units"] += 100.0
    content = b"".join(canonical_bytes(row) + b"\n" for row in rows_json)
    (forged / "predictions.jsonl").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    payload["predictions"]["content_sha256"] = digest
    payload["predictions"]["files"][0].update(size_bytes=len(content), sha256=digest)
    payload["descriptor"]["predictions_content_sha256"] = digest
    payload["evaluation_id"] = "forecast-evaluation-sha256-" + canonical_sha256(
        payload["descriptor"]
    )
    (forged / "evaluation_manifest.json").write_text(json.dumps(payload))
    load_evaluation(forged)  # Self-consistent hashes alone are deliberately insufficient.
    with pytest.raises(SnapshotError, match="recomputation_mismatch"):
        verify_evaluation(forged, features, split)
    (forged / "extra.json").write_text("{}")
    with pytest.raises(SnapshotError):
        load_evaluation(forged)
