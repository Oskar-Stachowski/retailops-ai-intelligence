"""Preregister calibration and complete development evaluation after the original search.

This declaration precedes the first canonical export. It preserves the original
25/50 winners, parents, policies and prospective resource budgets. It cannot
extend an existing journal or authorize execution through the old eight-step
driver. The complete journal adapter must consume this declaration as a whole.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
)
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignOperationPlan,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationRecipe,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    CampaignPortfolioProtocol,
    PortfolioSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScorePlan,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
    PreparedCanonicalForecastProtocol,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_policy_freeze import FrozenDevelopmentPolicies
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError


def _recipes(
    prefix: PreparedCanonicalForecastProtocol, policies: FrozenDevelopmentPolicies
) -> tuple[
    CampaignForecastScorePlan,
    CampaignForecastCalibrationPlan,
    tuple[CampaignContextBundleRecipe, ...],
    tuple[CampaignForecastEvaluationRecipe, ...],
]:
    budget = prefix.advancement.protocol.screening.protocol.future_budgets["100"]
    score = CampaignForecastScorePlan.model_validate_json(
        prefix.score.model_copy(update={"role": "calibration"}).model_dump_json()
    )
    calibration = CampaignForecastCalibrationPlan(
        source_recipe_sha256=prefix.sources[0].content_sha256(),
        export_operation_id=prefix.operations[0].operation_id,
        tune_operation_id=prefix.operations[7].operation_id,
        score_operation_ids=("development-100-calibration-score",),
        forecast_quality_policy_sha256=policies.policies.forecast_quality.content_sha256(),
        worker_environment_lock_sha256=prefix.score.worker_environment_lock_sha256,
        resources=budget.score,
    )
    contexts = tuple(
        CampaignContextBundleRecipe(
            phase="development",
            role="development_evaluation",
            source_recipe_sha256=source.content_sha256(),
            generation_operation_id=export.generation_operation_id,
            export_operation_id=operation.operation_id,
            segment_policy=policies.policies.required_groups.segment_policy,
            resources=budget.preparation,
        )
        for source, export, operation in zip(
            prefix.sources, prefix.exports, prefix.operations[:3], strict=True
        )
    )
    evaluations = tuple(
        CampaignForecastEvaluationRecipe(
            phase="development",
            role="development_evaluation",
            source_recipe_sha256=context.source_recipe_sha256,
            export_operation_id=context.export_operation_id,
            tune_operation_id=calibration.tune_operation_id,
            calibration_operation_id="development-100-calibrate",
            tune_score_operation_ids=prefix.selection_plan.score_operation_ids,
            quality_policy=policies.policies.forecast_quality,
            segment_policy_sha256=policies.context.segment_policy_sha256,
            uncertainty_policy_sha256=policies.context.uncertainty_policy_sha256,
            worker_environment_lock_sha256=prefix.score.worker_environment_lock_sha256,
            resources=budget.score,
        )
        for context in contexts
    )
    return score, calibration, contexts, evaluations


def _operations(
    prefix: PreparedCanonicalForecastProtocol,
    score: CampaignForecastScorePlan,
    calibration: CampaignForecastCalibrationPlan,
    contexts: tuple[CampaignContextBundleRecipe, ...],
    evaluations: tuple[CampaignForecastEvaluationRecipe, ...],
) -> tuple[CampaignOperationPlan, ...]:
    operations = list(prefix.operations)
    operations.append(
        CampaignOperationPlan(
            operation_id=calibration.score_operation_ids[0],
            phase="development",
            action="model_score",
            use_case="forecast",
            role="calibration",
            source_recipe_sha256=score.source_recipe_sha256,
            execution_recipe_sha256=score.content_sha256(),
            prerequisites=(
                score.export_operation_id,
                *(score.fit_operation_ids[family] for family in FAMILIES),
                calibration.tune_operation_id,
            ),
        )
    )
    calibration_id = evaluations[0].calibration_operation_id
    operations.append(
        CampaignOperationPlan(
            operation_id=calibration_id,
            phase="development",
            action="calibrator_fit",
            use_case="forecast",
            role="calibration",
            source_recipe_sha256=calibration.source_recipe_sha256,
            execution_recipe_sha256=calibration.content_sha256(),
            prerequisites=(
                calibration.export_operation_id,
                calibration.tune_operation_id,
                *calibration.score_operation_ids,
            ),
        )
    )
    for variant, context, evaluation in zip(VARIANTS, contexts, evaluations, strict=True):
        context_id = f"development-100-{variant}-context"
        operations.extend(
            (
                CampaignOperationPlan(
                    operation_id=context_id,
                    phase="development",
                    action="source_read",
                    use_case="source",
                    role="all_parent_data",
                    source_recipe_sha256=context.source_recipe_sha256,
                    execution_recipe_sha256=context.content_sha256(),
                    prerequisites=(context.export_operation_id, calibration_id),
                ),
                CampaignOperationPlan(
                    operation_id=f"development-100-{variant}-evaluate",
                    phase="development",
                    action="model_score",
                    use_case="forecast",
                    role="development_evaluation",
                    source_recipe_sha256=evaluation.source_recipe_sha256,
                    execution_recipe_sha256=evaluation.content_sha256(),
                    prerequisites=(
                        evaluation.export_operation_id,
                        evaluation.tune_operation_id,
                        calibration_id,
                        context_id,
                    ),
                ),
            )
        )
    return tuple(operations)


class CanonicalQualificationPlan(Contract):
    """Complete prospective forecast inventory; no re-stamping of existing receipts."""

    version: Literal["ai09-canonical-qualification-plan-1.0.0"] = (
        "ai09-canonical-qualification-plan-1.0.0"
    )
    prefix: PreparedCanonicalForecastProtocol
    policies: FrozenDevelopmentPolicies
    calibration_score: CampaignForecastScorePlan
    calibration: CampaignForecastCalibrationPlan
    contexts: Annotated[tuple[CampaignContextBundleRecipe, ...], Field(min_length=3, max_length=3)]
    evaluations: Annotated[
        tuple[CampaignForecastEvaluationRecipe, ...], Field(min_length=3, max_length=3)
    ]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=16, max_length=16)]
    maximum_new_attempts: Literal[16] = 16
    maximum_new_model_fit_attempts: Literal[3] = 3
    maximum_new_calibrator_fit_attempts: Literal[1] = 1
    maximum_model_fit_attempts_all_scales: Literal[12] = 12
    authorizes_operations: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_original_scope(self) -> Self:
        if any(
            getattr(self.prefix, key) != getattr(self.policies.context, key)
            for key in DevelopmentPlanningContext.model_fields
        ):
            raise ValueError("canonical_qualification_original_frozen_policies_required")
        expected = _recipes(self.prefix, self.policies)
        if (
            self.calibration_score,
            self.calibration,
            self.contexts,
            self.evaluations,
        ) != expected or self.operations != _operations(self.prefix, *expected):
            raise ValueError("canonical_qualification_complete_original_recipes_and_budgets")
        known: set[str] = set()
        for operation in self.operations:
            if operation.operation_id in known or not set(operation.prerequisites) <= known:
                raise ValueError("canonical_qualification_unique_topological_inventory")
            known.add(operation.operation_id)
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class PreparedCanonicalQualificationProtocol(CampaignPortfolioProtocol):
    """One new journal declares exports through evaluation before its first reservation."""

    portfolio_version: Literal["ai09-canonical-qualification-portfolio-1.0.0"] = (
        "ai09-canonical-qualification-portfolio-1.0.0"  # type: ignore[assignment]
    )
    qualification: CanonicalQualificationPlan
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=3, max_length=3)]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=16, max_length=16)]
    maximum_new_attempts: Literal[16] = 16
    maximum_new_fit_attempts: Literal[4] = 4
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        prefix = self.qualification.prefix
        if (
            any(
                getattr(self, key) != getattr(prefix, key)
                for key in DevelopmentPlanningContext.model_fields
            )
            or self.journal_path != prefix.journal_path
            or self.sources != prefix.sources
            or self.operations != self.qualification.operations
            or self.training_source_recipe_sha256
            != {
                case: prefix.sources[0].content_sha256()
                for case in ("forecast", "anomaly", "stockout")
            }
        ):
            raise ValueError("canonical_qualification_original_complete_protocol_required")
        return self


class PreparedCanonicalQualificationJournal(CampaignJournal):
    protocol: PreparedCanonicalQualificationProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(event.kind not in {"reserved", "finished"} for event in self.events):
            raise ValueError("canonical_qualification_development_only_no_final_grant")
        self._replay_events()
        return self


def qualification_protocol(
    plan: CanonicalQualificationPlan,
) -> PreparedCanonicalQualificationProtocol:
    """Resolve a complete typed declaration without altering any original journal."""
    plan = CanonicalQualificationPlan.model_validate_json(plan.model_dump_json())
    prefix = plan.prefix
    return PreparedCanonicalQualificationProtocol(
        **{key: getattr(prefix, key) for key in DevelopmentPlanningContext.model_fields},
        qualification=plan,
        journal_path=prefix.journal_path,
        sources=prefix.sources,
        operations=plan.operations,
        training_source_recipe_sha256={
            case: prefix.sources[0].content_sha256() for case in ("forecast", "anomaly", "stockout")
        },
    )


def compile_canonical_qualification(
    *,
    advancement: CompletedForecastAdvancement,
    parents: PreparedCanonicalDevelopmentParents,
    policies: FrozenDevelopmentPolicies,
) -> CanonicalQualificationPlan:
    """Compile before canonical execution, using verified original 25/50 and Source costs."""
    from retailops_ai.evaluation_campaign.canonical_forecast import compile_canonical_forecast

    policies = FrozenDevelopmentPolicies.model_validate_json(policies.model_dump_json())
    if policies.context.runtime != runtime_pin():
        raise SnapshotError("canonical_qualification_runtime_changed")
    prefix = compile_canonical_forecast(advancement=advancement, parents=parents)
    recipes = _recipes(prefix, policies)
    result = CanonicalQualificationPlan(
        prefix=prefix,
        policies=policies,
        calibration_score=recipes[0],
        calibration=recipes[1],
        contexts=recipes[2],
        evaluations=recipes[3],
        operations=_operations(prefix, *recipes),
    )
    path = Path(prefix.journal_path)
    if path.exists() or path.is_symlink():
        raise SnapshotError("canonical_qualification_cannot_extend_existing_journal")
    return result
