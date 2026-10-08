"""Typed metadata controls, not completed project fits, calibration or final evidence."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_campaign_calibration_data import calibration_plan
from test_campaign_score_worker import fake_fits
from test_campaign_tune_data import score_receipt, tune_plan

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibration,
    CampaignForecastCalibrationReceipt,
    CampaignForecastHorizonCalibration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_configuration import (
    bind_forecast_configuration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastFrozenConfiguration,
    CampaignForecastQualityPolicy,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_functionals import FrozenForecastComposer
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastChoice,
    CampaignForecastTuneReceipt,
    CampaignForecastTuneSelection,
)
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def parents():
    policy = CampaignForecastQualityPolicy()
    tune_scores, cal_scores, fits = [], [], {}
    for i in range(2):
        prototype = score_receipt(f"tune-{i}", 1400, 1400, "4" * 64, "5" * 64, "6" * 64)
        fitted = {}
        for family, fit in fake_fits(prototype.dataset_id).items():
            files = {"controlled-model.json": canonical_sha256((i, family))}
            fitted[family] = fit.model_copy(
                update={
                    "operation_id": f"fit-{i}-{family}",
                    "plan": fit.plan.model_copy(
                        update={
                            "family": family,
                            "source_recipe_sha256": prototype.plan.source_recipe_sha256,
                            "export_operation_id": prototype.plan.export_operation_id,
                            "worker_environment_lock_sha256": prototype.plan.worker_environment_lock_sha256,
                        }
                    ),
                    "protocol_sha256": prototype.protocol_sha256,
                    "runtime_code_sha256": prototype.runtime_code_sha256,
                    "export_receipt_sha256": prototype.export_receipt_sha256,
                    "artifact_files": files,
                    "model_artifact_sha256": canonical_sha256(files),
                }
            )
            fits[fitted[family].operation_id] = fitted[family]
        score = prototype.model_copy(
            update={
                "plan": prototype.plan.model_copy(
                    update={
                        "fit_operation_ids": {f: fitted[f].operation_id for f in FAMILIES},
                    }
                ),
                "fit_receipt_sha256": {f: fitted[f].content_sha256() for f in FAMILIES},
                "model_artifact_sha256": {f: fitted[f].model_artifact_sha256 for f in FAMILIES},
            }
        )
        tune_scores.append(score)
        cal_scores.append(
            score.model_copy(
                update={
                    "operation_id": f"cal-{i}",
                    "plan": score.plan.model_copy(update={"role": "calibration"}),
                    "keys_sha256": "7" * 64,
                    "eligible_keys_sha256": "8" * 64,
                    "role_population_sha256": "9" * 64,
                }
            )
        )

    def choice(model, index, family):
        score = tune_scores[index]
        return CampaignForecastChoice(
            model=model,
            score_operation_id=score.operation_id,
            fit_operation_id=score.plan.fit_operation_ids[family],
            model_artifact_sha256=score.model_artifact_sha256[family],
        )

    mean, median = choice("tensorflow", 0, "tensorflow"), choice("hgb", 1, "hgb")
    selection = CampaignForecastTuneSelection(
        status="selected_for_independent_evaluation",
        rows=1400,
        eligible_rows=1400,
        baseline_mean="history7",
        baseline_median="history28",
        baseline_interval="weekday28",
        mean=mean,
        median=median,
        candidate_interval_center=median,
        reasons=(),
    )
    files = {n: "a" * 64 for n in ("plan.json", "parents.json", "metrics.json", "selection.json")}
    first = tune_scores[0]
    plan = tune_plan(
        tuple(s.operation_id for s in tune_scores),
        forecast_quality_policy_sha256=policy.content_sha256(),
    )
    tune = CampaignForecastTuneReceipt(
        protocol_sha256=first.protocol_sha256,
        operation_id="selected-tune",
        reservation_id="campaign-operation-" + "1" * 32,
        plan=plan,
        export_receipt_sha256=first.export_receipt_sha256,
        score_receipt_sha256={s.operation_id: s.content_sha256() for s in tune_scores},
        dataset_id=first.dataset_id,
        runtime_code_sha256=first.runtime_code_sha256,
        rows=1400,
        eligible_rows=1400,
        keys_sha256=first.keys_sha256,
        eligible_keys_sha256=first.eligible_keys_sha256,
        role_population_sha256=first.role_population_sha256,
        baseline_predictions_sha256="a" * 64,
        selection=selection,
        artifact_sha256=canonical_sha256(files),
        artifact_bytes=1,
        artifact_files=files,
        worker_evidence={"controlled_metadata_not_completed_project": True},
    )
    calibrated = CampaignForecastCalibration(
        status="fitted_for_independent_evaluation",
        selection=selection,
        center=median,
        calibration_score_operation_id="cal-1",
        rows=1400,
        eligible_rows=1400,
        horizons=tuple(
            CampaignForecastHorizonCalibration(
                horizon_days=h,
                rows=100,
                eligible_rows=100,
                quantile_rank=91,
                radius=2.0,
                reasons=(),
            )
            for h in range(1, 15)
        ),
        calibration_fitted=True,
    )
    files = {
        n: "b" * 64 for n in ("plan.json", "parents.json", "population.json", "calibration.json")
    }
    calibration = CampaignForecastCalibrationReceipt(
        protocol_sha256=first.protocol_sha256,
        operation_id="frozen-calibration",
        reservation_id="campaign-operation-" + "2" * 32,
        plan=calibration_plan(
            tuple(s.operation_id for s in cal_scores),
            tune_operation_id=tune.operation_id,
            forecast_quality_policy_sha256=policy.content_sha256(),
        ),
        export_receipt_sha256=first.export_receipt_sha256,
        tune_receipt_sha256=tune.content_sha256(),
        score_receipt_sha256={s.operation_id: s.content_sha256() for s in cal_scores},
        dataset_id=first.dataset_id,
        runtime_code_sha256=first.runtime_code_sha256,
        rows=1400,
        eligible_rows=1400,
        keys_sha256=cal_scores[0].keys_sha256,
        eligible_keys_sha256=cal_scores[0].eligible_keys_sha256,
        role_population_sha256=cal_scores[0].role_population_sha256,
        calibration=calibrated,
        artifact_sha256=canonical_sha256(files),
        artifact_bytes=1,
        artifact_files=files,
        worker_evidence={"controlled_metadata_not_completed_project": True},
    )
    return tune, calibration, tuple(tune_scores), tuple(cal_scores), fits


def configuration():
    return bind_forecast_configuration(
        *parents(), feature_schema_sha256="e" * 64, quality_policy=CampaignForecastQualityPolicy()
    )


def trial_rows(index=0, *, role="development_evaluation", eligible=True):
    day = date(2026, 1, 1) + timedelta(days=index)
    key = dict(
        product_id="product-a",
        selling_location_id="location-a",
        channel="store",
        forecast_origin=make_origin(day).forecast_origin,
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=day + timedelta(days=1),
        horizon_days=1,
    )
    baselines = (
        FunctionalForecast(mean=4.0, median=3.0, interval=CentralInterval(lower=0.0, upper=5.0)),
        FunctionalForecast(
            mean=42.0, median=40.0, interval=CentralInterval(lower=30.0, upper=50.0)
        ),
        FunctionalForecast(mean=2.0, median=2.0, interval=CentralInterval(lower=0.0, upper=5.0)),
    )
    return tuple(
        CampaignForecastTrialPrediction(
            **key,
            role=role,
            trial_tune_score_operation_id=f"tune-{i}",
            example_sha256="f" * 64,
            eligible=eligible,
            exclusion_reasons=() if eligible else ("closed_target",),
            values=(
                *baselines,
                FunctionalForecast(mean=7.0, median=None, interval=None),
                FunctionalForecast(mean=6.0, median=1.0 if i == 0 else 8.0, interval=None),
                FunctionalForecast(mean=30.0 if i == 0 else 100.0, median=10.0, interval=None),
            )
            if eligible
            else tuple(FunctionalForecast(mean=None, median=None, interval=None) for _ in range(6)),
        )
        for i in range(2)
    )


def test_separate_selected_heads_join_exact_trials_and_fixed_calibration_without_labels():
    frozen = configuration()
    row = FrozenForecastComposer(frozen).compose(trial_rows())
    assert row.candidate.mean == 30.0 and row.candidate.median == 8.0
    assert row.candidate.interval == CentralInterval(lower=6.0, upper=10.0)
    assert row.reference.mean == 4.0 and row.reference.median == 40.0
    assert row.reference.interval == CentralInterval(lower=0.0, upper=5.0)
    assert row.reference.interval_center == 2.0
    assert row.reference.median > row.reference.interval.upper
    assert not frozen.final_access_authorized_by_this_document
    assert not frozen.journal_completion_proved_by_this_document


def test_configuration_is_validated_and_hashed_once_for_a_stream(monkeypatch):
    frozen = configuration()
    original, calls = CampaignForecastFrozenConfiguration.content_sha256, []

    def counted(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(CampaignForecastFrozenConfiguration, "content_sha256", counted)
    composer = FrozenForecastComposer(frozen)
    for i in range(10):
        assert (
            composer.compose(trial_rows(i)).frozen_configuration_sha256
            == composer.configuration_sha
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation", ["drop", "reverse", "extra", "key", "role", "hash", "eligibility", "baseline"]
)
def test_missing_trial_or_different_key_baseline_and_eligibility_fail_before_composition(mutation):
    rows = trial_rows()
    if mutation == "drop":
        rows = rows[:1]
    elif mutation == "reverse":
        rows = tuple(reversed(rows))
    elif mutation == "extra":
        rows = (*rows, rows[0])
    else:
        changes = {
            "key": {"product_id": "another-product"},
            "role": {"role": "final_test"},
            "hash": {"example_sha256": "0" * 64},
            "eligibility": {"eligible": False},
            "baseline": {
                "values": (rows[1].values[0].model_copy(update={"mean": 9.0}), *rows[1].values[1:])
            },
        }[mutation]
        rows = (rows[0], rows[1].model_copy(update=changes))
    with pytest.raises(SnapshotError):
        FrozenForecastComposer(configuration()).compose(rows)


def test_exclusions_are_preserved_and_final_has_an_honest_separate_wire():
    composer = FrozenForecastComposer(configuration())
    excluded = composer.compose(trial_rows(role="final_test", eligible=False))
    assert excluded.role == "final_test" and excluded.exclusion_reasons == ("closed_target",)
    assert excluded.candidate.mean is None and excluded.reference.interval_center is None
    raw = trial_rows(role="final_test")[0].model_dump(mode="json")
    raw.pop("trial_tune_score_operation_id")
    with pytest.raises(ValidationError):
        CampaignForecastRawPrediction.model_validate_json(canonical_bytes(raw))
    frozen = composer.configuration
    common = dict(
        source_recipe_sha256="a" * 64,
        export_operation_id="final-export",
        frozen_configuration_sha256=frozen.content_sha256(),
        segment_policy_sha256="b" * 64,
        uncertainty_policy_sha256="c" * 64,
        worker_environment_lock_sha256=frozen.worker_environment_lock_sha256,
        resources=parents()[0].plan.resources,
    )
    assert (
        CampaignForecastEvaluationPlan(phase="final", role="final_test", **common).role
        == "final_test"
    )
    with pytest.raises(ValidationError, match="phase_role"):
        CampaignForecastEvaluationPlan(phase="development", role="final_test", **common)


@pytest.mark.parametrize(
    "mutation", ["drop_trial", "order", "fit", "population", "calibrator", "quality_policy"]
)
def test_receipt_binding_rejects_incomplete_or_resealed_incompatible_parents(mutation):
    tune, cal, scores, cal_scores, fits = parents()
    policy = CampaignForecastQualityPolicy()
    if mutation == "drop_trial":
        scores = scores[:1]
    elif mutation == "order":
        scores = tuple(reversed(scores))
    elif mutation == "fit":
        key = scores[0].plan.fit_operation_ids["rf"]
        fits[key] = fits[key].model_copy(update={"runtime_code_sha256": "0" * 64})
    elif mutation == "population":
        cal = cal.model_copy(update={"keys_sha256": "0" * 64})
    elif mutation == "calibrator":
        cal = cal.model_copy(update={"tune_receipt_sha256": "0" * 64})
    elif mutation == "quality_policy":
        tune = tune.model_copy(
            update={
                "plan": tune.plan.model_copy(update={"forecast_quality_policy_sha256": "0" * 64})
            }
        )
        cal = cal.model_copy(update={"tune_receipt_sha256": tune.content_sha256()})
    with pytest.raises(SnapshotError):
        bind_forecast_configuration(
            tune,
            cal,
            scores,
            cal_scores,
            fits,
            feature_schema_sha256="e" * 64,
            quality_policy=policy,
        )


def test_typed_configuration_rejects_unbound_selection_and_unfitted_calibration():
    frozen = configuration()
    raw = frozen.model_dump(mode="json")
    raw["calibration"]["selection"]["mean"]["fit_operation_id"] = "invented-fit"
    with pytest.raises(ValidationError, match="selected_fit"):
        CampaignForecastFrozenConfiguration.model_validate_json(canonical_bytes(raw))


def test_retained_baseline_and_reused_fit_cannot_have_conflicting_metadata():
    frozen = configuration()
    raw = frozen.model_dump(mode="json")
    raw["calibration"]["selection"]["mean"] = {
        "model": "history28",
        "fit_operation_id": None,
        "score_operation_id": None,
        "model_artifact_sha256": None,
    }
    with pytest.raises(ValidationError, match="retained_baseline_binding"):
        CampaignForecastFrozenConfiguration.model_validate_json(canonical_bytes(raw))
    raw = frozen.model_dump(mode="json")
    raw["trials"][1]["fit_operation_ids"]["rf"] = raw["trials"][0]["fit_operation_ids"]["rf"]
    with pytest.raises(ValidationError, match="conflicting_bindings"):
        CampaignForecastFrozenConfiguration.model_validate_json(canonical_bytes(raw))
    raw = frozen.model_dump(mode="json")
    raw["calibration"]["horizons"][0].update(radius=None, reasons=["insufficient_calibration_rows"])
    raw["calibration"].update(status="not_ready", calibration_fitted=False)
    with pytest.raises(ValidationError, match="fitted_calibration"):
        CampaignForecastFrozenConfiguration.model_validate_json(canonical_bytes(raw))
