"""Additive development recipes preserve exact references and honest diagnostics."""

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecasting.functional_contract import BASELINES
from retailops_ai.forecasting.functional_recipe import Observation, predict_pair
from retailops_ai.forecasting.functional_v12_recipe import (
    CohortObservation,
    FunctionalV12Policy,
    PreparedV12Predictor,
    bind_pooled_mean,
    diagnose_recipe_v12,
    fit_recipe_v12,
    merge_mean_calibration,
    predict_pair_v12,
)
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.source_snapshot.files import SnapshotError


def fixture(volume="low"):
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
        for i in range(120):
            origin = end_of_day(day).isoformat()
            target = (day + timedelta(days=1)).isoformat()
            points = {
                name + ":" + functional: 0.0 if volume == "zero" else 2.0
                for name in BASELINES
                for functional in ("median", "mean")
            }
            points.update(hgb_mean=2.0, rf_mean=2.0, hgb_median=0.0)
            rows.append(
                Observation(
                    key=canonical_bytes(
                        [fold.name, "validation", origin, str(i), "location", "store", target]
                    ).decode(),
                    fold=fold.name,
                    role="validation",
                    origin=origin,
                    volume=volume,
                    category="one",
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


def test_exact_reference_median_and_band_survive_a_different_learned_median():
    fold, rows = fixture()
    recipe = fit_recipe_v12(rows, fold)
    assert recipe["reference_recipe"]["groups"]["low:one"]["median"]["selected"]["method"] == (
        "hgb_median"
    )
    changed = replace(rows[0], points=rows[0].points | {"hgb_median": 99.0}, actual=12345)
    learned, reference, _ = predict_pair(changed, recipe["reference_recipe"])
    candidate, baseline, meta = predict_pair_v12(changed, recipe)
    assert learned.interval.upper == 99.0
    assert candidate.median == baseline.median == reference.median == 2.0
    assert candidate.interval == baseline.interval == reference.interval
    assert candidate.interval.upper == 10.0
    assert candidate.interval.lower <= candidate.median <= candidate.interval.upper
    assert meta["selected"] == meta["baseline"]


def test_zero_history_gets_a_pooled_mean_without_moving_median_or_interval():
    fold, rows = fixture("zero")
    rows = [
        replace(row, category="sparse" if i % 3 == 0 else "other") for i, row in enumerate(rows)
    ]
    recipe = fit_recipe_v12(rows, fold, FunctionalV12Policy(prior_strength=200))
    for row in rows[:10]:
        candidate, baseline, meta = predict_pair_v12(row, recipe)
        assert candidate.mean == 2.0
        assert baseline.mean == candidate.median == baseline.median == 0.0
        assert candidate.interval == baseline.interval
        assert meta["mean_source"] == "zero_pool"
    assert recipe["offsets"]["global"] is None
    assert recipe["offsets"]["zero"]["parent_offset"] is None
    assert recipe["offsets"]["zero"]["statistics"]["unique_positive_targets"] == 48


def test_zero_pool_does_not_inherit_large_nonzero_residuals():
    fold, zero = fixture("zero")
    _, nonzero = fixture("low")
    nonzero = [replace(row, actual=cast_actual(row.actual) + 1000) for row in nonzero]
    wrapped = [CohortObservation(row, "sparse") for row in zero]
    wrapped += [CohortObservation(row, "large") for row in nonzero]
    recipe = fit_recipe_v12(wrapped, fold)
    assert recipe["offsets"]["global"]["offset"] == 1000
    assert predict_pair_v12(zero[0], recipe)[0].mean == 2


def cast_actual(value):
    assert value is not None
    return value


def test_duplicate_horizons_do_not_increase_shrinkage_support():
    fold, rows = fixture()
    original = fit_recipe_v12(rows, fold)
    aliases = []
    for row in rows[:120]:
        parts = json.loads(row.key)
        parts[-1] = (fold.validation.end + timedelta(days=1)).isoformat()
        aliases.append(replace(row, key=canonical_bytes(parts).decode(), horizon=2))
    duplicated = fit_recipe_v12([*rows, *aliases], fold)
    first = original["offsets"]["volumes"]["low"]["categories"]["one"]
    second = duplicated["offsets"]["volumes"]["low"]["categories"]["one"]
    assert first["statistics"]["rows"] == 240
    assert second["statistics"]["rows"] == 360
    assert first["statistics"]["unique_targets"] == second["statistics"]["unique_targets"] == 240
    assert first["local_weight"] == second["local_weight"]
    assert original["support"]["unique_targets"] == duplicated["support"]["unique_targets"]
    bad = replace(aliases[0], actual=999)
    with pytest.raises(SnapshotError, match="inconsistent_unique_target_actual"):
        fit_recipe_v12([*rows, bad], fold)


def test_explicit_cohorts_can_reuse_source_keys_without_colliding():
    fold, rows = fixture("zero")
    other = [replace(row, actual=cast_actual(row.actual) * 2) for row in rows]
    wrapped = [CohortObservation(row, "a") for row in rows]
    wrapped += [CohortObservation(row, "b") for row in other]
    recipe = fit_recipe_v12(wrapped, fold)
    assert recipe["support"]["rows"] == recipe["support"]["unique_targets"] == 480
    assert recipe["support"]["cohort_ids"] == ["a", "b"]
    assert predict_pair_v12(wrapped[0], recipe)[0].mean == 3
    with pytest.raises(SnapshotError, match="duplicate_validation_keys"):
        fit_recipe_v12([*wrapped, wrapped[0]], fold)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"role": "development_holdout"}, "requires_eligible_validation"),
        ({"reasons": ("censored",)}, "requires_eligible_validation"),
        ({"actual": None}, "requires_eligible_validation"),
        ({"available": "2099-01-01T00:00:00+00:00"}, "not_available_at_cutoff"),
        ({"available": "2020-01-01T00:00:00"}, "not_available_at_cutoff"),
        ({"key": "unbound"}, "target_or_cohort_binding"),
    ],
)
def test_calibration_rejects_unavailable_labels_and_unbound_targets(mutation, reason):
    fold, rows = fixture()
    with pytest.raises(SnapshotError, match=reason):
        fit_recipe_v12([replace(rows[0], **mutation), *rows[1:]], fold)


def test_missing_zero_calibration_is_local_and_not_silently_zero_filled():
    fold, rows = fixture()
    recipe = fit_recipe_v12(rows, fold)
    assert recipe["calibration_gaps"] == ["zero_pool_unavailable"]
    candidate, _, _ = predict_pair_v12(rows[0], recipe)
    assert candidate.mean == 2
    unseen = replace(rows[0], volume="zero")
    candidate, baseline, meta = predict_pair_v12(unseen, recipe)
    assert candidate.mean is None and baseline.mean == 2
    assert meta["mean_source"] == "zero_pool_unavailable"
    report = diagnose_recipe_v12([unseen], recipe)
    assert "mean_predictions_incomplete_or_empty" in report["not_ready_reasons"]


def test_diagnostics_preserve_real_mse_failure_even_with_perfect_mean_bias():
    fold, rows = fixture()
    rows = [replace(r, points=r.points | {"hgb_mean": 0.0 if r.actual else 2.5}) for r in rows]
    recipe = fit_recipe_v12(
        rows, fold, FunctionalV12Policy(mean_variant="hgb_blend", hgb_weight=1.0)
    )
    report = diagnose_recipe_v12(rows, recipe)
    global_result = next(s for s in report["segments"] if s["dimension"] == "global")
    assert global_result["candidate"]["mean"]["normalized_bias"] == 0
    assert "mean_mse_regression" in global_result["failed_reasons"]
    assert "mean_mse_regression" in report["failed_reasons"]
    assert report["status"] == "not_ready"
    assert "not_independent_qualification" in report["evaluation_use"]


def test_all_zero_actuals_still_fail_a_positive_calibrated_mean():
    fold, rows = fixture("zero")
    recipe = fit_recipe_v12(rows, fold)
    report = diagnose_recipe_v12([replace(r, actual=0) for r in rows], recipe)
    assert "positive_mean_forecast_on_all_zero_actuals" in report["failed_reasons"]
    assert "mean_mse_regression" in report["failed_reasons"]


def test_external_variant_and_prediction_do_not_select_from_future_outcomes():
    fold, rows = fixture("zero")
    retained = fit_recipe_v12(rows, fold, FunctionalV12Policy(mean_variant="baseline"))
    additive = fit_recipe_v12(rows, fold, FunctionalV12Policy(mean_variant="additive"))
    assert retained["recipe_id"] != additive["recipe_id"]
    assert predict_pair_v12(rows[0], retained)[0].mean == 0
    assert (
        "absolute_normalized_mean_bias_exceeded"
        in diagnose_recipe_v12(rows, retained)["failed_reasons"]
    )
    future = replace(rows[0], role="development_holdout", actual=9999, available="2099-01-01")
    assert predict_pair_v12(future, additive) == predict_pair_v12(rows[0], additive)
    with pytest.raises(SnapshotError, match="diagnostics_validation_only"):
        diagnose_recipe_v12([future], additive)


def test_recipe_identity_is_independent_of_input_order():
    fold, rows = fixture()
    assert fit_recipe_v12(rows, fold) == fit_recipe_v12(list(reversed(rows)), fold)


def train_fixture(fold, validation):
    training = []
    for row in validation[:120]:
        parts = json.loads(row.key)
        parts[1] = "train"
        parts[2] = end_of_day(fold.train.start).isoformat()
        parts[-1] = (fold.train.start + timedelta(days=1)).isoformat()
        training.append(
            replace(
                row,
                key=canonical_bytes(parts).decode(),
                role="train",
                origin=parts[2],
                available=end_of_day(fold.train.start + timedelta(days=2)).isoformat(),
                actual=cast_actual(row.actual) * 2,
                points={name: value for name, value in row.points.items() if ":" in name},
            )
        )
    return training


def test_optional_train_zero_pool_uses_exposures_and_needs_no_model_heads():
    fold, rows = fixture("zero")
    training = train_fixture(fold, rows)
    validation_only = fit_recipe_v12(rows, fold, training_rows=training)
    pooled = fit_recipe_v12(
        rows,
        fold,
        FunctionalV12Policy(zero_estimation="train_validation_pooled"),
        training_rows=training,
    )
    assert predict_pair_v12(rows[0], validation_only)[0].mean == 2
    assert predict_pair_v12(rows[0], pooled)[0].mean == pytest.approx(8 / 3)
    assert pooled["offsets"]["zero"]["statistics"]["rows"] == 360
    assert pooled["offsets"]["zero"]["statistics"]["unique_targets"] == 360
    assert pooled["zero_role_support"]["train"]["rows"] == 120
    assert pooled["zero_role_support"]["validation"]["rows"] == 240
    assert pooled["training_support"]["used_for_zero"] is True
    assert validation_only["zero_role_support"]["train"] is None
    assert pooled["zero_role_support"]["train"]["latest_label_available_at"] <= (
        fold.training_cutoff.isoformat()
    )


def test_training_labels_use_training_cutoff_not_later_selection_cutoff():
    fold, rows = fixture("zero")
    training = train_fixture(fold, rows)
    training[0] = replace(training[0], available=fold.selection_cutoff.isoformat())
    with pytest.raises(SnapshotError, match="label_not_available_at_cutoff"):
        fit_recipe_v12(rows, fold, training_rows=training)
    with pytest.raises(SnapshotError, match="requires_eligible_train"):
        fit_recipe_v12(rows, fold, training_rows=rows)


def test_invalid_zero_training_history_is_not_used_as_a_positive_prior():
    fold, rows = fixture("zero")
    training = train_fixture(fold, rows)
    training[0] = replace(training[0], points=training[0].points | {"history28:mean": 1.0})
    with pytest.raises(SnapshotError, match="zero_training_history_not_zero"):
        fit_recipe_v12(
            rows,
            fold,
            FunctionalV12Policy(zero_estimation="train_validation_pooled"),
            training_rows=training,
        )


def test_cohort_statistics_merge_keeps_local_references_and_pooled_zero_mean():
    fold, rows = fixture("zero")
    left = fit_recipe_v12([CohortObservation(row, "a") for row in rows], fold)
    right = fit_recipe_v12(
        [CohortObservation(replace(row, actual=cast_actual(row.actual) * 2), "b") for row in rows],
        fold,
    )
    pooled = merge_mean_calibration([left, right])
    assert pooled == merge_mean_calibration([right, left])
    assert pooled["mean_sufficient_statistics"]["zero"]["unique_targets"] == 480
    assert pooled["mean_sufficient_statistics"]["zero"]["unique_positive_targets"] == 96
    for local in (left, right):
        bound = bind_pooled_mean(local, pooled)
        assert bound["reference_recipe"] == local["reference_recipe"]
        selected, baseline, _ = PreparedV12Predictor(bound).predict(rows[0])
        assert selected.mean == 3
        assert selected.interval == baseline.interval
        assert selected.median == baseline.median
    with pytest.raises(SnapshotError, match="overlap_or_repeated_merge"):
        merge_mean_calibration([left, left])
    with pytest.raises(SnapshotError, match="overlap_or_repeated_merge"):
        merge_mean_calibration([bind_pooled_mean(left, pooled)])


def test_statistics_merge_rejects_policy_mismatch_and_tampered_receipts():
    fold, rows = fixture()
    left = fit_recipe_v12([CohortObservation(row, "a") for row in rows], fold)
    right = fit_recipe_v12(
        [CohortObservation(row, "b") for row in rows],
        fold,
        FunctionalV12Policy(prior_strength=200),
    )
    with pytest.raises(SnapshotError, match="incompatible_mean_calibration_merge"):
        merge_mean_calibration([left, right])
    with pytest.raises(SnapshotError, match="recipe_identity_mismatch"):
        PreparedV12Predictor(left | {"selection_cutoff": "2099-01-01"})
    calibration = merge_mean_calibration([left])
    with pytest.raises(SnapshotError, match="mean_calibration_identity_mismatch"):
        bind_pooled_mean(left, calibration | {"selection_cutoff": "2099-01-01"})
    with pytest.raises(SnapshotError, match="mean_calibration_binding"):
        bind_pooled_mean(right, calibration)


def test_merging_nonzero_statistics_preserves_total_exposure_and_shrinkage():
    fold, rows = fixture()
    left = fit_recipe_v12([CohortObservation(row, "a") for row in rows], fold)
    right = fit_recipe_v12(
        [CohortObservation(replace(row, actual=cast_actual(row.actual) + 1), "b") for row in rows],
        fold,
    )
    pooled = merge_mean_calibration([left, right])
    stats = pooled["mean_sufficient_statistics"]["global"]
    assert stats["rows"] == stats["unique_targets"] == 480
    assert stats["mean_residual"] == 0.5
    assert pooled["offsets"]["volumes"]["low"]["local_weight"] == pytest.approx(480 / 530)
    assert predict_pair_v12(rows[0], bind_pooled_mean(left, pooled))[0].mean == 2.5


def test_zero_only_changes_no_nonzero_mean_and_reports_its_remaining_bias():
    fold, zero = fixture("zero")
    _, nonzero = fixture()
    nonzero = [replace(r, actual=cast_actual(r.actual) + 10) for r in nonzero]
    rows = [CohortObservation(r, "zero") for r in zero]
    rows += [CohortObservation(r, "other") for r in nonzero]
    recipe = fit_recipe_v12(rows, fold, FunctionalV12Policy(mean_variant="zero_only"))
    assert predict_pair_v12(zero[0], recipe)[0].mean == 2
    candidate, baseline, metadata = predict_pair_v12(nonzero[0], recipe)
    assert candidate.mean == baseline.mean == 2
    assert metadata["mean_source"] == "exact_baseline"
    assert (
        "absolute_normalized_mean_bias_exceeded"
        in diagnose_recipe_v12(rows, recipe)["failed_reasons"]
    )


def test_prepared_predictor_cannot_be_changed_by_mutating_the_external_receipt():
    fold, rows = fixture("zero")
    recipe = fit_recipe_v12(rows, fold)
    predictor = PreparedV12Predictor(recipe)
    recipe["offsets"]["zero"]["offset"] = 999
    assert predictor.predict(rows[0])[0].mean == 2
    with pytest.raises(SnapshotError, match="recipe_identity_mismatch"):
        predict_pair_v12(rows[0], recipe)
