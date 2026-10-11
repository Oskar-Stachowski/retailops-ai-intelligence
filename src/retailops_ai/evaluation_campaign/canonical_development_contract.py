"""Original canonical development sources, prepared by the same pinned native planner.

These additive contracts retain the original 100-product PortfolioSourceRecipe
wire. Small development profiles remain restricted to 25 and 50 products.
"""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, DateWindow, FalseFlag, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    PortfolioSourceRecipe,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
    NativeDevelopmentPlanningPlan,
    NativeDevelopmentPlanningReceipt,
    planning_operations,
    validate_native_planning_scope,
)
from retailops_ai.evaluation_campaign.development_profiles import validate_preparation_recipes
from retailops_ai.evaluation_campaign.development_screening_contract import (
    validate_prepared_parent_scope,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    OrdinaryDevelopmentReusePlan,
    ReusedDevelopmentParentReceipt,
    validate_resolved_variant_scope,
)
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy

HISTORY = DateWindow(start=date(2025, 8, 1), end=date(2026, 7, 31))


def canonical_requested_parameters() -> dict[str, JsonValue]:
    return {
        "profile": "ai-dev",
        "seed": 42,
        "days": 365,
        "products": 100,
        "stores": 5,
        "warehouses": 3,
        "start_date": HISTORY.start.isoformat(),
        "end_date": HISTORY.end.isoformat(),
        "max_daily_rows": 182500,
        "forecast_plan_days": 14,
    }


def canonical_resolved_parameters() -> dict[str, JsonValue]:
    return canonical_requested_parameters() | {
        "business_timezone": "UTC",
        "output_format": "csv",
        "warmup_days": 0,
        "origin_days": 0,
        "label_tail_days": 0,
        "forecast_plan_version": "known-forecast-plans-1.0.0",
    }


class CanonicalDevelopmentPreparation(Contract):
    version: Literal["ai09-canonical-development-preparation-1.0.0"] = (
        "ai09-canonical-development-preparation-1.0.0"
    )
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=3, max_length=3)]
    generations: Annotated[tuple[CampaignGenerationPlan, ...], Field(min_length=3, max_length=3)]
    actual_coverage_verified: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_variants(self) -> Self:
        if tuple(s.variant for s in self.sources) != VARIANTS or any(
            s.phase != "development"
            or s.history != HISTORY
            or s.generation_config_sha256 != canonical_sha256(canonical_resolved_parameters())
            for s in self.sources
        ):
            raise ValueError("canonical_development_original_full_source_inventory_required")
        validate_preparation_recipes(
            self.sources,
            self.generations,
            requested_parameters=canonical_requested_parameters(),
            history=HISTORY,
            data_seed=42,
        )
        return self


def prepare_canonical_development(
    *,
    source: PortfolioSourceRecipe,
    generation: CampaignGenerationPlan,
    scenario_plans: dict[Literal["demand", "physical"], dict[str, JsonValue]],
) -> CanonicalDevelopmentPreparation:
    """Keep the completed ordinary plan and derive the two original native variants."""
    source = PortfolioSourceRecipe.model_validate_json(source.model_dump_json())
    generation = CampaignGenerationPlan.model_validate_json(generation.model_dump_json())
    if source.variant != "ordinary" or set(scenario_plans) != {"demand", "physical"}:
        raise ValueError("canonical_development_original_ordinary_and_both_native_plans_required")
    sources = [source]
    generations = [generation]
    for variant in ("demand", "physical"):
        scenario = scenario_plans[variant]
        planned_source = PortfolioSourceRecipe.model_validate_json(
            source.model_copy(
                update={"variant": variant, "scenario_plan_sha256": canonical_sha256(scenario)}
            ).model_dump_json()
        )
        sources.append(planned_source)
        generations.append(
            CampaignGenerationPlan.model_validate_json(
                generation.model_copy(
                    update={
                        "source_recipe_sha256": planned_source.content_sha256(),
                        "entrypoint": "planned_anomaly",
                        "scenario_plan": scenario,
                        "snapshot_schema_version": "1.2.0",
                        "required_use_cases": (
                            "forecast_source",
                            "inventory_source",
                            "anomaly_source",
                        ),
                    }
                ).model_dump_json()
            )
        )
    return CanonicalDevelopmentPreparation(sources=tuple(sources), generations=tuple(generations))


class CanonicalDevelopmentPlanningProtocol(CampaignProtocol):
    canonical_development_planning_version: Literal["ai09-canonical-development-planning-1.0.0"] = (
        "ai09-canonical-development-planning-1.0.0"
    )
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=1, max_length=1)]
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
        if (
            source.phase != "development"
            or source.history != HISTORY
            or source.generation_config_sha256 != canonical_sha256(canonical_resolved_parameters())
            or self.generation.resolved_parameters != canonical_resolved_parameters()
        ):
            raise ValueError("canonical_development_full_frozen_source_parameters_required")
        validate_native_planning_scope(
            source,
            self.generation,
            self.planning,
            self.operations,
            requested_parameters=canonical_requested_parameters(),
            history=HISTORY,
            maximum_total_wall_seconds=self.maximum_total_wall_seconds,
        )
        return self


class CanonicalDevelopmentPlanningJournal(CampaignJournal):
    protocol: CanonicalDevelopmentPlanningProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("canonical_development_only_preparation_events_allowed")
        self._replay_events()
        if any(
            e.kind == "finished"
            and e.result == "completed"
            and (
                e.cost is None
                or e.cost.peak_process_tree_rss_bytes is None
                or e.cost.artifact_bytes is None
            )
            for e in self.events
        ):
            raise ValueError("canonical_development_completion_requires_measured_cost")
        return self


class ResolvedCanonicalDevelopmentPlanningReceipt(NativeDevelopmentPlanningReceipt):
    resolved_canonical_preparation_version: Literal[
        "ai09-resolved-canonical-development-preparation-1.0.0"
    ] = "ai09-resolved-canonical-development-preparation-1.0.0"
    preparation: CanonicalDevelopmentPreparation

    @model_validator(mode="after")
    def native_plan_binding(self) -> Self:
        if (
            self.preparation.sources[0].content_sha256() != self.source_recipe_sha256
            or {s.variant: s.scenario_plan_sha256 for s in self.preparation.sources[1:]}
            != self.plan_sha256
        ):
            raise ValueError("canonical_development_original_native_plan_binding_mismatch")
        return self


class ResolvedCanonicalDevelopmentPreparationProtocol(CampaignProtocol):
    resolved_canonical_preparation_version: Literal[
        "ai09-resolved-canonical-development-variants-1.0.0"
    ] = "ai09-resolved-canonical-development-variants-1.0.0"
    bootstrap: CanonicalDevelopmentPlanningProtocol
    generated: CampaignGeneratedParentReceipt
    planned: ResolvedCanonicalDevelopmentPlanningReceipt
    planning_cost: CampaignCost
    reuse: OrdinaryDevelopmentReusePlan
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=3, max_length=3)]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=3, max_length=3)]
    maximum_new_attempts: Literal[3] = 3
    maximum_new_fit_attempts: Literal[0] = 0
    maximum_total_wall_seconds: Annotated[int, Field(ge=2, le=10800)] = 10800

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        validate_resolved_variant_scope(self)
        return self

    def cold_wall_seconds(self) -> float:
        return self.planned.generation_cost.wall_seconds + self.planning_cost.wall_seconds


class ResolvedCanonicalDevelopmentPreparationJournal(CampaignJournal):
    protocol: ResolvedCanonicalDevelopmentPreparationProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("canonical_development_only_preparation_events_allowed")
        self._replay_events()
        if any(
            e.kind == "finished"
            and e.result == "completed"
            and (
                e.cost is None
                or e.cost.peak_process_tree_rss_bytes is None
                or e.cost.artifact_bytes is None
            )
            for e in self.events
        ):
            raise ValueError("canonical_development_completion_requires_measured_cost")
        return self


class PreparedCanonicalDevelopmentParents(Contract):
    version: Literal["ai09-prepared-canonical-development-parents-1.0.0"] = (
        "ai09-prepared-canonical-development-parents-1.0.0"
    )
    preparation: ResolvedCanonicalDevelopmentPreparationProtocol
    preparation_journal_head_sha256: Sha256
    ordinary_reuse: ReusedDevelopmentParentReceipt
    variant_generations: tuple[CampaignGeneratedParentReceipt, CampaignGeneratedParentReceipt]
    preparation_operation_costs: tuple[CampaignCost, CampaignCost, CampaignCost]
    new_generation_performed: FalseFlag = False

    @model_validator(mode="after")
    def ancestry(self) -> Self:
        validate_prepared_parent_scope(self)
        return self

    @property
    def generations(self) -> tuple[CampaignGeneratedParentReceipt, ...]:
        return (self.preparation.generated, *self.variant_generations)

    def cold_wall_seconds(self) -> float:
        return self.preparation.cold_wall_seconds() + sum(
            c.wall_seconds for c in self.preparation_operation_costs
        )


def compile_canonical_development_planning(
    journal_path: Path,
    *,
    context: DevelopmentPlanningContext,
    producer_commit: CommitSha,
    producer_lock_sha256: Sha256,
    exporter_lock_sha256: Sha256,
    producer_planner_sha256: Sha256,
    partitions: ForecastPartitionPolicy,
    generation_resources: CampaignGenerationResources,
    planning_resources: CampaignGenerationResources,
    maximum_total_wall_seconds: int = 10800,
) -> CanonicalDevelopmentPlanningProtocol:
    source = PortfolioSourceRecipe(
        phase="development",
        seed=42,
        producer_commit=producer_commit,
        producer_lock_sha256=producer_lock_sha256,
        exporter_lock_sha256=exporter_lock_sha256,
        generation_config_sha256=canonical_sha256(canonical_resolved_parameters()),
        profile="ai-dev",
        history=HISTORY,
        products=100,
        selling_pairs=5,
        stock_locations=3,
        label_delay_days=partitions.label_delay_days,
        variant="ordinary",
        scenario_plan_sha256=None,
    )
    generation = CampaignGenerationPlan(
        source_recipe_sha256=source.content_sha256(),
        exporter_lock_sha256=exporter_lock_sha256,
        requested_parameters=canonical_requested_parameters(),
        resolved_parameters=canonical_resolved_parameters(),
        entrypoint="cached_inventory_v2",
        snapshot_schema_version="1.1.0",
        resources=generation_resources,
    )
    planning = NativeDevelopmentPlanningPlan(
        source_recipe_sha256=source.content_sha256(),
        generation_operation_id="development-100-ordinary-generate",
        partitions=partitions,
        producer_planner_sha256=producer_planner_sha256,
        resources=planning_resources,
    )
    return CanonicalDevelopmentPlanningProtocol(
        **context.model_dump(),
        journal_path=str(journal_path.absolute()),
        sources=(source,),
        generation=generation,
        planning=planning,
        operations=planning_operations(source, generation, planning),
        maximum_total_wall_seconds=maximum_total_wall_seconds,
    )
