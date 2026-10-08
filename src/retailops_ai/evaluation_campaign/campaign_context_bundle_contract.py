"""Prospective full-role Source context; metadata alone grants no access."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import EvaluationRole
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignContextStoragePolicy,
    CampaignForecastContextScope,
    CampaignForecastSegmentPolicy,
)

FILES = frozenset(
    {
        "recipe.json",
        "parents.json",
        "scope.json",
        "population.json",
        "census.json",
        "source-seal.json",
        "contexts.jsonl",
    }
)


class CampaignContextBundleRecipe(Contract):
    """Freeze parent operations and policy before generated identities exist."""

    version: Literal["ai09-source-context-bundle-recipe-1.0.0"] = (
        "ai09-source-context-bundle-recipe-1.0.0"
    )
    phase: Literal["development", "final"]
    role: EvaluationRole
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    export_operation_id: Symbol
    segment_policy: CampaignForecastSegmentPolicy
    storage_policy: CampaignContextStoragePolicy = CampaignContextStoragePolicy()
    max_rows: Annotated[int, Field(ge=1, le=100000000)] = 10000000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=4096, le=65536)] = 32768
    batch_windows: Annotated[int, Field(ge=1, le=256)] = 64
    resources: CampaignGenerationResources
    exposure: Literal["whole_source_parents_and_complete_role_no_sampling"] = (
        "whole_source_parents_and_complete_role_no_sampling"
    )
    private_source_effects_read: FalseFlag = False
    training_or_preprocessing_refitted: FalseFlag = False
    final_access_authorized_by_this_document: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def scope(self) -> Self:
        if (
            (self.phase == "final") != (self.role == "final_test")
            or self.generation_operation_id == self.export_operation_id
            or self.max_rows > self.segment_policy.max_rows
            or self.storage_policy.max_index_bytes > self.max_index_bytes
        ):
            raise ValueError("campaign_context_bundle_recipe_scope_or_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignContextBundleReceipt(Contract):
    """Completion requires the matching durable journal event and sealed bundle."""

    version: Literal["ai09-source-context-bundle-receipt-1.0.0"] = (
        "ai09-source-context-bundle-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    recipe: CampaignContextBundleRecipe
    generated_parent_receipt_sha256: Sha256
    generation_plan_sha256: Sha256
    export_receipt_sha256: Sha256
    runtime_code_sha256: Sha256
    scope: CampaignForecastContextScope
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    role_population_sha256: Sha256
    context_trace_sha256: Sha256
    census_sha256: Sha256
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    selection_sha256: Sha256 | None
    artifact_sha256: Sha256
    artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    full_role_label_file_passes: Literal[2] = 2
    complete_export_role_file_passes: Annotated[int, Field(ge=1, le=6)]
    actuals_in_context_rows: FalseFlag = False
    private_source_effects_read: FalseFlag = False
    quality_qualified: FalseFlag = False
    final_access_authorized_by_this_document: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.scope.role != self.recipe.role
            or self.scope.source_recipe_sha256 != self.recipe.source_recipe_sha256
            or self.scope.segment_policy_sha256 != self.recipe.segment_policy.content_sha256()
            or self.eligible_rows > self.rows
            or self.rows > self.recipe.max_rows
            or self.artifact_bytes > self.recipe.max_output_bytes
            or set(self.artifact_files) != FILES
            or self.artifact_sha256 != canonical_sha256(self.artifact_files)
            or (self.recipe.phase == "final") != (self.selection_sha256 is not None)
            or self.complete_export_role_file_passes != (1 if self.recipe.phase == "final" else 6)
        ):
            raise ValueError("campaign_context_bundle_receipt_scope_or_inventory_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
