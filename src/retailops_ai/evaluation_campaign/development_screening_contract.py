"""First full-history screening on prepared parents, without invented generations.

This scope admits the six preregistered 25-product fits and two paired tune
passes. It cannot admit 50/100 advancement, final access or model promotion.
Those require measured selection evidence and their own frozen execution scope.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTunePolicy
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_profiles import DevelopmentProfileSource
from retailops_ai.evaluation_campaign.development_variants_contract import (
    ResolvedDevelopmentPreparationProtocol,
    ReusedDevelopmentParentReceipt,
)


class PreparedDevelopmentParents(Contract):
    """Frozen original bookkeeping; validation alone grants no Source access."""

    version: Literal["ai09-prepared-development-parents-1.0.0"] = (
        "ai09-prepared-development-parents-1.0.0"
    )
    preparation: ResolvedDevelopmentPreparationProtocol
    preparation_journal_head_sha256: Sha256
    ordinary_reuse: ReusedDevelopmentParentReceipt
    variant_generations: tuple[CampaignGeneratedParentReceipt, CampaignGeneratedParentReceipt]
    preparation_operation_costs: tuple[CampaignCost, CampaignCost, CampaignCost]
    new_generation_performed: FalseFlag = False

    @model_validator(mode="after")
    def ancestry(self) -> Self:
        p, reused = self.preparation, self.ordinary_reuse
        preparation_sha256 = p.content_sha256()
        if (
            reused.protocol_sha256 != preparation_sha256
            or reused.source_recipe_sha256 != p.sources[0].content_sha256()
            or reused.operation_id != p.operations[0].operation_id
            or reused.source != p.generated.source
            or reused.generated_receipt_sha256 != p.generated.content_sha256()
            or reused.planning_receipt_sha256 != p.planned.content_sha256()
            or reused.generation_cost != p.planned.generation_cost
            or reused.planning_cost != p.planning_cost
            or reused.runtime != p.runtime
            or any(
                c.peak_process_tree_rss_bytes is None or c.artifact_bytes is None
                for c in self.preparation_operation_costs
            )
        ):
            raise ValueError("prepared_development_original_reuse_or_cost_binding")
        for receipt, source, operation in zip(
            self.variant_generations, p.sources[1:], p.operations[1:], strict=True
        ):
            if (
                receipt.protocol_sha256 != preparation_sha256
                or receipt.source_recipe_sha256 != source.content_sha256()
                or receipt.operation_id != operation.operation_id
                or receipt.runtime != p.runtime
                or receipt.source.source_parameters
                != source.development_profile.resolved_parameters()
            ):
                raise ValueError("prepared_development_original_variant_binding")
        return self

    @property
    def generations(self) -> tuple[CampaignGeneratedParentReceipt, ...]:
        return (self.preparation.generated, *self.variant_generations)

    def cold_wall_seconds(self) -> float:
        return self.preparation.cold_wall_seconds() + sum(
            c.wall_seconds for c in self.preparation_operation_costs
        )


def screening_operations(
    sources: tuple[DevelopmentProfileSource, ...],
    exports: tuple[CampaignDevelopmentExportPlan, ...],
    fits: tuple[CampaignForecastFitPlan, ...],
    scores: tuple[CampaignForecastScorePlan, ...],
) -> tuple[CampaignOperationPlan, ...]:
    result = []
    for source, export in zip(sources, exports, strict=True):
        result.append(
            CampaignOperationPlan(
                operation_id=f"development-25-{source.variant}-export",
                phase="development",
                action="source_read",
                use_case="source",
                role="all_parent_data",
                source_recipe_sha256=source.content_sha256(),
                execution_recipe_sha256=export.content_sha256(),
            )
        )
    for i, fit in enumerate(fits):
        result.append(
            CampaignOperationPlan(
                operation_id=f"development-25-{fit.family}-trial-{i % 2 + 1}",
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
    for i, score in enumerate(scores):
        result.append(
            CampaignOperationPlan(
                operation_id=f"development-25-tune-trio-{i + 1}",
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
    return tuple(result)


class PreparedDevelopmentScreeningProtocol(CampaignProtocol):
    prepared_development_screening_version: Literal["ai09-prepared-development-screening-1.0.0"] = (
        "ai09-prepared-development-screening-1.0.0"
    )
    parents: PreparedDevelopmentParents
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    exports: Annotated[tuple[CampaignDevelopmentExportPlan, ...], Field(min_length=3, max_length=3)]
    fits: Annotated[tuple[CampaignForecastFitPlan, ...], Field(min_length=6, max_length=6)]
    scores: tuple[CampaignForecastScorePlan, CampaignForecastScorePlan]
    screening_policy: CampaignForecastTunePolicy = CampaignForecastTunePolicy()
    screening_rule: Literal[
        "per_family_tune_mean_mse_bias_coverage_gates_then_frozen_trial_order"
    ] = "per_family_tune_mean_mse_bias_coverage_gates_then_frozen_trial_order"
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=11, max_length=11)]
    maximum_new_attempts: Literal[11] = 11
    maximum_new_fit_attempts: Literal[6] = 6
    multiscale_products: tuple[Literal[25], Literal[50], Literal[100]] = (25, 50, 100)
    multiscale_recipes_per_family: tuple[Literal[2], Literal[1], Literal[1]] = (2, 1, 1)
    maximum_multiscale_fit_attempts: Literal[12] = 12
    future_recipe_rule: Literal["same_original_selected_recipe_per_family_at_50_and_100"] = (
        "same_original_selected_recipe_per_family_at_50_and_100"
    )
    failed_attempts: Literal["consume_slot_retain_cost_no_automatic_retry"] = (
        "consume_slot_retain_cost_no_automatic_retry"
    )
    advancement_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        p = self.parents.preparation
        if (
            self.sources != p.sources
            or any(s.products != 25 for s in self.sources)
            or any(
                getattr(self, k) != getattr(p, k) for k in DevelopmentPlanningContext.model_fields
            )
            or self.operations
            != screening_operations(self.sources, self.exports, self.fits, self.scores)
        ):
            raise ValueError("prepared_screening_complete_scope_and_inherited_context")
        partitions = p.bootstrap.planning.partitions
        for source, plan, generated in zip(
            self.sources, self.exports, self.parents.generations, strict=True
        ):
            if (
                plan.source_recipe_sha256 != source.content_sha256()
                or plan.generation_operation_id != generated.operation_id
                or plan.roles != partitions.roles
                or plan.label_delay_days != partitions.label_delay_days
                or plan.purge_days != partitions.purge_days
            ):
                raise ValueError("prepared_screening_original_source_and_partitions")
            plan.bind(generated.source)
        if any(
            a.model_dump(exclude={"source_recipe_sha256", "generation_operation_id"})
            != self.exports[0].model_dump(
                exclude={"source_recipe_sha256", "generation_operation_id"}
            )
            for a in self.exports[1:]
        ):
            raise ValueError("prepared_screening_equal_export_policies_required")
        if tuple(f.family for f in self.fits) != (
            "rf",
            "rf",
            "hgb",
            "hgb",
            "tensorflow",
            "tensorflow",
        ):
            raise ValueError("prepared_screening_equal_six_trial_inventory")
        for fit in self.fits:
            if (
                fit.source_recipe_sha256 != self.sources[0].content_sha256()
                or fit.export_operation_id != self.operations[0].operation_id
                or fit.initialization_seed != 42
            ):
                raise ValueError("prepared_screening_train_only_ordinary_fixed_seed")
        varying = {
            "family",
            "rf",
            "hgb",
            "epochs",
            "batch_size",
            "patience",
            "learning_rate",
            "worker_environment_lock_sha256",
        }
        if any(
            f.model_dump(exclude=varying) != self.fits[0].model_dump(exclude=varying)
            for f in self.fits[1:]
        ):
            raise ValueError("prepared_screening_equal_family_budgets_and_populations")
        for start in (0, 2, 4):
            first, second = self.fits[start : start + 2]
            fields: set[str] = (
                {first.family}
                if first.family != "tensorflow"
                else {"epochs", "batch_size", "patience", "learning_rate"}
            )
            if (
                first.worker_environment_lock_sha256 != second.worker_environment_lock_sha256
                or first.model_dump(include=fields) == second.model_dump(include=fields)
                or first.model_dump(exclude=fields) != second.model_dump(exclude=fields)
            ):
                raise ValueError("prepared_screening_distinct_recipes_equal_family_resources")
        if any(
            fit.worker_environment_lock_sha256 != score.worker_environment_lock_sha256
            for fit in self.fits
            for score in self.scores
        ):
            raise ValueError("prepared_screening_common_worker_environment_required")
        for i, score in enumerate(self.scores):
            if (
                score.role != "tune"
                or score.source_recipe_sha256 != self.sources[0].content_sha256()
                or score.export_operation_id != self.operations[0].operation_id
                or score.fit_operation_ids
                != {
                    f: self.operations[3 + offset + i].operation_id
                    for f, offset in (("rf", 0), ("hgb", 2), ("tensorflow", 4))
                }
            ):
                raise ValueError("prepared_screening_two_complete_original_tune_trios")
        if self.scores[0].model_dump(exclude={"fit_operation_ids"}) != self.scores[1].model_dump(
            exclude={"fit_operation_ids"}
        ):
            raise ValueError("prepared_screening_equal_tune_population_and_resources")
        for r in (*[f.resources for f in self.fits], *[s.resources for s in self.scores]):
            if (
                r.wall_seconds > 1200
                or r.tree_rss_bytes > 12 * 1024**3
                or r.scratch_bytes > 8 * 1024**3
                or r.minimum_available_memory_bytes < 1024**3
                or r.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("prepared_screening_frozen_resource_ceiling")
        return self


class PreparedDevelopmentScreeningJournal(CampaignJournal):
    protocol: PreparedDevelopmentScreeningProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("prepared_screening_no_selection_final_or_close_admission")
        self._replay_events()
        return self
