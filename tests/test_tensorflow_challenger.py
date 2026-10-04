"""PIT, train-only state, full logical grain, masked partial horizons and CPU budgets."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, SERIES
from test_forecast_features import tables as tables
from test_forecast_manifests import plan
from test_forecast_manifests import timeline as timeline

from retailops_ai.evaluation_campaign.comparison import require_same_forecast_keys
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.forecasting.splits import label_point, qualify
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy, ChallengerPrediction
from retailops_ai.tensorflow_challenger.dataset import (
    fit_normalization,
    targets,
    transform_window,
    windows,
)
from retailops_ai.tensorflow_challenger.evaluation import compare_development
from retailops_ai.tensorflow_challenger.pipeline import fit_challenger

FEATURE_ID = "features-sha256-" + "a" * 64
SPLIT_ID = "split-sha256-" + "b" * 64


def records(timeline, day):
    fold = plan()
    view = OriginFeatures(timeline, make_origin(day))
    history = view.history(SERIES)
    result = []
    for row in view.targets(history):
        label = label_point(row, fold, timeline["daily_demand_versions"])
        member = qualify(row, history, fold, FeaturePolicy(), label)
        result.append((row, member, label, history))
    return result


@pytest.fixture
def development(timeline):
    fold = plan()
    train = windows(records(timeline, DAY), fold=fold, role="train")
    validation = windows(records(timeline, fold.validation.start), fold=fold, role="validation")
    return fold, train, validation


def test_partial_horizons_are_masked_and_never_shrink_the_population(timeline):
    fold = plan()
    rows = records(timeline, DAY)[::3]
    grouped = windows(rows, fold=fold, role="train")
    assert require_same_forecast_keys(
        [r[0] for r in rows], [s.row for w in grouped for s in w.samples]
    )[0] == len(rows)
    state = fit_normalization(grouped, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
    _, masks = targets(grouped[0], state)
    assert len(masks) == 14 and sum(masks) == len(rows)
    assert masks[1] == 0
    assert len(transform_window(grouped[0], state)) == state.input_width
    with pytest.raises(SnapshotError, match="duplicate_prediction_key"):
        windows([*rows, rows[0]], fold=fold, role="train")


def test_normalization_and_vocabulary_do_not_fit_validation(development):
    fold, train, validation = development
    state = fit_normalization(train, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
    sample = validation[0].samples[0]
    body = sample.row.model_dump(mode="json")
    next(v for v in body["values"] if v["name"] == "brand")["value"] = "validation-only-brand"
    row = InputRow.model_validate_json(json.dumps(body))
    modified = replace(
        validation[0], samples=(replace(sample, row=row), *validation[0].samples[1:])
    )
    assert "validation-only-brand" not in str(state.model_dump())
    assert transform_window(modified, state) != transform_window(validation[0], state)
    assert (
        fit_normalization(
            train, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID
        ).content_sha256()
        == state.content_sha256()
    )
    with pytest.raises(SnapshotError, match="eligible_fold_train_only"):
        fit_normalization(validation, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)


def test_future_observations_do_not_change_historical_window(timeline, development):
    fold, train, _ = development
    state = fit_normalization(train, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
    future = deepcopy(timeline)
    for row in future["daily_demand_versions"]:
        if row["business_date"] > DAY:
            row["observed_units"] = 99999
    changed = windows(records(future, DAY), fold=fold, role="train")
    assert changed[0].history == train[0].history
    assert transform_window(changed[0], state) == transform_window(train[0], state)


@pytest.mark.parametrize("role", ["development_holdout", "final_test", "purged"])
def test_final_or_holdout_role_is_refused_before_reading(role):
    def forbidden():
        raise AssertionError("must not read outcomes")
        yield

    with pytest.raises(SnapshotError, match="development_role_required"):
        windows(forbidden(), fold=plan(), role=role)


def test_wrong_history_label_or_full_grain_binding_is_rejected(timeline):
    row, member, label, history = records(timeline, DAY)[0]
    mutations = [
        (row, member.model_copy(update={"product_id": "other"}), label, history),
        (
            row,
            member,
            label.model_copy(
                update={"knowledge_cutoff": plan().training_cutoff + timedelta(days=1)}
            ),
            history,
        ),
        (row, member.model_copy(update={"label_content_sha256": "f" * 64}), label, history),
        (row.model_copy(update={"history_context_sha256": "f" * 64}), member, label, history),
    ]
    for invalid in mutations:
        with pytest.raises(SnapshotError, match="binding_mismatch"):
            windows([invalid], fold=plan(), role="train")


@pytest.mark.parametrize(
    "flags",
    [
        {"promotion_allowed": True},
        {"final_test_accessed": True},
        {"promotion_allowed": 0},
        {"trials": 2},
        {"epochs": 26},
        {"cpu_threads": 2},
    ],
)
def test_development_policy_cannot_authorize_final_or_relax_trial_budget(flags):
    with pytest.raises(ValidationError):
        ChallengerPolicy(**flags)


def test_fit_rejects_swapped_roles_and_matrix_budget_before_process(development, tmp_path):
    fold, train, validation = development
    with pytest.raises(SnapshotError, match="fit_requires_train"):
        fit_challenger(
            validation,
            train,
            fold=fold,
            feature_set_id=FEATURE_ID,
            split_id=SPLIT_ID,
            output=tmp_path / "wrong",
        )
    with pytest.raises(SnapshotError, match="matrix_budget"):
        fit_challenger(
            train,
            validation,
            fold=fold,
            feature_set_id=FEATURE_ID,
            split_id=SPLIT_ID,
            output=tmp_path / "over",
            policy=ChallengerPolicy(max_matrix_bytes=1024),
        )
    assert not (tmp_path / "over").exists()


@pytest.mark.parametrize(
    "budget", [{"wall_seconds": 0.001}, {"cpu_seconds": 0.001}, {"rss_bytes": 1024**2}]
)
def test_supervisor_limits_retain_failed_attempt_without_complete_model(
    development, tmp_path, budget
):
    fold, train, validation = development
    root = tmp_path / "limited"
    with pytest.raises(SnapshotError, match="budget_exceeded"):
        fit_challenger(
            train,
            validation,
            fold=fold,
            feature_set_id=FEATURE_ID,
            split_id=SPLIT_ID,
            output=root,
            policy=ChallengerPolicy(**budget),
        )
    assert json.loads((root / "attempt.json").read_text())["status"] == "failed"
    assert not (root / "manifest.json").exists()
    with pytest.raises(FileExistsError):
        fit_challenger(
            train, validation, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID, output=root
        )


def test_all_zero_development_keeps_undefined_wape_and_positive_error(development):
    _, _, validation = development
    original = validation[0]
    samples = tuple(
        replace(s, label=s.label.model_copy(update={"observed_sales_units": 0}))
        for s in original.samples
    )
    zero = (replace(original, samples=samples),)
    predictions = tuple(
        ChallengerPrediction(
            **s.row.model_dump(
                include=set(ChallengerPrediction.model_fields) & set(type(s.row).model_fields)
            ),
            value=FunctionalForecast(mean=5.0, median=5.0, interval=None),
        )
        for s in samples
    )
    report = compare_development(zero, predictions)
    metrics = report["global"]["candidate"]
    assert metrics["mean"]["wape"] is None and metrics["median"]["wape"] is None
    assert metrics["mean"]["mae"] == 5.0
    assert "positive_mean_forecast_on_all_zero_actuals" in report["global"]["failed_reasons"]
    with pytest.raises(SnapshotError, match="population_mismatch"):
        compare_development(zero, predictions[:-1])
