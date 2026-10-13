"""Prospective full public stockout features and fixed rolling-origin upstream."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, UtcTime
from retailops_ai.evaluation_campaign.campaign_stockout_parent_contract import (
    CampaignStockoutParentPlan,
)
from retailops_ai.stockout.feature_contract import FeatureValues
from retailops_ai.stockout.upstream_contract import DEFAULT_UPSTREAM_POLICY, UpstreamPolicy


class CampaignStockoutPublicPlan(Contract):
    version: Literal["ai09-stockout-full-public-plan-1.0.0"] = (
        "ai09-stockout-full-public-plan-1.0.0"
    )
    parent: CampaignStockoutParentPlan
    upstream: UpstreamPolicy = DEFAULT_UPSTREAM_POLICY
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_artifact_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=1024, le=65536)] = 16384
    population: Literal[
        "every_public_physical_origin_including_ineligible_and_missing_upstream"
    ] = "every_public_physical_origin_including_ineligible_and_missing_upstream"

    @model_validator(mode="after")
    def frozen(self) -> Self:
        if self.upstream != DEFAULT_UPSTREAM_POLICY:
            raise ValueError("campaign_stockout_frozen_native_upstream_required")
        if self.parent.storage.max_index_bytes > self.max_index_bytes:
            raise ValueError("campaign_stockout_combined_index_budget_required")
        return self


class CampaignStockoutPublicValues(FeatureValues):
    forecast_units_7d: Annotated[float, Field(ge=0)] | None
    forecast_days_of_supply: Annotated[float, Field(ge=0)] | None
    forecast_unavailable: Annotated[int, Field(ge=0, le=1, strict=True)]

    @model_validator(mode="after")
    def native_comparison(self) -> Self:
        expected = (
            7 * self.available_qty / self.forecast_units_7d
            if self.available_qty is not None and self.forecast_units_7d
            else None
        )
        if (self.forecast_units_7d is None) != bool(
            self.forecast_unavailable
        ) or self.forecast_days_of_supply != expected:
            raise ValueError("campaign_stockout_public_missing_upstream_or_supply_mismatch")
        return self


class CampaignStockoutPublicRow(Contract):
    product_id: Symbol
    stock_location_id: Symbol
    as_of: UtcTime
    status: Literal["eligible", "already_stockout", "insufficient_data"]
    reason: str | None
    category_id: Symbol | Literal["__unknown__"]
    category_source_record_sha256: Sha256 | None
    values: CampaignStockoutPublicValues
    feature_sha256: Sha256
    upstream_sha256: Sha256

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if self.category_source_record_sha256 is None and self.category_id != "__unknown__":
            raise ValueError("campaign_stockout_public_category_provenance")
        return self
