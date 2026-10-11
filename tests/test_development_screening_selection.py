"""Real native accumulators on controlled rows; no authenticated Project selection."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_campaign_tune_data import diagnostic, tune_plan, values

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.development_screening_selection import (
    ScreeningFamilyRanking,
    rank_screening_families,
)
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError

IDS = ("trial-z-first", "trial-a-second")


def forecasts(rf, hgb, tf, *, perfect_baseline=False):
    result = list(values(rf_mean=rf))
    for i, value in ((4, hgb), (5, tf)):
        result[i] = result[i].model_copy(update={"mean": value})
    if perfect_baseline:
        for i in range(3):
            result[i] = result[i].model_copy(update={"mean": 10.0})
    return tuple(result)


def control(*, first=None, second=None, **kwargs):
    a, sa = diagnostic(IDS[0], forecasts=first or forecasts(10.0, 10.8, 10.3), **kwargs)
    b, sb = diagnostic(IDS[1], forecasts=second or forecasts(10.1, 10.2, 10.9), **kwargs)
    return [a, b], {IDS[0]: sa, IDS[1]: sb}, tune_plan(IDS)


def test_each_family_keeps_its_original_winner_and_both_trials():
    trials, scores, plan = control()
    result = rank_screening_families(trials, scores, plan)
    assert result.status == "three_families_ranked"
    assert [result.winners[f].score_operation_id for f in ("rf", "hgb", "tensorflow")] == [
        IDS[0],
        IDS[1],
        IDS[0],
    ]
    assert len(result.trials) == 6 and all(t.eligible for t in result.trials)
    for family, winner in result.winners.items():
        score = scores[winner.score_operation_id]
        assert winner.fit_operation_id == score.plan.fit_operation_ids[family]
        assert winner.score_receipt_sha256 == score.content_sha256()
        assert winner.model_artifact_sha256 == score.model_artifact_sha256[family]
    assert not any((result.advancement_authorized, result.promotion_allowed, result.stage_ready))


def test_family_search_retains_losing_challengers_without_promoting_them():
    f = forecasts(10.5, 10.5, 10.5, perfect_baseline=True)
    result = rank_screening_families(*control(first=f, second=f))
    assert result.global_tune_diagnostics.mean.model == "history7"
    assert result.status == "three_families_ranked"
    assert all(w.frozen_trial_index == 0 for w in result.winners.values())
    assert not result.quality_qualified and not result.advancement_authorized


def test_bias_failure_is_retained_and_the_valid_original_recipe_wins():
    result = rank_screening_families(*control(first=forecasts(12.0, 10.2, 10.3)))
    assert result.winners["rf"].score_operation_id == IDS[1]
    first = result.trials[0]
    assert not first.eligible and first.mean_squared_error == pytest.approx(4.0)
    assert first.reasons == ("normalized_mean_bias_above_limit",)


def test_no_valid_family_stops_advancement_without_hiding_other_rankings():
    result = rank_screening_families(
        *control(first=forecasts(12.0, 10.2, 10.3), second=forecasts(13.0, 10.5, 10.1))
    )
    assert result.status == "not_ready" and result.winners["rf"] is None
    assert result.reasons == ("no_valid_rf_trial",)
    assert result.winners["hgb"] is not None and result.winners["tensorflow"] is not None
    assert len(result.trials) == 6


def test_missing_candidate_predictions_cannot_win_with_a_partial_mean():
    result = rank_screening_families(*control(first=forecasts(None, 10.2, 10.3)))
    assert result.winners["rf"].score_operation_id == IDS[1]
    assert result.trials[0].mean_squared_error is None
    assert result.trials[0].reasons == ("incomplete_mean_predictions",)


@pytest.mark.parametrize("count,censored", [(0, 100), (99, 0), (100, 26)])
def test_original_native_population_gates_block_all_families(count, censored):
    result = rank_screening_families(*control(count=count, censored=censored))
    assert result.status == "not_ready"
    assert all(w is None for w in result.winners.values())
    assert result.rows == count + censored and result.eligible_rows == count
    assert len(result.trials) == 6 and not any(t.eligible for t in result.trials)


def test_zero_demand_keeps_undefined_ratios_and_frozen_order():
    zero = tuple(
        FunctionalForecast(
            mean=0.0,
            median=None if i == 3 else 0.0,
            interval=CentralInterval(lower=0.0, upper=0.0) if i < 3 else None,
        )
        for i in range(6)
    )
    result = rank_screening_families(*control(first=zero, second=zero, actual=0))
    assert result.status == "three_families_ranked"
    assert all(t.normalized_mean_bias is None for t in result.trials)
    assert all(w.frozen_trial_index == 0 for w in result.winners.values())
    assert not result.quality_qualified


@pytest.mark.parametrize("change", ["role", "source", "dataset", "runtime", "export", "fit"])
def test_changed_parent_or_duplicate_trial_is_rejected(change):
    trials, scores, plan = control()
    second = scores[IDS[1]]
    if change in ("role", "source"):
        key, value = (
            ("role", "calibration") if change == "role" else ("source_recipe_sha256", "f" * 64)
        )
        second = second.model_copy(update={"plan": second.plan.model_copy(update={key: value})})
    elif change == "fit":
        second = second.model_copy(
            update={
                "plan": second.plan.model_copy(
                    update={"fit_operation_ids": scores[IDS[0]].plan.fit_operation_ids}
                )
            }
        )
    else:
        field = {
            "dataset": "dataset_id",
            "runtime": "runtime_code_sha256",
            "export": "export_receipt_sha256",
        }[change]
        second = second.model_copy(
            update={
                field: (
                    "ai09-physical-forecast-sha256-" + "f" * 64 if change == "dataset" else "f" * 64
                )
            }
        )
    scores[IDS[1]] = second
    with pytest.raises(SnapshotError):
        rank_screening_families(trials, scores, plan)


@pytest.mark.parametrize(
    "change", ["order", "population", "baseline", "horizon", "nonfinite", "subset"]
)
def test_all_original_keys_trials_and_horizons_are_required(change):
    trials, scores, plan = control()
    if change == "order":
        trials.reverse()
    elif change == "population":
        trials[1]["eligible_keys_sha256"] = "f" * 64
    elif change == "baseline":
        trials[1]["baseline_predictions_sha256"] = "f" * 64
    elif change == "horizon":
        trials[1]["metrics"]["segments"].pop()
    elif change == "nonfinite":
        trials[1]["metrics"]["segments"][0]["models"]["rf_mean"]["mean"]["mse"] = float("nan")
    else:
        trials.pop()
    with pytest.raises(SnapshotError):
        rank_screening_families(trials, scores, plan)


def test_resealed_rank_cannot_substitute_another_original_winner_or_drop_rejections():
    result = rank_screening_families(*control())
    value = result.model_dump(mode="json")
    changed = deepcopy(value)
    changed["winners"]["rf"] = changed["trials"][1]
    with pytest.raises(ValidationError, match="frozen_ranking"):
        ScreeningFamilyRanking.model_validate_json(canonical_bytes(changed))
    changed = deepcopy(value)
    changed["trials"].pop()
    with pytest.raises(ValidationError):
        ScreeningFamilyRanking.model_validate_json(canonical_bytes(changed))
