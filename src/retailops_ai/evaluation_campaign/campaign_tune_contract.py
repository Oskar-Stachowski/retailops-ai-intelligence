"""Preregistered selection over every completed trial, with no calibration or release."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS

Model = Literal["history7", "history28", "weekday28", "rf_mean", "hgb", "tensorflow"]
Baseline = Literal["history7", "history28", "weekday28"]


class CampaignForecastTunePolicy(Contract):
    version: Literal["ai09-forecast-tune-policy-1.0.0"] = "ai09-forecast-tune-policy-1.0.0"
    median_objective: Literal["mae"] = "mae"
    mean_objective: Literal["mse_with_bias_guard"] = "mse_with_bias_guard"
    baseline_interval_objective: Literal["central_interval_score"] = "central_interval_score"
    candidate_interval_center: Literal["selected_median_then_fit_on_calibration_role"] = (
        "selected_median_then_fit_on_calibration_role"
    )
    nominal_coverage: Annotated[float, Field(ge=0.9, le=0.9)] = 0.9
    minimum_relative_median_improvement: Annotated[float, Field(ge=0.05, le=0.05)] = 0.05
    maximum_absolute_normalized_mean_bias: Annotated[float, Field(ge=0.1, le=0.1)] = 0.1
    minimum_rows: Literal[100] = 100
    minimum_eligibility_coverage: Annotated[float, Field(ge=0.8, le=0.8)] = 0.8
    zero_denominator: Literal["undefined_ratios_no_floor_or_ratio_gate"] = (
        "undefined_ratios_no_floor_or_ratio_gate"
    )
    tie_breaker: Literal["baseline_first_then_model_order_then_frozen_trial_order"] = (
        "baseline_first_then_model_order_then_frozen_trial_order"
    )
    model_order: tuple[str, ...] = MODELS

    @model_validator(mode="after")
    def order(self) -> Self:
        if self.model_order != MODELS:
            raise ValueError("campaign_tune_model_order_changed")
        return self


class CampaignForecastTunePlan(Contract):
    version: Literal["ai09-campaign-forecast-tune-1.0.0"] = "ai09-campaign-forecast-tune-1.0.0"
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    score_operation_ids: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=64)]
    campaign_selection_policy_sha256: Sha256
    forecast_quality_policy_sha256: Sha256
    worker_environment_lock_sha256: Sha256
    policy: CampaignForecastTunePolicy = CampaignForecastTunePolicy()
    max_rows: Annotated[int, Field(ge=1, le=10000000)] = 10000000
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**2)] = 16 * 1024**2
    resources: CampaignGenerationResources
    role: Literal["tune"] = "tune"
    population: Literal["all_common_tune_keys_in_every_preregistered_fit_trial"] = (
        "all_common_tune_keys_in_every_preregistered_fit_trial"
    )
    calibration_fitted: FalseFlag = False
    independent_evaluation_accessed: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if len(set(self.score_operation_ids)) != len(self.score_operation_ids):
            raise ValueError("campaign_tune_duplicate_trial")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastChoice(Contract):
    model: Model
    fit_operation_id: Symbol | None = None
    score_operation_id: Symbol | None = None
    model_artifact_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def origin(self) -> Self:
        learned = self.model not in MODELS[:3]
        fields = (self.fit_operation_id, self.score_operation_id, self.model_artifact_sha256)
        if any((value is not None) != learned for value in fields):
            raise ValueError("campaign_tune_choice_requires_complete_learned_artifact_binding")
        return self


class CampaignForecastTuneSelection(Contract):
    status: Literal["selected_for_independent_evaluation", "not_ready"]
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    baseline_mean: Baseline | None
    baseline_median: Baseline | None
    baseline_interval: Baseline | None
    mean: CampaignForecastChoice | None
    median: CampaignForecastChoice | None
    candidate_interval_center: CampaignForecastChoice | None
    reasons: tuple[str, ...]
    calibration_fitted: FalseFlag = False
    independent_evaluation_accessed: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def functionals(self) -> Self:
        ready = not self.reasons
        if (
            self.eligible_rows > self.rows
            or ready != (self.status == "selected_for_independent_evaluation")
            or self.median is not None
            and self.median.model == "rf_mean"
            or self.candidate_interval_center != self.median
            or ready
            and any(
                x is None
                for x in (
                    self.baseline_mean,
                    self.baseline_median,
                    self.baseline_interval,
                    self.mean,
                    self.median,
                )
            )
        ):
            raise ValueError("campaign_tune_selection_status_or_functionals_mismatch")
        return self


class CampaignForecastTuneReceipt(Contract):
    version: Literal["ai09-campaign-forecast-tune-receipt-1.0.0"] = (
        "ai09-campaign-forecast-tune-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignForecastTunePlan
    export_receipt_sha256: Sha256
    score_receipt_sha256: dict[str, Sha256]
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    runtime_code_sha256: Sha256
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    role_population_sha256: Sha256
    baseline_predictions_sha256: Sha256
    selection: CampaignForecastTuneSelection
    artifact_sha256: Sha256
    artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    calibration_fitted: FalseFlag = False
    quality_qualified: FalseFlag = False
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (
            set(self.score_receipt_sha256) != set(self.plan.score_operation_ids)
            or set(self.artifact_files)
            != {"plan.json", "parents.json", "metrics.json", "selection.json"}
            or self.artifact_sha256 != canonical_sha256(self.artifact_files)
            or (self.rows, self.eligible_rows)
            != (self.selection.rows, self.selection.eligible_rows)
            or self.rows > self.plan.max_rows
            or self.artifact_bytes > self.plan.max_output_bytes
        ):
            raise ValueError("campaign_tune_receipt_inventory_or_population_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
