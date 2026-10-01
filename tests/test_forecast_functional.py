"""Separate targets, train/validation boundaries, portable quantiles and full replay."""

import json
from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import sample
from test_forecast_manifests import timeline as timeline
from test_forecast_models import small_policy

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.functional_campaign import (
    build_campaign,
    load_campaign,
    verify_campaign,
)
from retailops_ai.forecasting.functional_contract import BASELINES, FunctionalPolicy
from retailops_ai.forecasting.functional_models import fit_worker, functional_code
from retailops_ai.forecasting.functional_preprocessing import (
    fit_ordered_train_samples,
    transform_values,
)
from retailops_ai.forecasting.functional_recipe import (
    Observation,
    empirical_baselines,
    fit_recipe,
    predict_pair,
)
from retailops_ai.forecasting.manifest_contract import FeaturePolicy, FoldPlan
from retailops_ai.forecasting.manifests import load_feature_set
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.preprocessing import fit_train_samples, transform
from retailops_ai.forecasting.splits import build_split, load_split
from retailops_ai.source_snapshot.files import SnapshotError


def test_streaming_preprocessing_preserves_encoding_and_train_hash(tables):
    row, _, _, member = sample(tables)
    from test_forecast_manifests import plan

    args = dict(
        fold=plan(),
        policy=FeaturePolicy(),
        feature_set_id="features-sha256-" + "a" * 64,
        split_id="split-sha256-" + "b" * 64,
    )
    prior = fit_train_samples([(row, member)], **args)
    streamed = fit_ordered_train_samples(iter([(row, member)]), **args)
    assert prior.descriptor.model_dump(exclude={"code"}) == streamed.descriptor.model_dump(
        exclude={"code"}
    )
    assert transform_values({v.name: v.value for v in row.values}, streamed) == transform(
        row, prior, feature_set_id=args["feature_set_id"]
    )
    with pytest.raises(SnapshotError, match="unique_sorted"):
        fit_ordered_train_samples([(row, member), (row, member)], **args)
    with pytest.raises(SnapshotError, match="eligible_fold_train"):
        fit_ordered_train_samples([(row, member.model_copy(update={"role": "validation"}))], **args)


@pytest.mark.parametrize(
    "head,expected",
    [
        ("hgb_median", 0.0),
        ("hgb_lower", 0.0),
        ("hgb_upper", 10.0),
        ("hgb_mean", 2.0),
        ("rf_mean", None),
    ],
)
def test_bounded_worker_exports_distinct_mean_and_quantiles(head, expected):
    x = np.zeros((100, 2), dtype=np.float64)
    y = np.asarray([0.0] * 80 + [10.0] * 20)
    estimator, receipt = fit_worker(x, y, head, FunctionalPolicy(model=small_policy()))
    values = TreePredictor(estimator).matrix(np.zeros((3, 2), dtype=np.float64))
    if expected is not None:
        np.testing.assert_allclose(values, expected, rtol=1e-12, atol=1e-12)
    assert receipt.cpu_seconds < 90
    assert receipt.peak_rss_bytes < 1024**3


def test_empirical_baseline_keeps_mean_separate_from_median_and_checks_binding(tables):
    row, history, _, _ = sample(tables)
    points = tuple(
        p.model_copy(update={"observed_units": 10 if i % 5 == 0 else 0})
        for i, p in enumerate(history.points)
    )
    intermittent = history.model_copy(update={"points": points})
    bound = row.model_copy(update={"history_context_sha256": intermittent.content_sha256()})
    points, _ = empirical_baselines(bound, intermittent)
    assert points["history28:median"] == 0
    assert points["history28:mean"] > 0
    with pytest.raises(SnapshotError, match="history_binding"):
        empirical_baselines(row, intermittent)


def recipe_fixture():
    from test_forecast_manifests import DAY

    fold = FoldPlan(
        name="test",
        train=DateWindow(start=DAY, end=DAY),
        validation=DateWindow(start=DAY + timedelta(days=16), end=DAY + timedelta(days=17)),
        development_holdout=DateWindow(
            start=DAY + timedelta(days=33), end=DAY + timedelta(days=33)
        ),
        training_cutoff=end_of_day(DAY + timedelta(days=15)),
        selection_cutoff=end_of_day(DAY + timedelta(days=32)),
        evaluation_cutoff=end_of_day(DAY + timedelta(days=48)),
    )
    rows = []
    for day in (fold.validation.start, fold.validation.end):
        for i in range(100):
            points = {
                name + ":" + target: 0.0 if target == "median" else 2.0
                for name in BASELINES
                for target in ("median", "mean")
            }
            points.update(hgb_median=0.0, hgb_mean=2.0, rf_mean=2.0)
            rows.append(
                Observation(
                    key=day.isoformat() + str(i),
                    fold=fold.name,
                    role="validation",
                    origin=end_of_day(day).isoformat(),
                    volume="low",
                    category="intermittent",
                    channel="store",
                    horizon=1,
                    reasons=(),
                    actual=10 if i % 5 == 0 else 0,
                    available=end_of_day(day + timedelta(days=2)).isoformat(),
                    points=points,
                    bands={name: (0.0, 10.0) for name in (*BASELINES, "hgb")},
                )
            )
    return fold, rows


def test_selection_uses_only_mature_validation_and_keeps_both_targets():
    fold, rows = recipe_fixture()
    recipe = fit_recipe(rows, fold, FunctionalPolicy())
    candidate, baseline, cells = predict_pair(rows[0], recipe)
    assert candidate.median == baseline.median == 0
    assert candidate.mean == baseline.mean == 2
    assert candidate.interval.lower == 0 and candidate.interval.upper == 10
    assert recipe["calibration"][cells["selected"]]["rows"] == 100
    for mutation in (
        {"role": "development_holdout"},
        {"available": (fold.selection_cutoff + timedelta(days=1)).isoformat()},
        {"reasons": ("unknown",)},
    ):
        with pytest.raises(SnapshotError, match="only_available_validation"):
            fit_recipe([replace(rows[0], **mutation), *rows[1:]], fold, FunctionalPolicy())
    # Future holdout outcomes cannot influence a previously fitted recipe.
    held = replace(rows[0], actual=99999, role="development_holdout")
    assert predict_pair(held, recipe) == predict_pair(rows[0], recipe)


def test_missing_calibration_is_not_filled_with_zero_or_hidden():
    fold, rows = recipe_fixture()
    sparse = rows[:14] + rows[100:114]
    recipe = fit_recipe(sparse, fold, FunctionalPolicy())
    candidate, _, _ = predict_pair(sparse[0], recipe)
    assert candidate.interval is None
    assert recipe["calibration"] == {}


@pytest.mark.parametrize("artifacts", [36], indirect=True)
def test_two_fold_campaign_replays_without_refitting_and_retains_empty_segments(
    artifacts, tmp_path, monkeypatch
):
    features, _, curated, calendar = artifacts
    plan = plan_backtest(
        calendar.descriptor.origin_window,
        BacktestPolicy(
            initial_train_days=1,
            validation_days=2,
            development_holdout_days=1,
            step_days=2,
            folds=2,
        ),
    )
    split = build_split(features, curated, tmp_path / "new-splits", plan)
    policy = FunctionalPolicy(model=small_policy())
    freeze = {
        "code": functional_code(),
        "model_environment": model_code().model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "feature_set_id": load_feature_set(features).feature_set_id,
        "split_id": load_split(split).split_id,
        "fixture_only": True,
    }
    freeze["freeze_id"] = "functional-freeze-sha256-" + canonical_sha256(freeze)
    with pytest.raises(SnapshotError, match="freeze_code_policy_or_parent"):
        build_campaign(
            features, split, tmp_path / "invalid", policy, freeze | {"feature_set_id": "tampered"}
        )
    result = build_campaign(features, split, tmp_path / "campaign", policy, freeze)
    manifest = load_campaign(result)
    with pytest.raises(SnapshotError, match="test_already_exposed"):
        build_campaign(features, split, tmp_path / "campaign", policy, freeze)
    assert manifest["descriptor"]["gates"]["status"] == "not_ready"
    assert manifest["descriptor"]["prediction_rows"] == 2 * 3 * 14
    segments = json.loads((result / "segments.json").read_text())
    assert any(
        s["dimension"] == "volume"
        and s["value"] == "zero"
        and s["eligible_rows"] == 0
        and s["status"] == "not_ready"
        for s in segments
    )
    from retailops_ai.forecasting import functional_campaign

    def forbidden(*args, **kwargs):
        pytest.fail("replay must not refit")

    monkeypatch.setattr(functional_campaign, "fit_worker", forbidden)
    assert verify_campaign(result, features, split)["campaign_id"] == manifest["campaign_id"]
    from types import SimpleNamespace

    from retailops_ai.forecasting import functional_run

    source = tmp_path / "source"
    source.mkdir()
    (source / "retained-snapshot.json").write_text('{"fixture": true}')
    parent = manifest["descriptor"]["parent"]
    monkeypatch.setattr(functional_run, "verify_campaign", lambda *args: manifest)
    monkeypatch.setattr(
        functional_run,
        "verify_import",
        lambda *args: SimpleNamespace(
            source_id=parent["source_dataset_id"], snapshot_id=parent["snapshot_id"]
        ),
    )
    monkeypatch.setattr(
        functional_run,
        "verify_curated",
        lambda *args: {"curated_dataset_id": parent["curated_dataset_id"]},
    )
    monkeypatch.setattr(
        functional_run.shutil, "disk_usage", lambda *args: SimpleNamespace(free=100 * 1024**3)
    )
    exported = functional_run.export_run(
        source, curated, features, split, result, tmp_path / "exported", "a" * 40
    )
    packed = functional_run.load_run(exported)
    assert packed["descriptor"]["quality_status"] == "not_ready"
    assert packed["descriptor"]["replay_status"] == "passed"
    assert (
        json.loads((exported / "handoff.json").read_text())[
            "legacy_single_output_importer_compatible"
        ]
        is False
    )
    assert (exported / "source/retained-snapshot.json").read_bytes() == (
        source / "retained-snapshot.json"
    ).read_bytes()
    (exported / "handoff.json").write_text("{}")
    with pytest.raises(SnapshotError, match="run_file_checksum"):
        functional_run.load_run(exported)
    with (result / "predictions.jsonl").open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(SnapshotError, match="file_checksum"):
        load_campaign(result)
