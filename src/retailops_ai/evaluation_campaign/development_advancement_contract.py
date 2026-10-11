"""Freeze future resource/path budgets before the six screening fits.

Each later scale has one original journal path, so a failed fit cannot regain
its slot by compiling another destination. Native scenario IDs are resolved by
the original pinned planner, never selected using model outcomes.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignDevelopmentExportPlan
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan, Family
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastTunePlan,
    CampaignForecastTuneReceipt,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_profiles import DevelopmentProfileSource
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentParents,
)
from retailops_ai.evaluation_campaign.development_screening_selection import ScreeningFamilyRanking
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    PreparedDevelopmentSelectionProtocol,
)

PrivatePath = Annotated[str, Field(pattern=r"^/", min_length=2, max_length=1024)]


class ForecastScaleBudget(Contract):
    bootstrap_journal_path: PrivatePath
    preparation_journal_path: PrivatePath
    execution_journal_path: PrivatePath
    preparation: CampaignGenerationResources
    fit: CampaignGenerationResources
    score: CampaignGenerationResources
    maximum_total_preparation_wall_seconds: Annotated[int, Field(ge=2, le=10800)]

    def paths(self) -> tuple[str, ...]:
        return (
            self.bootstrap_journal_path,
            self.preparation_journal_path,
            self.execution_journal_path,
        )

    @model_validator(mode="after")
    def ceilings(self) -> Self:
        if len(set(self.paths())) != 3 or any(str(Path(p).resolve()) != p for p in self.paths()):
            raise ValueError("advancement_requires_distinct_canonical_journal_paths")
        for resources in (self.preparation, self.fit, self.score):
            if (
                resources.tree_rss_bytes > 12 * 1024**3
                or resources.scratch_bytes > 8 * 1024**3
                or resources.minimum_available_memory_bytes < 1024**3
                or resources.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("advancement_resource_ceiling_or_reserve")
        if (
            self.preparation.wall_seconds > self.maximum_total_preparation_wall_seconds
            or self.fit.wall_seconds > 1200
            or self.score.wall_seconds > 1200
        ):
            raise ValueError("advancement_wall_budget_exceeded")
        return self


class MultiscaleSelectionProtocol(PreparedDevelopmentSelectionProtocol):
    multiscale_selection_version: Literal["ai09-multiscale-selection-1.0.0"] = (
        "ai09-multiscale-selection-1.0.0"
    )
    future_budgets: dict[Literal["50", "100"], ForecastScaleBudget]
    future_data_rule: Literal[
        "same_pinned_native_planner_partitions_full_history_and_original_model_configuration"
    ] = "same_pinned_native_planner_partitions_full_history_and_original_model_configuration"

    @model_validator(mode="after")
    def future_scope(self) -> Self:
        if set(self.future_budgets) != {"50", "100"}:
            raise ValueError("advancement_both_future_scale_budgets_required")
        paths = [p for budget in self.future_budgets.values() for p in budget.paths()]
        original = self.screening.parents.preparation
        if len(set(paths)) != 6 or set(paths) & {
            self.journal_path,
            original.journal_path,
            original.bootstrap.journal_path,
        }:
            raise ValueError("advancement_journals_cannot_reuse_original_or_other_scale_paths")
        return self


class MultiscaleSelectionJournal(CampaignJournal):
    protocol: MultiscaleSelectionProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("multiscale_screening_no_final_freeze_or_close")
        self._replay_events()
        return self


class CompletedForecastScreening(Contract):
    """Original, independently rechecked screening receipt and complete costs."""

    protocol: MultiscaleSelectionProtocol
    journal_head_sha256: Sha256
    selection_bundle: PrivatePath
    selected: CampaignForecastTuneReceipt
    ranking: ScreeningFamilyRanking
    operation_costs: Annotated[tuple[CampaignCost, ...], Field(min_length=12, max_length=12)]

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.selected.protocol_sha256 != self.protocol.content_sha256()
            or self.selected.plan != self.protocol.selection_plan
            or self.selected.operation_id != self.protocol.operations[-1].operation_id
            or self.ranking.status != "three_families_ranked"
            or any(
                c.peak_process_tree_rss_bytes is None or c.artifact_bytes is None
                for c in self.operation_costs
            )
        ):
            raise ValueError("advancement_original_qualified_screening_and_measured_costs_required")
        return self

    def cold_wall_seconds(self) -> float:
        return self.protocol.screening.parents.cold_wall_seconds() + sum(
            c.wall_seconds for c in self.operation_costs
        )


def advancement_plans(
    screening: CompletedForecastScreening, parents: PreparedDevelopmentParents
) -> tuple[
    tuple[CampaignDevelopmentExportPlan, ...],
    tuple[CampaignForecastFitPlan, ...],
    CampaignForecastScorePlan,
    CampaignForecastTunePlan,
]:
    """Rebind data identities and only the resource budgets frozen before fits."""
    prefix = screening.protocol.screening
    resources = screening.protocol.future_budgets["50"]
    source = parents.preparation.sources[0]
    export_id = "development-50-ordinary-export"
    exports = tuple(
        original.model_copy(
            update={
                "source_recipe_sha256": s.content_sha256(),
                "generation_operation_id": g.operation_id,
            }
        )
        for original, s, g in zip(
            prefix.exports, parents.preparation.sources, parents.generations, strict=True
        )
    )
    originals = {
        o.operation_id: plan for o, plan in zip(prefix.operations[3:9], prefix.fits, strict=True)
    }
    fits = []
    for family in ("rf", "hgb", "tensorflow"):
        winner = screening.ranking.winners[family]
        if winner is None:
            raise ValueError("advancement_missing_original_family_winner")
        original = originals[winner.fit_operation_id]
        if original.family != family:
            raise ValueError("advancement_winner_family_changed")
        fits.append(
            original.model_copy(
                update={
                    "source_recipe_sha256": source.content_sha256(),
                    "export_operation_id": export_id,
                    "resources": resources.fit,
                }
            )
        )
    score = prefix.scores[0].model_copy(
        update={
            "source_recipe_sha256": source.content_sha256(),
            "export_operation_id": export_id,
            "fit_operation_ids": {
                f: f"development-50-{f}-fit" for f in ("rf", "hgb", "tensorflow")
            },
            "resources": resources.score,
        }
    )
    tune = screening.protocol.selection_plan.model_copy(
        update={
            "source_recipe_sha256": source.content_sha256(),
            "export_operation_id": export_id,
            "score_operation_ids": ("development-50-tune-trio",),
            "resources": resources.score,
        }
    )
    return exports, tuple(fits), score, tune


def advancement_operations(
    sources: tuple[DevelopmentProfileSource, ...],
    exports: tuple[CampaignDevelopmentExportPlan, ...],
    fits: tuple[CampaignForecastFitPlan, ...],
    score: CampaignForecastScorePlan,
    tune: CampaignForecastTunePlan,
) -> tuple[CampaignOperationPlan, ...]:
    operations = [
        CampaignOperationPlan(
            operation_id=f"development-50-{s.variant}-export",
            phase="development",
            action="source_read",
            use_case="source",
            role="all_parent_data",
            source_recipe_sha256=s.content_sha256(),
            execution_recipe_sha256=p.content_sha256(),
        )
        for s, p in zip(sources, exports, strict=True)
    ]
    for fit in fits:
        operations.append(
            CampaignOperationPlan(
                operation_id=score.fit_operation_ids[fit.family],
                phase="development",
                action="model_fit",
                use_case="forecast",
                role="train",
                source_recipe_sha256=fit.source_recipe_sha256,
                execution_recipe_sha256=fit.content_sha256(),
                forecast_family=fit.family,
                initialization_seed=fit.initialization_seed,
                prerequisites=(fit.export_operation_id,),
            )
        )
    operations.append(
        CampaignOperationPlan(
            operation_id=tune.score_operation_ids[0],
            phase="development",
            action="model_score",
            use_case="forecast",
            role="tune",
            source_recipe_sha256=score.source_recipe_sha256,
            execution_recipe_sha256=score.content_sha256(),
            prerequisites=(
                score.export_operation_id,
                *(score.fit_operation_ids[f] for f in ("rf", "hgb", "tensorflow")),
            ),
        )
    )
    operations.append(
        CampaignOperationPlan(
            operation_id="development-50-tune-selection",
            phase="development",
            action="model_score",
            use_case="forecast",
            role="tune",
            source_recipe_sha256=tune.source_recipe_sha256,
            execution_recipe_sha256=tune.content_sha256(),
            prerequisites=tuple(o.operation_id for o in operations),
        )
    )
    return tuple(operations)


class PreparedForecastAdvancementProtocol(CampaignProtocol):
    forecast_advancement_version: Literal["ai09-prepared-forecast-advancement-1.0.0"] = (
        "ai09-prepared-forecast-advancement-1.0.0"
    )
    screening: CompletedForecastScreening
    parents: PreparedDevelopmentParents
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    exports: Annotated[tuple[CampaignDevelopmentExportPlan, ...], Field(min_length=3, max_length=3)]
    fits: Annotated[tuple[CampaignForecastFitPlan, ...], Field(min_length=3, max_length=3)]
    score: CampaignForecastScorePlan
    selection_plan: CampaignForecastTunePlan
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=8, max_length=8)]
    maximum_new_attempts: Literal[8] = 8
    maximum_new_fit_attempts: Literal[3] = 3
    canonical_advancement_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        p = self.parents.preparation
        original = self.screening.protocol.screening.parents.preparation
        budget = self.screening.protocol.future_budgets["50"]
        if (
            self.journal_path != budget.execution_journal_path
            or p.journal_path != budget.preparation_journal_path
            or p.bootstrap.journal_path != budget.bootstrap_journal_path
            or self.sources != p.sources
            or any(s.products != 50 for s in self.sources)
            or any(
                getattr(self, k) != getattr(p, k) or getattr(self, k) != getattr(original, k)
                for k in DevelopmentPlanningContext.model_fields
            )
            or p.bootstrap.planning.partitions != original.bootstrap.planning.partitions
            or p.bootstrap.planning.producer_planner_sha256
            != original.bootstrap.planning.producer_planner_sha256
            or any(
                getattr(s, k) != getattr(original.sources[0], k)
                for s in self.sources
                for k in (
                    "producer_commit",
                    "producer_lock_sha256",
                    "exporter_lock_sha256",
                    "label_delay_days",
                    "history",
                    "seed",
                    "selling_pairs",
                    "stock_locations",
                )
            )
            or any(
                r != budget.preparation
                for r in (
                    p.reuse.resources,
                    p.bootstrap.generation.resources,
                    p.bootstrap.planning.resources,
                    *(g.resources for g in p.planned.preparation.generations),
                )
            )
            or p.maximum_total_wall_seconds != budget.maximum_total_preparation_wall_seconds
            or p.bootstrap.maximum_total_wall_seconds
            != budget.maximum_total_preparation_wall_seconds
            or (self.exports, self.fits, self.score, self.selection_plan)
            != advancement_plans(self.screening, self.parents)
            or self.operations
            != advancement_operations(
                self.sources, self.exports, self.fits, self.score, self.selection_plan
            )
        ):
            raise ValueError(
                "advancement_original_configuration_parents_policy_budget_or_path_changed"
            )
        return self


class PreparedForecastAdvancementJournal(CampaignJournal):
    protocol: PreparedForecastAdvancementProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("advancement_no_final_freeze_or_close")
        self._replay_events()
        return self


class CompletedForecastAdvancement(Contract):
    protocol: PreparedForecastAdvancementProtocol
    journal_head_sha256: Sha256
    selection_bundle: PrivatePath
    selected: CampaignForecastTuneReceipt
    operation_costs: Annotated[tuple[CampaignCost, ...], Field(min_length=8, max_length=8)]
    status: Literal["original_families_verified", "not_ready"]
    family_reasons: dict[Family, tuple[str, ...]]
    reasons: tuple[str, ...]
    canonical_execution_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.selected.protocol_sha256 != self.protocol.content_sha256()
            or self.selected.plan != self.protocol.selection_plan
            or self.selected.operation_id != self.protocol.operations[-1].operation_id
            or set(self.family_reasons) != {"rf", "hgb", "tensorflow"}
            or self.reasons
            != (
                *self.selected.selection.reasons,
                *(
                    "invalid_" + f + "_family"
                    for f in ("rf", "hgb", "tensorflow")
                    if self.family_reasons[f]
                ),
            )
            or (self.status == "original_families_verified") != (not self.reasons)
            or any(
                c.peak_process_tree_rss_bytes is None or c.artifact_bytes is None
                for c in self.operation_costs
            )
        ):
            raise ValueError("advancement_completed_result_binding_or_measured_costs")
        return self

    def cold_wall_seconds(self) -> float:
        return (
            self.protocol.screening.cold_wall_seconds()
            + self.protocol.parents.cold_wall_seconds()
            + sum(c.wall_seconds for c in self.operation_costs)
        )
