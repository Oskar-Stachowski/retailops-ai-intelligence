"""Actual native score/metric replay on exposed controls, never Project qualification."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import point, scope
from test_anomaly_portfolio_model import saved_model as saved_model

from retailops_ai.anomaly_detectors.protocol import Window, series_key
from retailops_ai.anomaly_evaluation.contract import Episode, Truth, TruthWindow
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_evaluation as component
from retailops_ai.evaluation_campaign.campaign_anomaly_scoring import iter_anomaly_census_scores

SOURCE_ID = "source-sha256-" + "b" * 64
SCENARIO_SHA = "c" * 64
WINDOW = Window(start=date(2026, 8, 12), end=date(2026, 8, 15))
AS_OF = datetime(2026, 8, 20, tzinfo=UTC)


def labels(*, scopes=None, episodes=True, available=AS_OF):
    scopes = scopes or (scope(),)
    return Truth(
        source_dataset_id=SOURCE_ID,
        source_scenario_sha256=SCENARIO_SHA,
        complete_windows=tuple(
            TruthWindow(**s.model_dump(), window=WINDOW, available_at=available) for s in scopes
        ),
        episodes=(
            Episode(
                **scopes[0].model_dump(),
                episode_id="exposed-spike",
                business_type="one_day_spike",
                window=Window(start=date(2026, 8, 13), end=date(2026, 8, 13)),
                first_evidence_available_at=datetime(2026, 8, 15, tzinfo=UTC),
                label_available_at=available,
            ),
        )
        if episodes
        else (),
    )


def plan(model, truth, family="seasonal_residual", *, scopes=None, window=WINDOW, as_of=AS_OF):
    return component.CampaignAnomalyCensusPlan(
        phase="development",
        source_dataset_id=SOURCE_ID,
        source_recipe_sha256="1" * 64,
        source_scenario_sha256=SCENARIO_SHA,
        feature_parent_sha256="2" * 64,
        model_sha256=canonical_sha256(model.model_dump(mode="json")),
        truth_sha256=canonical_sha256(truth.model_dump(mode="json")),
        scopes=scopes or (scope(),),
        window=window,
        as_of=as_of,
        family=family,
        max_rows=component.MAX_NATIVE_EVALUATION_ROWS,
    )


def rows(model, configuration, *, units=90):
    points = [
        point(configuration.window.start + timedelta(days=i), units)
        for i in range((configuration.window.end - configuration.window.start).days + 1)
    ]
    return list(
        iter_anomaly_census_scores(
            model,
            iter(points),
            configuration.scopes,
            configuration.window,
            configuration.family,
            configuration.native_role,
            configuration.as_of,
        )
    )


@pytest.mark.parametrize("family", ["seasonal_residual", "isolation_forest"])
def test_genuine_saved_model_matches_native_observation_and_episode_evaluation(saved_model, family):
    truth = labels()
    configuration = plan(saved_model, truth, family)
    scored = rows(saved_model, configuration)
    result = component.evaluate_anomaly_census(saved_model, iter(scored), truth, configuration)
    assert result.native_evaluation == evaluate(
        [r.decision for r in scored],
        truth,
        WINDOW,
        AS_OF,
        source_dataset_id=SOURCE_ID,
    )
    assert result.rows == 4 and result.replay_batches == 1
    assert not result.parent_source_verified and not result.quality_qualified
    assert not result.critical_segment_inventory_complete and not result.block_uncertainty_complete
    assert not result.stage_ready and not result.promotion_allowed
    component.verify_anomaly_census_evaluation(
        result, model=saved_model, scored=iter(scored), truth=truth, plan=configuration
    )
    if family == "seasonal_residual":
        observation = result.native_evaluation["descriptor"]["observation"]
        episode = result.native_evaluation["descriptor"]["episode"]
        assert observation["precision"]["value"] == 0.25
        assert observation["false_alerts_per_1000"]["value"] == 1000
        assert episode["n_detected"] == 1 and episode["repeat_alert_count"] == 1
        assert episode["unmatched_alert_count"] == 2
        assert episode["delay_from_evidence_seconds"]["value"] == 0


def test_no_positive_truth_does_not_produce_perfect_quality(saved_model):
    truth = labels(episodes=False)
    configuration = plan(saved_model, truth)
    result = component.evaluate_anomaly_census(
        saved_model, rows(saved_model, configuration, units=10), truth, configuration
    )
    report = result.native_evaluation["descriptor"]
    for name in ("precision", "recall", "high_severity_precision", "average_precision"):
        assert report["observation"][name]["status"] == "not_evaluable"
        assert report["observation"][name]["value"] is None
    assert report["episode"]["recall"]["value"] is None


def test_missing_input_unknown_and_immature_truth_keep_distinct_native_denominators(saved_model):
    scopes = (scope(), scope(product=str(UUID(int=3))))
    truth = labels(scopes=scopes)
    value = truth.model_dump(mode="json")
    value["complete_windows"][1]["window"]["end"] = "2026-08-13"
    truth = Truth.model_validate_json(json.dumps(value))
    configuration = plan(saved_model, truth, scopes=scopes)
    scored = rows(saved_model, configuration)
    result = component.evaluate_anomaly_census(saved_model, scored, truth, configuration)
    coverage = result.native_evaluation["descriptor"]["coverage"]
    assert coverage["requested"] == 8 and coverage["evaluable"] == 4
    assert coverage["unknown_truth"] == 2 and coverage["insufficient_data"] == 2
    assert coverage["input_insufficient_data_total"] == 4
    immature = labels(available=AS_OF + timedelta(days=1))
    configuration = plan(saved_model, immature)
    result = component.evaluate_anomaly_census(
        saved_model, rows(saved_model, configuration), immature, configuration
    )
    assert result.native_evaluation["descriptor"]["coverage"]["immature_truth"] == 4
    assert result.native_evaluation["descriptor"]["episode"]["recall"]["value"] is None


@pytest.mark.parametrize(
    "attack", ["missing", "extra", "duplicate", "order", "role", "clock", "family", "point_hash"]
)
def test_complete_scoring_census_and_native_role_clock_are_required(saved_model, attack):
    truth = labels()
    configuration = plan(saved_model, truth)
    scored = rows(saved_model, configuration)
    if attack == "missing":
        scored.pop()
    elif attack == "extra":
        scored.append(scored[-1])
    elif attack == "duplicate":
        scored[1] = scored[0]
    elif attack == "order":
        scored.reverse()
    elif attack == "point_hash":
        scored[0] = replace(scored[0], public_point_sha256=None)
    else:
        changes = {
            "role": {"role": "final_test"},
            "clock": {"scoring_origin": scored[0].decision.scoring_origin + timedelta(hours=1)},
            "family": {"family": "isolation_forest"},
        }[attack]
        scored[0] = replace(scored[0], decision=scored[0].decision.model_copy(update=changes))
    with pytest.raises(ValueError, match="campaign_anomaly_evaluation_"):
        component.evaluate_anomaly_census(saved_model, iter(scored), truth, configuration)


def test_resealed_consistent_wrong_score_is_rejected_by_saved_model_replay(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth)
    scored = rows(saved_model, configuration)
    # Keep the decision internally consistent and above the unchanged threshold.
    wrong = scored[0].decision.model_copy(update={"score": scored[0].decision.score + 1})
    scored[0] = replace(scored[0], decision=wrong)
    with pytest.raises(ValueError, match="saved_model_prediction_mismatch"):
        component.evaluate_anomaly_census(saved_model, scored, truth, configuration)


@pytest.mark.parametrize(
    "attack", ["source", "truth_hash", "scenario", "model", "budget", "cutoff"]
)
def test_wrong_binding_or_population_budget_fails_before_reading_scores(saved_model, attack):
    truth = labels()
    configuration = plan(saved_model, truth)
    changes = {
        "source": {"source_dataset_id": "source-sha256-" + "0" * 64},
        "truth_hash": {"truth_sha256": "0" * 64},
        "scenario": {"source_scenario_sha256": "0" * 64},
        "model": {"model_sha256": "0" * 64},
        "budget": {"max_rows": 1},
        "cutoff": {"window": Window(start=date(2026, 8, 7), end=date(2026, 8, 10))},
    }[attack]
    configuration = configuration.model_copy(update=changes)

    def unread():
        pytest.fail("Invalid frozen binding consumed scoring input")
        yield

    with pytest.raises(ValueError, match="campaign_anomaly_evaluation_"):
        component.evaluate_anomaly_census(saved_model, unread(), truth, configuration)


def test_resealed_metrics_cannot_bypass_independent_native_evaluation(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth)
    scored = rows(saved_model, configuration)
    result = component.evaluate_anomaly_census(saved_model, scored, truth, configuration)
    native = deepcopy(result.native_evaluation)
    native["descriptor"]["episode"]["n_detected"] = 0
    changed = result.model_copy(
        update={
            "native_evaluation": native,
            "native_evaluation_sha256": canonical_sha256(native),
        }
    )
    with pytest.raises(ValueError, match="replay_mismatch"):
        component.verify_anomaly_census_evaluation(
            changed, model=saved_model, scored=scored, truth=truth, plan=configuration
        )
    with pytest.raises(ValidationError):
        component.CampaignAnomalyCensusEvaluation.model_validate_json(
            result.model_copy(update={"quality_qualified": True}).model_dump_json()
        )


def test_resealed_result_plan_cannot_replace_independently_frozen_parent_binding(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth)
    scored = rows(saved_model, configuration)
    result = component.evaluate_anomaly_census(saved_model, scored, truth, configuration)
    changed = result.model_copy(
        update={"plan": configuration.model_copy(update={"feature_parent_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="replay_mismatch"):
        component.verify_anomaly_census_evaluation(
            changed, model=saved_model, scored=scored, truth=truth, plan=configuration
        )


def test_numerical_row_change_is_rejected_even_if_public_point_hash_is_retained(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth)
    scored = rows(saved_model, configuration)
    changed = scored[0].model_row.model_copy(update={"observed_units": 91})
    scored[0] = replace(scored[0], model_row=changed)
    with pytest.raises(ValueError, match="scoring_input_binding"):
        component.evaluate_anomaly_census(saved_model, scored, truth, configuration)


def test_exposed_control_final_mapping_uses_native_final_role_without_admission(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth).model_copy(update={"phase": "final"})
    scored = rows(saved_model, configuration)
    assert all(r.decision.role == "final_test" for r in scored)
    result = component.evaluate_anomaly_census(saved_model, scored, truth, configuration)
    assert result.plan.phase == "final" and not result.quality_qualified
    assert not result.parent_source_verified and not result.stage_ready


def test_later_scoring_parent_failure_never_returns_partial_evaluation(saved_model):
    truth = labels()
    configuration = plan(saved_model, truth)

    def interrupted():
        yield rows(saved_model, configuration)[0]
        raise OSError("declared-later-parent-failure")

    with pytest.raises(OSError, match="later-parent-failure"):
        component.evaluate_anomaly_census(saved_model, interrupted(), truth, configuration)


def test_multiple_native_replay_batches_keep_every_declared_abstention(saved_model):
    scopes = tuple(
        sorted((scope(product=str(UUID(int=i))) for i in range(1, 1302)), key=series_key)
    )
    window = Window(start=date(2026, 8, 12), end=date(2026, 8, 19))
    as_of = datetime(2026, 8, 26, tzinfo=UTC)
    truth = Truth(
        source_dataset_id=SOURCE_ID,
        source_scenario_sha256=SCENARIO_SHA,
        complete_windows=tuple(
            TruthWindow(**s.model_dump(), window=window, available_at=as_of) for s in scopes
        ),
        episodes=(),
    )
    configuration = plan(saved_model, truth, scopes=scopes, window=window, as_of=as_of)
    scored = iter_anomaly_census_scores(
        saved_model, iter(()), scopes, window, "seasonal_residual", "batch", as_of
    )
    result = component.evaluate_anomaly_census(saved_model, scored, truth, configuration)
    assert result.rows == 10408 and result.replay_batches == 2
    coverage = result.native_evaluation["descriptor"]["coverage"]
    assert coverage["requested"] == coverage["insufficient_data"] == 10408
    assert coverage["unknown_truth"] == coverage["evaluable"] == 0
    assert result.native_evaluation["descriptor"]["observation"]["precision"]["value"] is None
