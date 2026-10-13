"""Freeze stockout preparation before Source and bind actual completed parents."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignGeneratedParentReceipt,
    CampaignParentBudget,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignContextStoragePolicy
from retailops_ai.evaluation_campaign.campaign_stockout_label_facts import (
    CampaignStockoutLabelStoragePolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_labels import CampaignStockoutLabelPlan
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    CampaignStockoutMaturityPlan,
    CampaignStockoutMaturityPolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutParentPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_qualification import (
    CampaignStockoutQualificationPolicy,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    FILES,
    CampaignStockoutRoleArtifactPlan,
)


class CampaignStockoutPreparationPlan(Contract):
    version: Literal["ai09-stockout-preparation-plan-1.0.0"] = (
        "ai09-stockout-preparation-plan-1.0.0"
    )
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    policy: CampaignStockoutMaturityPolicy
    parent_budget: CampaignParentBudget = CampaignParentBudget()
    public_storage: CampaignContextStoragePolicy = CampaignContextStoragePolicy()
    label_storage: CampaignStockoutLabelStoragePolicy = CampaignStockoutLabelStoragePolicy()
    qualification: CampaignStockoutQualificationPolicy = CampaignStockoutQualificationPolicy()
    max_rows: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 1_000_000
    max_index_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_public_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_role_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_stream_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    resources: CampaignGenerationResources
    phase: Literal["development"] = "development"
    scope: Literal["complete_public_upstream_original_labels_and_all_five_mature_roles"] = (
        "complete_public_upstream_original_labels_and_all_five_mature_roles"
    )
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def bounded(self) -> Self:
        r = self.resources
        if (
            self.public_storage.max_index_bytes > self.max_index_bytes
            or r.tree_rss_bytes > 12 * 1024**3
            or r.scratch_bytes > 8 * 1024**3
            or r.wall_seconds > 10800
            or r.minimum_available_memory_bytes < 1024**3
            or r.minimum_free_disk_bytes < 6 * 1024**3
            or self.max_public_bytes + self.max_role_bytes > r.scratch_bytes
        ):
            raise ValueError("stockout_preparation_frozen_resource_limits")
        return self

    def content_sha256(self) -> str:
        value = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_sha256(value.model_dump(mode="json"))

    def public_plan(self, generated: CampaignGeneratedParentReceipt) -> CampaignStockoutPublicPlan:
        source = generated.source
        if {k: getattr(source, k) for k in type(self.parent_budget).model_fields} != (
            self.parent_budget.model_dump(mode="python")
        ):
            raise ValueError("stockout_preparation_parent_budget_changed")
        return CampaignStockoutPublicPlan(
            parent=CampaignStockoutParentPlan(
                source=source,
                runtime=generated.runtime,
                storage=self.public_storage,
                max_origins=self.max_rows,
            ),
            max_index_bytes=self.max_index_bytes,
            max_artifact_bytes=self.max_public_bytes,
        )

    def role_plan(
        self, generated: CampaignGeneratedParentReceipt, public_dataset_id: str
    ) -> CampaignStockoutRoleArtifactPlan:
        return CampaignStockoutRoleArtifactPlan(
            maturity=CampaignStockoutMaturityPlan(
                public_dataset_id=public_dataset_id,
                public=self.public_plan(generated),
                labels=CampaignStockoutLabelPlan(
                    source=generated.source,
                    runtime=generated.runtime,
                    storage=self.label_storage,
                    qualification=self.qualification,
                ),
                policy=self.policy,
                max_rows=self.max_rows,
                max_stream_bytes=self.max_stream_bytes,
            ),
            max_artifact_bytes=self.max_role_bytes,
        )


class CampaignStockoutPreparationReceipt(Contract):
    version: Literal["ai09-stockout-preparation-receipt-1.0.0"] = (
        "ai09-stockout-preparation-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignStockoutPreparationPlan
    generated: CampaignGeneratedParentReceipt
    role_plan: CampaignStockoutRoleArtifactPlan
    public_dataset_id: Annotated[str, Field(pattern=r"^ai09-stockout-public-sha256-[0-9a-f]{64}$")]
    dataset_id: Annotated[str, Field(pattern=r"^ai09-stockout-roles-sha256-[0-9a-f]{64}$")]
    manifest_sha256: Sha256
    artifact_bytes: Annotated[StrictInt, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    audit_scope: Literal["cooperating_local_runner_ordering_and_budget"] = (
        "cooperating_local_runner_ordering_and_budget"
    )
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.generated.protocol_sha256 != self.protocol_sha256
            or self.generated.source_recipe_sha256 != self.plan.source_recipe_sha256
            or self.generated.operation_id != self.plan.generation_operation_id
            or self.role_plan != self.plan.role_plan(self.generated, self.public_dataset_id)
            or self.artifact_bytes > self.plan.max_role_bytes
            or self.artifact_files.get("manifest.json") != self.manifest_sha256
            or set(self.artifact_files) != {"manifest.json", *FILES}
        ):
            raise ValueError("stockout_preparation_receipt_binding_mismatch")
        return self

    def content_sha256(self) -> str:
        value = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_sha256(value.model_dump(mode="json"))
