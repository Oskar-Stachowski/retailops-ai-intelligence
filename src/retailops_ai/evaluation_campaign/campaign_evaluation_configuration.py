"""Bind every development trial and fixed functional; never read labels or select again."""

from collections.abc import Sequence

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastFrozenConfiguration,
    CampaignForecastQualityPolicy,
    CampaignForecastTrialBinding,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.source_snapshot.files import SnapshotError

POPULATION = (
    "rows",
    "eligible_rows",
    "keys_sha256",
    "eligible_keys_sha256",
    "role_population_sha256",
)


def bind_forecast_configuration(
    tune: CampaignForecastTuneReceipt,
    calibration: CampaignForecastCalibrationReceipt,
    tune_scores: Sequence[CampaignForecastScoreReceipt],
    calibration_scores: Sequence[CampaignForecastScoreReceipt],
    fits: dict[str, CampaignForecastFitReceipt],
    *,
    feature_schema_sha256: str,
    quality_policy: CampaignForecastQualityPolicy,
) -> CampaignForecastFrozenConfiguration:
    """Pure metadata binding; the audited runner separately proves all journal completions."""
    tune = CampaignForecastTuneReceipt.model_validate_json(
        canonical_bytes(tune.model_dump(mode="json"))
    )
    calibration = CampaignForecastCalibrationReceipt.model_validate_json(
        canonical_bytes(calibration.model_dump(mode="json"))
    )
    tune_scores = tuple(
        CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(s.model_dump(mode="json")))
        for s in tune_scores
    )
    calibration_scores = tuple(
        CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(s.model_dump(mode="json")))
        for s in calibration_scores
    )
    fits = {
        key: CampaignForecastFitReceipt.model_validate_json(
            canonical_bytes(f.model_dump(mode="json"))
        )
        for key, f in fits.items()
    }
    if (
        calibration.tune_receipt_sha256 != tune.content_sha256()
        or calibration.plan.tune_operation_id != tune.operation_id
        or calibration.calibration.selection != tune.selection
        or calibration.export_receipt_sha256 != tune.export_receipt_sha256
        or any(
            getattr(calibration, field) != getattr(tune, field)
            for field in ("protocol_sha256", "runtime_code_sha256", "dataset_id")
        )
        or any(
            getattr(calibration.plan, field) != getattr(tune.plan, field)
            for field in (
                "source_recipe_sha256",
                "export_operation_id",
                "worker_environment_lock_sha256",
                "forecast_quality_policy_sha256",
            )
        )
        or quality_policy.content_sha256() != tune.plan.forecast_quality_policy_sha256
        or tuple(s.operation_id for s in tune_scores) != tune.plan.score_operation_ids
        or tuple(s.operation_id for s in calibration_scores) != calibration.plan.score_operation_ids
        or {s.operation_id: s.content_sha256() for s in tune_scores} != tune.score_receipt_sha256
        or {s.operation_id: s.content_sha256() for s in calibration_scores}
        != calibration.score_receipt_sha256
    ):
        raise SnapshotError("campaign_evaluation_tune_calibration_receipt_binding_mismatch")

    def triplet(score: CampaignForecastScoreReceipt) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (
                score.plan.fit_operation_ids[f],
                score.fit_receipt_sha256[f],
                score.model_artifact_sha256[f],
            )
            for f in FAMILIES
        )

    for role, scores in (("tune", tune_scores), ("calibration", calibration_scores)):
        populations = set()
        for score in scores:
            if (
                score.plan.role != role
                or score.export_receipt_sha256 != tune.export_receipt_sha256
                or any(
                    getattr(score, field) != getattr(tune, field)
                    for field in ("protocol_sha256", "runtime_code_sha256", "dataset_id")
                )
                or any(
                    getattr(score.plan, field) != getattr(tune.plan, field)
                    for field in (
                        "source_recipe_sha256",
                        "export_operation_id",
                        "worker_environment_lock_sha256",
                    )
                )
            ):
                raise SnapshotError("campaign_evaluation_trial_context_mismatch")
            populations.add(tuple(getattr(score, f) for f in POPULATION))
        if len(populations) != 1:
            raise SnapshotError("campaign_evaluation_common_trial_population_required")
        parent = tune if role == "tune" else calibration
        if tuple(getattr(parent, f) for f in POPULATION) not in populations:
            raise SnapshotError("campaign_evaluation_selection_or_calibration_population_mismatch")
    matched = {triplet(score): score for score in calibration_scores}
    if (
        len(matched) != len(calibration_scores)
        or len({triplet(s) for s in tune_scores}) != len(tune_scores)
        or {triplet(s) for s in tune_scores} != set(matched)
    ):
        raise SnapshotError("campaign_evaluation_every_tune_trial_once_on_calibration_required")
    required_fits = {key for score in tune_scores for key in score.plan.fit_operation_ids.values()}
    if set(fits) != required_fits:
        raise SnapshotError("campaign_evaluation_complete_fit_inventory_required")
    training_populations = set()
    trials = []
    for score in tune_scores:
        for family in FAMILIES:
            key = score.plan.fit_operation_ids[family]
            fit = fits[key]
            if (
                fit.operation_id != key
                or fit.plan.family != family
                or fit.content_sha256() != score.fit_receipt_sha256[family]
                or fit.model_artifact_sha256 != score.model_artifact_sha256[family]
                or fit.export_receipt_sha256 != tune.export_receipt_sha256
                or any(
                    getattr(fit, field) != getattr(tune, field)
                    for field in ("protocol_sha256", "runtime_code_sha256", "dataset_id")
                )
                or any(
                    getattr(fit.plan, field) != getattr(tune.plan, field)
                    for field in (
                        "source_recipe_sha256",
                        "export_operation_id",
                        "worker_environment_lock_sha256",
                    )
                )
            ):
                raise SnapshotError("campaign_evaluation_fit_receipt_binding_mismatch")
            training_populations.add(
                (
                    fit.train_keys_sha256,
                    fit.early_stopping_keys_sha256,
                    fit.train_eligible_rows,
                    fit.early_stopping_eligible_rows,
                )
            )
        cal_score = matched[triplet(score)]
        trials.append(
            CampaignForecastTrialBinding(
                tune_score_operation_id=score.operation_id,
                calibration_score_operation_id=cal_score.operation_id,
                tune_score_receipt_sha256=score.content_sha256(),
                calibration_score_receipt_sha256=cal_score.content_sha256(),
                fit_operation_ids=score.plan.fit_operation_ids,
                fit_receipt_sha256=score.fit_receipt_sha256,
                model_artifact_sha256=score.model_artifact_sha256,
                encoding_sha256={
                    f: fits[score.plan.fit_operation_ids[f]].encoding_sha256 for f in FAMILIES
                },
            )
        )
    if len(training_populations) != 1:
        raise SnapshotError("campaign_evaluation_common_fitted_population_required")
    return CampaignForecastFrozenConfiguration(
        protocol_sha256=tune.protocol_sha256,
        development_source_recipe_sha256=tune.plan.source_recipe_sha256,
        development_dataset_id=tune.dataset_id,
        runtime_code_sha256=tune.runtime_code_sha256,
        worker_environment_lock_sha256=tune.plan.worker_environment_lock_sha256,
        feature_schema_sha256=feature_schema_sha256,
        tune_operation_id=tune.operation_id,
        tune_receipt_sha256=tune.content_sha256(),
        calibration_operation_id=calibration.operation_id,
        calibration_receipt_sha256=calibration.content_sha256(),
        quality_policy_sha256=quality_policy.content_sha256(),
        calibration=calibration.calibration,
        trials=tuple(trials),
    )
