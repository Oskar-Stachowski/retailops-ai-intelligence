"""Three original finalists on canonical development, after measured 25/50 selection."""

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
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import PortfolioSourceRecipe
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastTunePlan,
    CampaignForecastTuneReceipt,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
    PrivatePath,
    validate_advancement_scope,
)


class PreparedCanonicalForecastProtocol(CampaignProtocol):
    canonical_forecast_version: Literal["ai09-prepared-canonical-forecast-1.0.0"] = (
        "ai09-prepared-canonical-forecast-1.0.0"
    )
    advancement: CompletedForecastAdvancement
    parents: PreparedCanonicalDevelopmentParents
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=3, max_length=3)]
    exports: Annotated[tuple[CampaignDevelopmentExportPlan, ...], Field(min_length=3, max_length=3)]
    fits: Annotated[tuple[CampaignForecastFitPlan, ...], Field(min_length=3, max_length=3)]
    score: CampaignForecastScorePlan
    selection_plan: CampaignForecastTunePlan
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=8, max_length=8)]
    maximum_new_attempts: Literal[8] = 8
    maximum_new_fit_attempts: Literal[3] = 3
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        if self.advancement.status != "original_families_verified":
            raise ValueError("canonical_forecast_three_valid_original_fifty_families_required")
        validate_advancement_scope(
            self, screening=self.advancement.protocol.screening, products=100
        )
        return self


class PreparedCanonicalForecastJournal(CampaignJournal):
    protocol: PreparedCanonicalForecastProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        if any(e.kind not in {"reserved", "finished"} for e in self.events):
            raise ValueError("canonical_forecast_no_final_freeze_or_close")
        self._replay_events()
        return self


class CompletedCanonicalForecast(Contract):
    protocol: PreparedCanonicalForecastProtocol
    journal_head_sha256: Sha256
    selection_bundle: PrivatePath
    selected: CampaignForecastTuneReceipt
    operation_costs: Annotated[tuple[CampaignCost, ...], Field(min_length=8, max_length=8)]
    status: Literal["original_families_verified", "not_ready"]
    family_reasons: dict[Family, tuple[str, ...]]
    reasons: tuple[str, ...]
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
            raise ValueError("canonical_forecast_original_result_binding_or_measured_costs")
        return self

    def cold_wall_seconds(self) -> float:
        return (
            self.protocol.advancement.cold_wall_seconds()
            + self.protocol.parents.cold_wall_seconds()
            + sum(c.wall_seconds for c in self.operation_costs)
        )
