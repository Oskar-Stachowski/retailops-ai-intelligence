"""Complete three-variant preparation with explicit ordinary reuse and cold costs."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
    NativeDevelopmentPlanningProtocol,
    ResolvedNativeDevelopmentPlanningReceipt,
)
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfilePreparation,
    DevelopmentProfileSource,
)
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec


class OrdinaryDevelopmentReusePlan(Contract):
    version: Literal["ai09-ordinary-development-reuse-plan-1.0.0"] = (
        "ai09-ordinary-development-reuse-plan-1.0.0"
    )
    bootstrap_protocol_sha256: Sha256
    bootstrap_journal_head_sha256: Sha256
    generated_receipt_sha256: Sha256
    planning_receipt_sha256: Sha256
    resources: CampaignGenerationResources
    exposure: Literal["complete_snapshot_and_curated_parent_independently_replayed"] = (
        "complete_snapshot_and_curated_parent_independently_replayed"
    )
    source_generation_authorized: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


def variant_operations(
    preparation: DevelopmentProfilePreparation,
    reuse: OrdinaryDevelopmentReusePlan,
) -> tuple[CampaignOperationPlan, ...]:
    ordinary = preparation.sources[0]
    operations = [
        CampaignOperationPlan(
            operation_id=f"development-{ordinary.products}-ordinary-reuse",
            phase="development",
            action="source_read",
            use_case="source",
            role="all_parent_data",
            source_recipe_sha256=ordinary.content_sha256(),
            execution_recipe_sha256=reuse.content_sha256(),
        )
    ]
    for source, plan in zip(preparation.sources[1:], preparation.generations[1:], strict=True):
        operations.append(
            CampaignOperationPlan(
                operation_id=f"development-{source.products}-{source.variant}-generate",
                phase="development",
                action="source_generate",
                use_case="source",
                role="all_parent_data",
                source_recipe_sha256=source.content_sha256(),
                execution_recipe_sha256=plan.content_sha256(),
                prerequisites=(operations[-1].operation_id,),
            )
        )
    return tuple(operations)


class ResolvedDevelopmentPreparationProtocol(CampaignProtocol):
    resolved_development_preparation_version: Literal[
        "ai09-resolved-development-variants-1.0.0"
    ] = "ai09-resolved-development-variants-1.0.0"
    bootstrap: NativeDevelopmentPlanningProtocol
    generated: CampaignGeneratedParentReceipt
    planned: ResolvedNativeDevelopmentPlanningReceipt
    planning_cost: CampaignCost
    reuse: OrdinaryDevelopmentReusePlan
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=3, max_length=3)]
    maximum_new_attempts: Literal[3] = 3
    maximum_new_fit_attempts: Literal[0] = 0
    maximum_total_wall_seconds: Annotated[int, Field(ge=2, le=10800)] = 10800

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        preparation = self.planned.preparation
        if (
            any(
                getattr(self, k) != getattr(self.bootstrap, k)
                for k in DevelopmentPlanningContext.model_fields
            )
            or self.bootstrap.content_sha256() != self.reuse.bootstrap_protocol_sha256
            or self.generated.protocol_sha256 != self.bootstrap.content_sha256()
            or self.planned.protocol_sha256 != self.bootstrap.content_sha256()
            or self.generated.content_sha256() != self.reuse.generated_receipt_sha256
            or self.planned.content_sha256() != self.reuse.planning_receipt_sha256
            or self.generated.content_sha256() != self.planned.generation_receipt_sha256
            or self.generated.source.parent.source_dataset_id != self.planned.source_dataset_id
            or self.generated.runtime != self.runtime
            or self.planned.runtime != self.runtime
            or self.generated.operation_id != self.bootstrap.operations[0].operation_id
            or self.planned.operation_id != self.bootstrap.operations[1].operation_id
            or self.planned.planning_sha256 != self.bootstrap.planning.content_sha256()
            or self.planned.producer_planner_sha256
            != self.bootstrap.planning.producer_planner_sha256
            or preparation.sources[0] != self.bootstrap.sources[0]
            or preparation.generations[0] != self.bootstrap.generation
            or self.sources != preparation.sources
            or self.operations != variant_operations(preparation, self.reuse)
            or self.planning_cost.peak_process_tree_rss_bytes is None
            or self.planning_cost.artifact_bytes is None
        ):
            raise ValueError("resolved_development_frozen_bootstrap_or_variant_mismatch")
        for resources in (self.reuse.resources, *(g.resources for g in preparation.generations)):
            if (
                resources.wall_seconds > self.maximum_total_wall_seconds
                or resources.tree_rss_bytes > 12 * 1024**3
                or resources.scratch_bytes > 8 * 1024**3
                or resources.minimum_available_memory_bytes < 1024**3
                or resources.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("resolved_development_resource_ceiling")
        return self

    def cold_wall_seconds(self) -> float:
        return self.planned.generation_cost.wall_seconds + self.planning_cost.wall_seconds


class ResolvedDevelopmentPreparationJournal(CampaignJournal):
    protocol: ResolvedDevelopmentPreparationProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(event.kind not in {"reserved", "finished"} for event in self.events):
            raise ValueError("resolved_development_only_preparation_events_allowed")
        self._replay_events()
        for event in self.events:
            if (
                event.kind == "finished"
                and event.result == "completed"
                and (
                    event.cost is None
                    or event.cost.peak_process_tree_rss_bytes is None
                    or event.cost.artifact_bytes is None
                )
            ):
                raise ValueError("resolved_development_completion_requires_resource_cost")
        return self


class ReusedDevelopmentParentReceipt(Contract):
    version: Literal["ai09-reused-development-parent-receipt-1.0.0"] = (
        "ai09-reused-development-parent-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    source_recipe_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    source: PhysicalSourceSpec
    generated_receipt_sha256: Sha256
    planning_receipt_sha256: Sha256
    generation_cost: CampaignCost
    planning_cost: CampaignCost
    verified_inventories: dict[Literal["snapshot", "curated", "logical_curated"], Sha256]
    runtime: PreparationRuntime
    new_source_generation_performed: FalseFlag = False
    quality_qualified: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_cost_and_inventory(self) -> Self:
        if set(self.verified_inventories) != {"snapshot", "curated", "logical_curated"} or any(
            c.peak_process_tree_rss_bytes is None or c.artifact_bytes is None
            for c in (self.generation_cost, self.planning_cost)
        ):
            raise ValueError("reused_development_parent_complete_inventory_and_cost_required")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))
