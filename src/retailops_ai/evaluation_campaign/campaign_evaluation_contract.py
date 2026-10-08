"""Frozen forecast bindings and honest independent/final wires; no journal permission."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, ForecastKey, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibration,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import Family
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast, Units

EvaluationRole = Literal["development_evaluation", "final_test"]


class CampaignForecastQualityPolicy(Contract):
    """The same numerical gates as v2, with an explicit campaign evaluation scope."""

    version: Literal["ai09-campaign-forecast-quality-1.0.0"] = (
        "ai09-campaign-forecast-quality-1.0.0"
    )
    evaluation_use: Literal["independent_and_final_after_frozen_selection_no_reselection"] = (
        "independent_and_final_after_frozen_selection_no_reselection"
    )
    minimum_relative_mae_improvement: Annotated[float, Field(ge=0.05, le=0.05)] = 0.05
    maximum_segment_mae_regression: Annotated[float, Field(ge=0.10, le=0.10)] = 0.10
    maximum_absolute_normalized_mean_bias: Annotated[float, Field(ge=0.10, le=0.10)] = 0.10
    minimum_global_rows: Literal[100] = 100
    minimum_segment_rows: Literal[30] = 30
    minimum_eligibility_coverage: Annotated[float, Field(ge=0.80, le=0.80)] = 0.80
    minimum_prediction_coverage: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    minimum_calibration_rows_per_horizon: Literal[100] = 100
    nominal_coverage: Annotated[float, Field(ge=0.90, le=0.90)] = 0.90
    minimum_empirical_coverage: Annotated[float, Field(ge=0.80, le=0.80)] = 0.80
    legacy_width_ratio_threshold: Annotated[float, Field(ge=2.0, le=2.0)] = 2.0
    interval_objective: Literal["central_interval_score_no_regression_against_baseline"] = (
        "central_interval_score_no_regression_against_baseline"
    )
    zero_actuals: Literal["absolute_errors_and_false_positive_units_no_ratio_gate"] = (
        "absolute_errors_and_false_positive_units_no_ratio_gate"
    )
    reference_functionals: Literal["separate_tune_choices_not_a_joint_distribution"] = (
        "separate_tune_choices_not_a_joint_distribution"
    )

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastReference(Contract):
    """Metric references can come from different baselines; retain the band's own center."""

    mean: Units | None
    median: Units | None
    interval: CentralInterval | None
    interval_center: Units | None
    functionals: Literal["independently_selected_metric_references_not_joint_distribution"] = (
        "independently_selected_metric_references_not_joint_distribution"
    )

    @model_validator(mode="after")
    def band_center(self) -> Self:
        if (self.interval is None) != (self.interval_center is None) or (
            self.interval is not None
            and self.interval_center is not None
            and not self.interval.lower <= self.interval_center <= self.interval.upper
        ):
            raise ValueError("campaign_evaluation_reference_interval_center_mismatch")
        return self


class CampaignForecastTrialBinding(Contract):
    tune_score_operation_id: Symbol
    calibration_score_operation_id: Symbol
    tune_score_receipt_sha256: Sha256
    calibration_score_receipt_sha256: Sha256
    fit_operation_ids: dict[Family, Symbol]
    fit_receipt_sha256: dict[Family, Sha256]
    model_artifact_sha256: dict[Family, Sha256]
    encoding_sha256: dict[Family, Sha256]

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if (
            any(
                set(mapping) != set(FAMILIES)
                for mapping in (
                    self.fit_operation_ids,
                    self.fit_receipt_sha256,
                    self.model_artifact_sha256,
                    self.encoding_sha256,
                )
            )
            or len(set(self.fit_operation_ids.values())) != 3
            or self.tune_score_operation_id == self.calibration_score_operation_id
        ):
            raise ValueError("campaign_evaluation_complete_trial_binding_required")
        return self


class CampaignForecastFrozenConfiguration(Contract):
    version: Literal["ai09-frozen-forecast-configuration-1.0.0"] = (
        "ai09-frozen-forecast-configuration-1.0.0"
    )
    protocol_sha256: Sha256
    development_source_recipe_sha256: Sha256
    development_dataset_id: Annotated[
        str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")
    ]
    runtime_code_sha256: Sha256
    worker_environment_lock_sha256: Sha256
    feature_schema_sha256: Sha256
    tune_operation_id: Symbol
    tune_receipt_sha256: Sha256
    calibration_operation_id: Symbol
    calibration_receipt_sha256: Sha256
    quality_policy_sha256: Sha256
    calibration: CampaignForecastCalibration
    trials: Annotated[tuple[CampaignForecastTrialBinding, ...], Field(min_length=1, max_length=64)]
    journal_completion_proved_by_this_document: FalseFlag = False
    final_access_authorized_by_this_document: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def selected_bindings(self) -> Self:
        selected = self.calibration.selection
        scores = {trial.tune_score_operation_id: trial for trial in self.trials}
        cal_ids = {trial.calibration_score_operation_id for trial in self.trials}
        triplets = {tuple(t.fit_operation_ids[f] for f in FAMILIES) for t in self.trials}
        if (
            self.calibration.status != "fitted_for_independent_evaluation"
            or not self.calibration.calibration_fitted
            or len(scores) != len(self.trials)
            or len(cal_ids) != len(self.trials)
            or len(triplets) != len(self.trials)
            or self.tune_operation_id == self.calibration_operation_id
        ):
            raise ValueError("campaign_evaluation_fitted_calibration_and_all_trials_required")
        fitted: dict[str, tuple[str, str, str, str]] = {}
        for trial in self.trials:
            for family in FAMILIES:
                identity = (
                    family,
                    trial.fit_receipt_sha256[family],
                    trial.model_artifact_sha256[family],
                    trial.encoding_sha256[family],
                )
                operation = trial.fit_operation_ids[family]
                if operation in fitted and fitted[operation] != identity:
                    raise ValueError("campaign_evaluation_same_fit_has_conflicting_bindings")
                fitted[operation] = identity
        for choice, baseline in (
            (selected.mean, selected.baseline_mean),
            (selected.median, selected.baseline_median),
        ):
            if choice is None:
                raise ValueError("campaign_evaluation_frozen_functional_missing")
            if choice.score_operation_id is None and choice.model != baseline:
                raise ValueError("campaign_evaluation_retained_baseline_binding_mismatch")
            if choice.score_operation_id is not None:
                selected_trial = scores.get(choice.score_operation_id)
                selected_family: Family = (
                    "rf"
                    if choice.model == "rf_mean"
                    else "tensorflow"
                    if choice.model == "tensorflow"
                    else "hgb"
                )
                if (
                    selected_trial is None
                    or selected_trial.fit_operation_ids[selected_family] != choice.fit_operation_id
                    or selected_trial.model_artifact_sha256[selected_family]
                    != choice.model_artifact_sha256
                ):
                    raise ValueError("campaign_evaluation_selected_fit_binding_mismatch")
        if selected.median is None:
            raise ValueError("campaign_evaluation_frozen_functional_missing")
        median_trial = scores[
            selected.median.score_operation_id or self.trials[0].tune_score_operation_id
        ]
        if (
            self.calibration.calibration_score_operation_id
            != median_trial.calibration_score_operation_id
        ):
            raise ValueError("campaign_evaluation_calibration_selected_trial_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastEvaluationPlan(Contract):
    version: Literal["ai09-campaign-forecast-evaluation-1.0.0"] = (
        "ai09-campaign-forecast-evaluation-1.0.0"
    )
    phase: Literal["development", "final"]
    role: EvaluationRole
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    frozen_configuration_sha256: Sha256
    quality_policy: CampaignForecastQualityPolicy = CampaignForecastQualityPolicy()
    segment_policy_sha256: Sha256
    uncertainty_policy_sha256: Sha256
    worker_environment_lock_sha256: Sha256
    max_rows: Annotated[int, Field(ge=1, le=100000000)] = 10000000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    batch_windows: Annotated[int, Field(ge=1, le=256)] = 64
    resources: CampaignGenerationResources
    population: Literal["every_role_key_every_frozen_trial_no_sampling"] = (
        "every_role_key_every_frozen_trial_no_sampling"
    )
    training_or_preprocessing_refitted: FalseFlag = False
    architecture_reselected: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def scope(self) -> Self:
        if (self.phase == "final") != (self.role == "final_test"):
            raise ValueError("campaign_evaluation_phase_role_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastTrialPrediction(ForecastKey):
    role: EvaluationRole
    trial_tune_score_operation_id: Symbol
    example_sha256: Sha256
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    values: Annotated[tuple[FunctionalForecast, ...], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def raw_functionals(self) -> Self:
        if (
            self.eligible != (not self.exclusion_reasons)
            or self.values[3].median is not None
            or any(v.interval for v in self.values[3:])
            or not self.eligible
            and any(
                v.mean is not None or v.median is not None or v.interval is not None
                for v in self.values
            )
        ):
            raise ValueError("campaign_evaluation_raw_functionals_or_eligibility_mismatch")
        return self


class CampaignForecastEvaluationPrediction(ForecastKey):
    role: EvaluationRole
    example_sha256: Sha256
    frozen_configuration_sha256: Sha256
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    candidate: FunctionalForecast
    reference: CampaignForecastReference

    @model_validator(mode="after")
    def eligibility(self) -> Self:
        if self.eligible != (not self.exclusion_reasons) or (
            not self.eligible
            and any(
                value is not None
                for value in (
                    self.candidate.mean,
                    self.candidate.median,
                    self.candidate.interval,
                    self.reference.mean,
                    self.reference.median,
                    self.reference.interval,
                    self.reference.interval_center,
                )
            )
        ):
            raise ValueError("campaign_evaluation_excluded_key_has_prediction")
        return self
