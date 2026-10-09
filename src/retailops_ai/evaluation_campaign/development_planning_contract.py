"""Ordinary Source bootstrap before native scenario IDs exist; no trial grant."""

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    FalseFlag,
    Sha256,
    Symbol,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfile,
    DevelopmentProfilePreparation,
    DevelopmentProfileSource,
)
from retailops_ai.evaluation_campaign.legacy_carryover import LegacyCampaignCarryover
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy


class DevelopmentPlanningContext(Contract):
    """Existing costs and prospective policy pins, without invented final plans."""

    legacy: LegacyCampaignCarryover
    legacy_sha256: Sha256
    runtime: PreparationRuntime
    selection_policy_sha256: Sha256
    use_case_quality_policy_sha256: dict[Literal["forecast", "anomaly", "stockout"], Sha256]
    segment_policy_sha256: Sha256
    uncertainty_policy_sha256: Sha256
    seed_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]
    scenario_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]


class NativeDevelopmentPlanningPlan(Contract):
    version: Literal["ai09-native-development-planning-plan-1.0.0"] = (
        "ai09-native-development-planning-plan-1.0.0"
    )
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    partitions: ForecastPartitionPolicy
    producer_planner_sha256: Sha256
    producer_planner_version: Literal["ai09-native-development-scenario-selection-1.0.0"] = (
        "ai09-native-development-scenario-selection-1.0.0"
    )
    resources: CampaignGenerationResources
    max_bundle_bytes: Annotated[int, Field(ge=1024, le=2 * 1024**2)] = 2 * 1024**2
    exposure: Literal["complete_ordinary_parent_read_before_any_model_trial"] = (
        "complete_ordinary_parent_read_before_any_model_trial"
    )
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))

    def scenario_roles(self) -> list[dict[str, str]]:
        return [
            {
                "role": r.role,
                "start_date": r.origins.start.isoformat(),
                "end_date": r.origins.end.isoformat(),
            }
            for r in self.partitions.roles[2:]
        ]


def planning_operations(
    source: DevelopmentProfileSource,
    generation: CampaignGenerationPlan,
    planning: NativeDevelopmentPlanningPlan,
) -> tuple[CampaignOperationPlan, ...]:
    common = dict(
        phase="development",
        use_case="source",
        role="all_parent_data",
        source_recipe_sha256=source.content_sha256(),
    )
    return (
        CampaignOperationPlan.model_validate(
            {
                **common,
                "action": "source_generate",
                "operation_id": planning.generation_operation_id,
                "execution_recipe_sha256": generation.content_sha256(),
            }
        ),
        CampaignOperationPlan.model_validate(
            {
                **common,
                "action": "source_read",
                "operation_id": f"development-{source.products}-native-scenario-plan",
                "execution_recipe_sha256": planning.content_sha256(),
                "prerequisites": (planning.generation_operation_id,),
            }
        ),
    )


class NativeDevelopmentPlanningProtocol(CampaignProtocol):
    native_development_planning_version: Literal["ai09-native-development-planning-1.0.0"] = (
        "ai09-native-development-planning-1.0.0"
    )
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=1, max_length=1)]
    generation: CampaignGenerationPlan
    planning: NativeDevelopmentPlanningPlan
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=2, max_length=2)]
    maximum_new_attempts: Literal[2] = 2
    maximum_new_fit_attempts: Literal[0] = 0
    maximum_total_wall_seconds: Annotated[int, Field(ge=2, le=10800)] = 10800

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        source = self.sources[0]
        self.generation.bind(source)
        profile = source.development_profile
        if (
            source.variant != "ordinary"
            or self.generation.requested_parameters != profile.requested_parameters()
            or self.planning.source_recipe_sha256 != source.content_sha256()
            or self.planning.partitions.label_delay_days != source.label_delay_days
            or self.operations != planning_operations(source, self.generation, self.planning)
        ):
            raise ValueError("native_planning_frozen_ordinary_source_or_operation_mismatch")
        for resources in (self.generation.resources, self.planning.resources):
            if (
                resources.wall_seconds > self.maximum_total_wall_seconds
                or resources.tree_rss_bytes > 12 * 1024**3
                or resources.scratch_bytes > 8 * 1024**3
                or resources.minimum_available_memory_bytes < 1024**3
                or resources.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("native_planning_resource_ceiling")
        for role in self.planning.partitions.roles:
            if (
                role.origins.start < profile.history.start + timedelta(days=28)
                or role.label_knowledge_cutoff > end_of_day(profile.history.end)
                or (
                    role.role in {"tune", "calibration", "development_evaluation"}
                    and (role.origins.end - role.origins.start).days < 29
                )
            ):
                raise ValueError("native_planning_exposed_development_roles_required")
        return self


class NativeDevelopmentPlanningJournal(CampaignJournal):
    protocol: NativeDevelopmentPlanningProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(event.kind not in {"reserved", "finished"} for event in self.events):
            raise ValueError("native_planning_only_preparation_events_allowed")
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
                raise ValueError("native_planning_completion_requires_resource_cost")
        return self


class NativeDevelopmentPlanningReceipt(Contract):
    version: Literal["ai09-native-development-planning-receipt-1.0.0"] = (
        "ai09-native-development-planning-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    source_recipe_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    source_dataset_id: Annotated[str, Field(pattern=r"^source-sha256-[0-9a-f]{64}$")]
    generation_receipt_sha256: Sha256
    generation_cost: CampaignCost
    planning_sha256: Sha256
    producer_planner_sha256: Sha256
    bundle_sha256: Sha256
    bundle_bytes: Annotated[int, Field(ge=1, le=2 * 1024**2)]
    plan_sha256: dict[Literal["demand", "physical"], Sha256]
    runtime: PreparationRuntime
    realized_effects_verified: FalseFlag = False
    critical_coverage_verified: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_evidence(self) -> Self:
        if (
            set(self.plan_sha256) != {"demand", "physical"}
            or self.generation_cost.peak_process_tree_rss_bytes is None
            or self.generation_cost.artifact_bytes is None
        ):
            raise ValueError("native_planning_requires_both_plans_and_cold_generation_cost")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class ResolvedNativeDevelopmentPlanningReceipt(NativeDevelopmentPlanningReceipt):
    """Resolve executable plans within the already charged native planning read.

    The original v31 receipt wire remains unchanged. This v32 extension carries
    only generation recipes, so the next protocol can freeze them without
    reopening the truth-bearing native bundle before reserving another read.
    """

    resolved_preparation_version: Literal["ai09-resolved-development-preparation-1.0.0"] = (
        "ai09-resolved-development-preparation-1.0.0"
    )
    preparation: DevelopmentProfilePreparation

    @model_validator(mode="after")
    def native_plan_binding(self) -> Self:
        if (
            self.preparation.sources[0].content_sha256() != self.source_recipe_sha256
            or {s.variant: s.scenario_plan_sha256 for s in self.preparation.sources[1:]}
            != self.plan_sha256
        ):
            raise ValueError("native_planning_resolved_preparation_binding_mismatch")
        return self


def parse_native_planning_receipt(
    receipt: NativeDevelopmentPlanningReceipt,
) -> NativeDevelopmentPlanningReceipt:
    model = (
        ResolvedNativeDevelopmentPlanningReceipt
        if isinstance(receipt, ResolvedNativeDevelopmentPlanningReceipt)
        else NativeDevelopmentPlanningReceipt
    )
    return model.model_validate_json(receipt.model_dump_json())


def compile_native_development_planning(
    journal_path: Path,
    *,
    context: DevelopmentPlanningContext,
    profile: DevelopmentProfile,
    producer_commit: CommitSha,
    producer_lock_sha256: Sha256,
    exporter_lock_sha256: Sha256,
    producer_planner_sha256: Sha256,
    partitions: ForecastPartitionPolicy,
    generation_resources: CampaignGenerationResources,
    planning_resources: CampaignGenerationResources,
    maximum_total_wall_seconds: int = 10800,
) -> NativeDevelopmentPlanningProtocol:
    """Freeze one ordinary generation and one native read, without any Source I/O."""
    source = DevelopmentProfileSource(
        phase="development",
        seed=42,
        producer_commit=producer_commit,
        producer_lock_sha256=producer_lock_sha256,
        exporter_lock_sha256=exporter_lock_sha256,
        generation_config_sha256=canonical_sha256(profile.resolved_parameters()),
        profile="ai-dev",
        history=profile.history,
        products=profile.products,
        selling_pairs=profile.selling_pairs,
        stock_locations=profile.stock_locations,
        label_delay_days=partitions.label_delay_days,
        variant="ordinary",
        scenario_plan_sha256=None,
        development_profile=profile,
    )
    generation = CampaignGenerationPlan(
        source_recipe_sha256=source.content_sha256(),
        exporter_lock_sha256=exporter_lock_sha256,
        requested_parameters=profile.requested_parameters(),
        resolved_parameters=profile.resolved_parameters(),
        entrypoint="cached_inventory_v2",
        snapshot_schema_version="1.1.0",
        resources=generation_resources,
    )
    planning = NativeDevelopmentPlanningPlan(
        source_recipe_sha256=source.content_sha256(),
        generation_operation_id=f"development-{profile.products}-ordinary-generate",
        partitions=partitions,
        producer_planner_sha256=producer_planner_sha256,
        resources=planning_resources,
    )
    return NativeDevelopmentPlanningProtocol.model_validate_json(
        canonical_bytes(
            {
                **context.model_dump(mode="json"),
                "journal_path": str(journal_path.absolute()),
                "sources": [source.model_dump(mode="json")],
                "generation": generation.model_dump(mode="json"),
                "planning": planning.model_dump(mode="json"),
                "operations": [
                    o.model_dump(mode="json")
                    for o in planning_operations(source, generation, planning)
                ],
                "maximum_total_wall_seconds": maximum_total_wall_seconds,
            }
        )
    )
