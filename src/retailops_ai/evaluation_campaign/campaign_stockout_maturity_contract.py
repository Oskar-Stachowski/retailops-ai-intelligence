"""Explicit stockout purposes on the five shared development date windows."""

from datetime import UTC, datetime, time, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, end_of_day
from retailops_ai.evaluation_campaign.campaign_stockout_labels import CampaignStockoutLabelPlan
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicPlan,
)
from retailops_ai.evaluation_campaign.partition_contract import ROLES, RoleWindow

StockoutMatureRole = Literal[
    "base_train",
    "calibrator_fit",
    "recipe_selection",
    "threshold_assessment",
    "development_evaluation",
]
STOCKOUT_ROLES: tuple[StockoutMatureRole, ...] = (
    "base_train",
    "calibrator_fit",
    "recipe_selection",
    "threshold_assessment",
    "development_evaluation",
)


def start_of_day(value: RoleWindow) -> datetime:
    return datetime.combine(value.origins.start, time.min, UTC)


class CampaignStockoutMaturityPolicy(Contract):
    version: Literal["ai09-stockout-full-mature-development-roles-1.0.0"] = (
        "ai09-stockout-full-mature-development-roles-1.0.0"
    )
    windows: tuple[RoleWindow, ...] = Field(min_length=5, max_length=5)
    stockout_purposes: tuple[StockoutMatureRole, ...] = STOCKOUT_ROLES
    horizon_days: Literal[7] = 7
    purge_days: Annotated[StrictInt, Field(ge=15, le=90)] = 15
    maturity: Literal["available_by_role_cutoff_and_strictly_before_next_role"] = (
        "available_by_role_cutoff_and_strictly_before_next_role"
    )
    full_population: Literal["all_physical_origins_including_ineligible_and_purged"] = (
        "all_physical_origins_including_ineligible_and_purged"
    )
    final_test: Literal["separate_frozen_campaign_not_included_or_authorized"] = (
        "separate_frozen_campaign_not_included_or_authorized"
    )

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if tuple(w.role for w in self.windows) != ROLES or self.stockout_purposes != STOCKOUT_ROLES:
            raise ValueError("campaign_stockout_maturity_exact_role_mapping_required")
        for position, window in enumerate(self.windows):
            if window.label_knowledge_cutoff < end_of_day(
                window.origins.end + timedelta(days=self.horizon_days)
            ):
                raise ValueError("campaign_stockout_maturity_cutoff_before_full_horizon")
            if position + 1 < len(self.windows):
                following = self.windows[position + 1]
                if following.origins.start <= window.origins.end + timedelta(
                    days=self.purge_days
                ) or window.label_knowledge_cutoff >= start_of_day(following):
                    raise ValueError("campaign_stockout_maturity_overlap_or_boundary_leak")
        return self


class CampaignStockoutMaturityPlan(Contract):
    version: Literal["ai09-stockout-full-mature-development-plan-1.0.0"] = (
        "ai09-stockout-full-mature-development-plan-1.0.0"
    )
    public_dataset_id: Annotated[str, Field(pattern=r"^ai09-stockout-public-sha256-[0-9a-f]{64}$")]
    public: CampaignStockoutPublicPlan
    labels: CampaignStockoutLabelPlan
    policy: CampaignStockoutMaturityPolicy
    max_rows: Annotated[StrictInt, Field(ge=1, le=1_000_000)] = 1_000_000
    max_stream_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[StrictInt, Field(ge=4096, le=65536)] = 65536

    @model_validator(mode="after")
    def same_original_parent(self) -> Self:
        if (
            self.public.parent.source != self.labels.source
            or self.public.parent.runtime != self.labels.runtime
        ):
            raise ValueError("campaign_stockout_maturity_same_source_and_runtime_required")
        return self
