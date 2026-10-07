"""Complete common-key development predictions; raw diagnostics grant no qualification."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, ForecastKey, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_fit_contract import Family
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast

FAMILIES: tuple[Family, ...] = ("rf", "hgb", "tensorflow")
ScoreRole = Literal["tune", "calibration"]


class CampaignForecastScorePlan(Contract):
    version: Literal["ai09-campaign-raw-forecast-score-1.0.0"] = (
        "ai09-campaign-raw-forecast-score-1.0.0"
    )
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    fit_operation_ids: dict[Family, Symbol]
    role: ScoreRole
    worker_environment_lock_sha256: Sha256
    max_rows: Annotated[int, Field(ge=1, le=10000000)] = 10000000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    batch_windows: Annotated[int, Field(ge=1, le=256)] = 64
    resources: CampaignGenerationResources
    population: Literal["all_role_keys_with_identical_eligibility_no_sampling"] = (
        "all_role_keys_with_identical_eligibility_no_sampling"
    )
    model_order: tuple[str, ...] = MODELS
    interval_scope: Literal["raw_empirical_baselines_only_candidates_uncalibrated"] = (
        "raw_empirical_baselines_only_candidates_uncalibrated"
    )
    encoding: Literal["completed_fit_train_encoding_without_refit"] = (
        "completed_fit_train_encoding_without_refit"
    )
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if (
            set(self.fit_operation_ids) != set(FAMILIES)
            or len(set(self.fit_operation_ids.values())) != 3
            or self.model_order != MODELS
        ):
            raise ValueError("campaign_score_complete_model_inventory_required")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastRawPrediction(ForecastKey):
    role: ScoreRole
    example_sha256: Sha256
    eligible: bool
    exclusion_reasons: tuple[str, ...]
    values: Annotated[tuple[FunctionalForecast, ...], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def functionals(self) -> Self:
        if self.eligible != (not self.exclusion_reasons):
            raise ValueError("campaign_score_eligibility_reason_mismatch")
        if self.values[3].median is not None or any(v.interval for v in self.values[3:]):
            raise ValueError("campaign_score_raw_candidate_functionals_mismatch")
        if not self.eligible and any(
            v.mean is not None or v.median is not None or v.interval is not None
            for v in self.values
        ):
            raise ValueError("campaign_score_excluded_key_has_prediction")
        return self


class CampaignForecastScoreReceipt(Contract):
    version: Literal["ai09-campaign-raw-forecast-score-receipt-1.0.0"] = (
        "ai09-campaign-raw-forecast-score-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignForecastScorePlan
    export_receipt_sha256: Sha256
    fit_receipt_sha256: dict[Family, Sha256]
    model_artifact_sha256: dict[Family, Sha256]
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    runtime_code_sha256: Sha256
    role_population_sha256: Sha256
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
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
            set(self.fit_receipt_sha256) != set(FAMILIES)
            or set(self.model_artifact_sha256) != set(FAMILIES)
            or set(self.artifact_files)
            != {"plan.json", "parents.json", "predictions.jsonl", "metrics.json"}
            or self.artifact_sha256 != canonical_sha256(self.artifact_files)
            or self.eligible_rows > self.rows
            or self.rows > self.plan.max_rows
            or self.artifact_bytes > self.plan.max_output_bytes
        ):
            raise ValueError("campaign_score_receipt_inventory_or_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
