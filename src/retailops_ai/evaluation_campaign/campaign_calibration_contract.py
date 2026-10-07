"""Calibration-only residual fit after immutable Tune selection, never release proof."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastChoice,
    CampaignForecastTuneSelection,
)


def residual_rank(n: int) -> int:
    """Exact ceil((n+1)*0.9); do not truncate an unavailable order statistic."""
    return (9 * (n + 1) + 9) // 10


class CampaignForecastCalibrationPlan(Contract):
    version: Literal["ai09-campaign-forecast-calibration-1.0.0"] = (
        "ai09-campaign-forecast-calibration-1.0.0"
    )
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    tune_operation_id: Symbol
    score_operation_ids: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=64)]
    forecast_quality_policy_sha256: Sha256
    worker_environment_lock_sha256: Sha256
    resources: CampaignGenerationResources
    max_rows: Annotated[int, Field(ge=1, le=10000000)] = 10000000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**2)] = 16 * 1024**2
    role: Literal["calibration"] = "calibration"
    method: Literal["per_horizon_absolute_median_residual_split_conformal"] = (
        "per_horizon_absolute_median_residual_split_conformal"
    )
    nominal_coverage: Annotated[float, Field(ge=0.9, le=0.9)] = 0.9
    minimum_eligible_rows_per_horizon: Literal[100] = 100
    minimum_eligibility_coverage_per_horizon: Annotated[float, Field(ge=0.8, le=0.8)] = 0.8
    center: Literal["completed_tune_median_choice_no_architecture_reselection"] = (
        "completed_tune_median_choice_no_architecture_reselection"
    )
    interval_bounds: Literal["max_zero_median_minus_radius_and_median_plus_radius"] = (
        "max_zero_median_minus_radius_and_median_plus_radius"
    )
    temporal_coverage_guarantee_claimed: FalseFlag = False
    training_or_preprocessing_refitted: FalseFlag = False
    independent_evaluation_accessed: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if len(set(self.score_operation_ids)) != len(self.score_operation_ids):
            raise ValueError("campaign_calibration_duplicate_score_trial")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastHorizonCalibration(Contract):
    horizon_days: Annotated[int, Field(ge=1, le=14)]
    rows: Annotated[int, Field(ge=0)]
    eligible_rows: Annotated[int, Field(ge=0)]
    quantile_rank: Annotated[int, Field(ge=1)]
    radius: Annotated[float, Field(ge=0)] | None
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def population(self) -> Self:
        if (
            self.eligible_rows > self.rows
            or self.quantile_rank != residual_rank(self.eligible_rows)
            or (self.radius is None) != bool(self.reasons)
            or not self.reasons
            and self.quantile_rank > self.eligible_rows
            or not self.reasons
            and (self.eligible_rows < 100 or self.rows == 0 or self.eligible_rows / self.rows < 0.8)
        ):
            raise ValueError("campaign_calibration_horizon_rank_or_status_mismatch")
        return self


class CampaignForecastCalibration(Contract):
    status: Literal["fitted_for_independent_evaluation", "not_ready"]
    selection: CampaignForecastTuneSelection
    center: CampaignForecastChoice
    calibration_score_operation_id: Symbol
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    horizons: Annotated[
        tuple[CampaignForecastHorizonCalibration, ...], Field(min_length=14, max_length=14)
    ]
    calibration_fitted: bool
    temporal_coverage_guarantee_claimed: FalseFlag = False
    independent_evaluation_accessed: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        fitted = all(not h.reasons for h in self.horizons)
        if (
            self.selection.status != "selected_for_independent_evaluation"
            or self.center != self.selection.median
            or self.center.model == "rf_mean"
            or tuple(h.horizon_days for h in self.horizons) != tuple(range(1, 15))
            or sum(h.rows for h in self.horizons) != self.rows
            or sum(h.eligible_rows for h in self.horizons) != self.eligible_rows
            or fitted != self.calibration_fitted
            or fitted != (self.status == "fitted_for_independent_evaluation")
        ):
            raise ValueError("campaign_calibration_complete_horizon_or_selection_mismatch")
        return self


class CampaignForecastCalibrationReceipt(Contract):
    version: Literal["ai09-campaign-forecast-calibration-receipt-1.0.0"] = (
        "ai09-campaign-forecast-calibration-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignForecastCalibrationPlan
    export_receipt_sha256: Sha256
    tune_receipt_sha256: Sha256
    score_receipt_sha256: dict[str, Sha256]
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    runtime_code_sha256: Sha256
    role_population_sha256: Sha256
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    calibration: CampaignForecastCalibration
    artifact_sha256: Sha256
    artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    training_or_preprocessing_refitted: FalseFlag = False
    independent_evaluation_accessed: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (
            set(self.score_receipt_sha256) != set(self.plan.score_operation_ids)
            or self.calibration.calibration_score_operation_id not in self.plan.score_operation_ids
            or (self.rows, self.eligible_rows)
            != (self.calibration.rows, self.calibration.eligible_rows)
            or self.rows > self.plan.max_rows
            or self.artifact_bytes > self.plan.max_output_bytes
            or set(self.artifact_files)
            != {"plan.json", "parents.json", "population.json", "calibration.json"}
            or self.artifact_sha256 != canonical_sha256(self.artifact_files)
        ):
            raise ValueError("campaign_calibration_receipt_inventory_or_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
