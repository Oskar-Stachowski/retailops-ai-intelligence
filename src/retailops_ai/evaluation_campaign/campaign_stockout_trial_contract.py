"""One frozen full-population base or conditional stockout attempt and its receipt."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictFloat, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import start_of_day
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    CampaignStockoutRoleFile,
)
from retailops_ai.stockout_training.contract import Family, TrainingPolicy, Variant


class CampaignStockoutTrialPlan(Contract):
    version: Literal["ai09-stockout-trial-plan-1.0.0"] = "ai09-stockout-trial-plan-1.0.0"
    source_recipe_sha256: Sha256
    preparation_operation_id: Symbol
    kind: Literal["base", "conditional"]
    family: Family
    variant: Variant
    base_operation_id: Symbol | None = None
    calibrator_C: StrictFloat | None = None
    policy: TrainingPolicy = TrainingPolicy()
    initialization_seed: Literal[42] = 42
    max_artifact_bytes: Annotated[StrictInt, Field(ge=4096, le=512 * 1024**2)] = 128 * 1024**2
    resources: CampaignGenerationResources
    full_population: Literal["complete_original_eligible_role_no_sampling"] = (
        "complete_original_eligible_role_no_sampling"
    )
    reload_scope: Literal["portable_equations_on_same_fit_role_not_quality"] = (
        "portable_equations_on_same_fit_role_not_quality"
    )
    final_test_accessed: FalseFlag = False

    @model_validator(mode="after")
    def frozen(self) -> Self:
        conditional = self.kind == "conditional"
        if conditional != (self.base_operation_id is not None) or (
            self.calibrator_C not in (0.01, 0.1, 1.0, 10.0)
            if conditional
            else self.calibrator_C is not None
        ):
            raise ValueError("stockout_trial_original_base_and_frozen_C_required")
        r = self.resources
        if (
            self.policy != TrainingPolicy()
            or r.tree_rss_bytes > 12 * 1024**3
            or r.scratch_bytes > 8 * 1024**3
            or r.wall_seconds > 1200
            or r.minimum_available_memory_bytes < 1024**3
            or r.minimum_free_disk_bytes < 6 * 1024**3
            or self.max_artifact_bytes > r.scratch_bytes
        ):
            raise ValueError("stockout_trial_frozen_policy_or_resource_limits")
        return self

    @property
    def role(self) -> str:
        return "base_train" if self.kind == "base" else "calibrator_fit"

    def fit_clock(self, prepared: CampaignStockoutPreparationReceipt) -> UtcTime:
        return start_of_day(prepared.plan.policy.windows[1 if self.kind == "base" else 2])

    def content_sha256(self) -> str:
        value = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_sha256(value.model_dump(mode="json"))


class CampaignStockoutTrialReceipt(Contract):
    version: Literal["ai09-stockout-trial-receipt-1.0.0"] = "ai09-stockout-trial-receipt-1.0.0"
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignStockoutTrialPlan
    prepared: CampaignStockoutPreparationReceipt
    base_receipt_sha256: Sha256 | None
    base_pipeline_sha256: Sha256 | None
    role_file: CampaignStockoutRoleFile
    fit_known_at: UtcTime
    pipeline_sha256: Sha256
    model_artifact_bytes: Annotated[StrictInt, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    audit_scope: Literal["cooperating_local_runner_ordering_and_budget"] = (
        "cooperating_local_runner_ordering_and_budget"
    )
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        conditional = self.plan.kind == "conditional"
        if (
            self.prepared.protocol_sha256 != self.protocol_sha256
            or self.plan.source_recipe_sha256 != self.prepared.plan.source_recipe_sha256
            or self.plan.preparation_operation_id != self.prepared.operation_id
            or conditional != (self.base_receipt_sha256 is not None)
            or conditional != (self.base_pipeline_sha256 is not None)
            or self.role_file.rows < (20 if conditional else 2)
            or self.role_file.labels_sha256 is None
            or self.role_file.sha256 != self.prepared.artifact_files[self.plan.role + ".jsonl"]
            or self.fit_known_at != self.plan.fit_clock(self.prepared)
            or self.model_artifact_bytes > self.plan.max_artifact_bytes
            or set(self.artifact_files) != {"pipeline.json", "binding.json"}
            or self.artifact_files.get("pipeline.json") != self.pipeline_sha256
        ):
            raise ValueError("stockout_trial_receipt_binding_mismatch")
        return self

    def content_sha256(self) -> str:
        value = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_sha256(value.model_dump(mode="json"))
