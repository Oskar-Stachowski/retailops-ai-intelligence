"""Private development role files, retaining complete original membership."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, UtcTime
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    STOCKOUT_ROLES,
    CampaignStockoutMaturityPlan,
    StockoutMatureRole,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public_contract import (
    CampaignStockoutPublicRow,
)
from retailops_ai.evaluation_campaign.partition_contract import ROLES, DevelopmentRole
from retailops_ai.stockout.contract import LabelPoint

ROLE_FILES = {role: role + ".jsonl" for role in STOCKOUT_ROLES}
MEMBERSHIP = "membership.jsonl"
FILES = {MEMBERSHIP, *ROLE_FILES.values()}


class CampaignStockoutRoleArtifactPlan(Contract):
    version: Literal["ai09-full-stockout-role-artifact-plan-1.0.0"] = (
        "ai09-full-stockout-role-artifact-plan-1.0.0"
    )
    maturity: CampaignStockoutMaturityPlan
    max_artifact_bytes: Annotated[StrictInt, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_membership_record_bytes: Annotated[StrictInt, Field(ge=1024, le=16384)] = 4096
    population: Literal["all_membership_and_every_eligible_row_in_exactly_one_role"] = (
        "all_membership_and_every_eligible_row_in_exactly_one_role"
    )


class CampaignStockoutRoleMembership(Contract):
    role: StockoutMatureRole | Literal["purged"]
    development_window: DevelopmentRole | None
    eligible: StrictBool
    reason: str | None
    label_knowledge_cutoff: UtcTime | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.eligible != (self.reason is None):
            raise ValueError("stockout_role_eligibility_reason_mismatch")
        if self.role == "purged":
            if (
                self.eligible
                or self.development_window is not None
                or self.label_knowledge_cutoff is not None
            ):
                raise ValueError("stockout_role_purged_membership_mismatch")
        elif (
            self.development_window != ROLES[STOCKOUT_ROLES.index(self.role)]
            or self.label_knowledge_cutoff is None
        ):
            raise ValueError("stockout_role_window_mapping_mismatch")
        return self


class CampaignStockoutRoleRow(Contract):
    membership: CampaignStockoutRoleMembership
    feature: CampaignStockoutPublicRow
    label: LabelPoint

    @model_validator(mode="after")
    def same_key(self) -> Self:
        if (self.feature.product_id, self.feature.stock_location_id, self.feature.as_of) != (
            self.label.product_id,
            self.label.stock_location_id,
            self.label.as_of,
        ):
            raise ValueError("stockout_role_physical_key_mismatch")
        return self


class CampaignStockoutMembershipRecord(Contract):
    product_id: Symbol
    stock_location_id: Symbol
    as_of: UtcTime
    membership: CampaignStockoutRoleMembership
    feature_row_sha256: Sha256
    label_row_sha256: Sha256
    joined_row_sha256: Sha256


class CampaignStockoutRoleFile(Contract):
    rows: Annotated[StrictInt, Field(ge=0, le=1_000_000)]
    bytes: Annotated[StrictInt, Field(ge=0, le=8 * 1024**3)]
    sha256: Sha256
    keys_sha256: Sha256
    origin_stream_sha256: Sha256
    joined_row_hashes_sha256: Sha256
    labels_sha256: Sha256 | None
