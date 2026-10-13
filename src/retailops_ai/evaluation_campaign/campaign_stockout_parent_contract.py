"""Explicit stockout identities and complete public-parent preparation limits."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, CuratedID, SourceID
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_segment_contract import CampaignContextStoragePolicy
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeaturePolicy


class CampaignStockoutContextScope(Contract):
    version: Literal["ai09-stockout-public-context-1.0.0"] = "ai09-stockout-public-context-1.0.0"
    data_seed: Literal[42, 137, 2026]
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignStockoutParentPlan(Contract):
    version: Literal["ai09-stockout-public-parent-plan-1.0.0"] = (
        "ai09-stockout-public-parent-plan-1.0.0"
    )
    source: PhysicalSourceSpec
    runtime: PreparationRuntime
    storage: CampaignContextStoragePolicy = CampaignContextStoragePolicy()
    max_origins: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000
    feature_policy: FeaturePolicy = DEFAULT_FEATURE_POLICY
    population: Literal["all_public_inventory_snapshot_physical_origins_no_sampling"] = (
        "all_public_inventory_snapshot_physical_origins_no_sampling"
    )

    @model_validator(mode="after")
    def native_policy(self) -> Self:
        if self.feature_policy != DEFAULT_FEATURE_POLICY:
            raise ValueError("campaign_stockout_frozen_native_feature_policy_required")
        return self
