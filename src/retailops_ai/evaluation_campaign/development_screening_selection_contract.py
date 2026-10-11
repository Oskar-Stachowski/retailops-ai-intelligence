"""Freeze the measured family-selection pass before any screening export or fit."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import FalseFlag
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTunePlan
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_profiles import DevelopmentProfileSource
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentScreeningProtocol,
)
from retailops_ai.source_snapshot.files import SnapshotError


def screening_selection_plan(
    prefix: PreparedDevelopmentScreeningProtocol,
) -> CampaignForecastTunePlan:
    return CampaignForecastTunePlan(
        source_recipe_sha256=prefix.sources[0].content_sha256(),
        export_operation_id=prefix.operations[0].operation_id,
        score_operation_ids=tuple(o.operation_id for o in prefix.operations[9:11]),
        campaign_selection_policy_sha256=prefix.selection_policy_sha256,
        forecast_quality_policy_sha256=prefix.use_case_quality_policy_sha256["forecast"],
        worker_environment_lock_sha256=prefix.scores[0].worker_environment_lock_sha256,
        policy=prefix.screening_policy,
        max_rows=prefix.scores[0].max_rows,
        resources=prefix.scores[0].resources,
    )


def screening_selection_operation(
    prefix: PreparedDevelopmentScreeningProtocol, plan: CampaignForecastTunePlan
) -> CampaignOperationPlan:
    return CampaignOperationPlan(
        operation_id="development-25-screening-selection",
        phase="development",
        action="model_score",
        use_case="forecast",
        role="tune",
        source_recipe_sha256=prefix.sources[0].content_sha256(),
        execution_recipe_sha256=plan.content_sha256(),
        prerequisites=tuple(o.operation_id for o in prefix.operations),
    )


class PreparedDevelopmentSelectionProtocol(CampaignProtocol):
    screening_selection_version: Literal["ai09-prepared-screening-selection-1.0.0"] = (
        "ai09-prepared-screening-selection-1.0.0"
    )
    screening: PreparedDevelopmentScreeningProtocol
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    selection_plan: CampaignForecastTunePlan
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=12, max_length=12)]
    maximum_new_attempts: Literal[12] = 12
    maximum_new_fit_attempts: Literal[6] = 6
    advancement_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        prefix = self.screening
        if (
            self.sources != prefix.sources
            or self.journal_path != prefix.journal_path
            or any(
                getattr(self, k) != getattr(prefix, k)
                for k in DevelopmentPlanningContext.model_fields
            )
            or self.operations[:11] != prefix.operations
            or self.selection_plan != screening_selection_plan(prefix)
            or self.operations[-1] != screening_selection_operation(prefix, self.selection_plan)
        ):
            raise ValueError("screening_selection_original_policy_budget_and_prerequisites")
        return self


class PreparedDevelopmentSelectionJournal(CampaignJournal):
    protocol: PreparedDevelopmentSelectionProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("screening_selection_no_final_freeze_or_close")
        self._replay_events()
        return self


def freeze_screening_selection(
    prefix: PreparedDevelopmentScreeningProtocol,
) -> PreparedDevelopmentSelectionProtocol:
    """Extend the uninitialized declaration; never append permission to a live journal."""
    prefix = PreparedDevelopmentScreeningProtocol.model_validate_json(prefix.model_dump_json())
    if Path(prefix.journal_path).exists():
        raise SnapshotError("screening_selection_must_be_frozen_before_journal_initialization")
    from retailops_ai.evaluation_campaign.development_screening import (
        verify_prepared_development_parents,
    )

    verify_prepared_development_parents(prefix.parents)
    plan = screening_selection_plan(prefix)
    return PreparedDevelopmentSelectionProtocol(
        **{k: getattr(prefix, k) for k in DevelopmentPlanningContext.model_fields},
        journal_path=prefix.journal_path,
        screening=prefix,
        sources=prefix.sources,
        selection_plan=plan,
        operations=(*prefix.operations, screening_selection_operation(prefix, plan)),
    )
