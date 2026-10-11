"""Freeze the complete original development program before its first Source operation."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.development_search import ForecastSearchRecipe
from retailops_ai.evaluation_campaign.source_preparation_contract import SourcePreparationOptions


class ForecastWorkflowResources(Contract):
    fit: CampaignGenerationResources
    score: CampaignGenerationResources

    @model_validator(mode="after")
    def original_limits(self) -> Self:
        for resource in (self.fit, self.score):
            if (
                resource.wall_seconds > 1200
                or resource.tree_rss_bytes > 12 * 1024**3
                or resource.scratch_bytes > 8 * 1024**3
                or resource.minimum_available_memory_bytes < 1024**3
                or resource.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("development_workflow_original_model_resource_limits")
        return self


class DevelopmentWorkflowOptions(Contract):
    version: Literal["ai09-original-development-workflow-options-1.0.0"] = (
        "ai09-original-development-workflow-options-1.0.0"
    )
    source: SourcePreparationOptions
    worker_python: str
    worker_environment_lock_sha256: Sha256
    recipes: Annotated[tuple[ForecastSearchRecipe, ...], Field(min_length=6, max_length=6)]
    resources: dict[Literal["25", "50", "100"], ForecastWorkflowResources]
    maximum_source_operations: Literal[15] = 15
    maximum_forecast_operations: Literal[28] = 28
    maximum_fit_attempts: Literal[12] = 12
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def original_program(self) -> Self:
        if (
            self.source.products != 25
            or self.source.generation_resources != self.source.planning_resources
            or self.source.generation_resources != self.source.reuse_resources
            or set(self.resources) != {"25", "50", "100"}
            or not Path(self.worker_python).is_absolute()
            or str(Path(self.worker_python)) != self.worker_python
            or ".." in Path(self.worker_python).parts
        ):
            raise ValueError("development_workflow_complete_original_scope_required")
        if (
            tuple(r.family for r in self.recipes)
            != ("rf", "rf", "hgb", "hgb", "tensorflow", "tensorflow")
            or len({r.name for r in self.recipes}) != 6
        ):
            raise ValueError("development_workflow_equal_six_trial_inventory")
        for first, second in zip(self.recipes[::2], self.recipes[1::2], strict=True):
            varying = (
                {first.family}
                if first.family != "tensorflow"
                else {"epochs", "batch_size", "patience", "learning_rate"}
            )
            if first.effective_sha256() == second.effective_sha256() or first.model_dump(
                exclude={"name", *varying}
            ) != second.model_dump(exclude={"name", *varying}):
                raise ValueError("development_workflow_two_original_distinct_recipes_per_family")
        return self
