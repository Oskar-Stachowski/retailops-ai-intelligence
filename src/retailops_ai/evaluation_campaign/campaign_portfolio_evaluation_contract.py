"""Explicit preregistered development source transfer; never a fitting grant."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, FalseFlag, Symbol
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastFrozenConfiguration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationRecipe,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import CampaignPortfolioProtocol

DevelopmentDataset = Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]


class CampaignPortfolioForecastEvaluationBinding(Contract):
    version: Literal["ai09-portfolio-forecast-evaluation-binding-1.0.0"] = (
        "ai09-portfolio-forecast-evaluation-binding-1.0.0"
    )
    protocol: CampaignPortfolioProtocol
    operation_id: Symbol
    recipe: CampaignForecastEvaluationRecipe
    training_dataset_id: DevelopmentDataset
    evaluation_dataset_id: DevelopmentDataset
    training_or_preprocessing_refitted: FalseFlag = False
    access_authorized_by_this_document: FalseFlag = False
    stage_ready: FalseFlag = False

    def matches(
        self,
        configuration: CampaignForecastFrozenConfiguration,
        plan: CampaignForecastEvaluationPlan,
    ) -> bool:
        """Resolve the original frozen recipe and bind both complete source scopes."""
        operation = next(
            (o for o in self.protocol.operations if o.operation_id == self.operation_id), None
        )
        export = next(
            (o for o in self.protocol.operations if o.operation_id == plan.export_operation_id),
            None,
        )
        training = self.protocol.training_source_recipe_sha256["forecast"]
        source = next(
            (s for s in self.protocol.sources if s.content_sha256() == plan.source_recipe_sha256),
            None,
        )
        try:
            resolved = self.recipe.resolve(configuration)
        except ValueError:
            return False
        return (
            plan.phase == "development"
            and plan.role == "development_evaluation"
            and configuration.protocol_sha256 == self.protocol.content_sha256()
            and configuration.runtime_code_sha256 == self.protocol.runtime.code_sha256
            and configuration.development_source_recipe_sha256 == training
            and configuration.development_dataset_id == self.training_dataset_id
            and (plan.source_recipe_sha256 == training)
            == (self.evaluation_dataset_id == self.training_dataset_id)
            and resolved == plan
            and source is not None
            and source.phase == "development"
            and operation is not None
            and operation.phase == "development"
            and operation.action == "model_score"
            and operation.use_case == "forecast"
            and operation.role == "development_evaluation"
            and operation.source_recipe_sha256 == plan.source_recipe_sha256
            and operation.execution_recipe_sha256 == self.recipe.content_sha256()
            and {
                plan.export_operation_id,
                configuration.tune_operation_id,
                configuration.calibration_operation_id,
            }
            <= set(operation.prerequisites)
            and export is not None
            and export.phase == "development"
            and export.action == "source_read"
            and export.use_case == "source"
            and export.role == "all_parent_data"
            and export.source_recipe_sha256 == plan.source_recipe_sha256
        )
