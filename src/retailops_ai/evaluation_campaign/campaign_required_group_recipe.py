"""Freeze group ownership before native Source recipes have been resolved.

Every later source is bound by the original portfolio protocol and evaluation
plan. Group ownership depends only on its preregistered variant, never on its
observed outcomes, counts, seed or generated identity. The v27 policy stays valid.
"""

import json
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    CampaignPortfolioProtocol,
    SourceVariant,
)
from retailops_ai.evaluation_campaign.campaign_required_group_contract import (
    CampaignPortfolioRequiredGroupPolicy,
    CampaignRequiredGroup,
    GroupID,
    validate_group_inventory,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastSegmentCensus,
    CampaignForecastSegmentPolicy,
)
from retailops_ai.source_snapshot.files import SnapshotError


class CampaignVariantRequiredGroups(Contract):
    variant: SourceVariant
    required: Annotated[tuple[CampaignRequiredGroup, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def unambiguous(self) -> Self:
        identities = tuple(g.identity() for g in self.required)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("campaign_required_groups_sorted_unique_inventory")
        return self


class CampaignRequiredGroupRecipe(Contract):
    version: Literal["ai09-prospective-required-group-recipe-1.0.0"] = (
        "ai09-prospective-required-group-recipe-1.0.0"
    )
    segment_policy: CampaignForecastSegmentPolicy
    variants: Annotated[
        tuple[CampaignVariantRequiredGroups, ...], Field(min_length=3, max_length=3)
    ]
    source_binding: Literal["original_protocol_source_recipe_and_evaluation_scope"] = (
        "original_protocol_source_recipe_and_evaluation_scope"
    )
    coverage_rule: Literal["same_preregistered_variant_owners_in_every_seed_without_pooling"] = (
        "same_preregistered_variant_owners_in_every_seed_without_pooling"
    )
    critical_insufficient_sample: Literal["blocks_qualification"] = "blocks_qualification"
    diagnostics: Literal["all_census_groups_metrics_counts_validity_and_uncertainty_retained"] = (
        "all_census_groups_metrics_counts_validity_and_uncertainty_retained"
    )
    native_scenario_effects_qualified: FalseFlag = False
    unknown_category_encoding_qualified: FalseFlag = False
    final_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def full_critical_coverage(self) -> Self:
        if tuple(v.variant for v in self.variants) != VARIANTS:
            raise ValueError("campaign_group_recipe_complete_ordered_variants_required")
        validate_group_inventory(
            self.segment_policy,
            {v.variant: frozenset(g.identity() for g in v.required) for v in self.variants},
        )
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))

    def bind(self, protocol: CampaignPortfolioProtocol) -> None:
        self = type(self).model_validate_json(self.model_dump_json())
        protocol = CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())
        if (
            self.content_sha256() != protocol.selection_policy_sha256
            or self.segment_policy.content_sha256() != protocol.segment_policy_sha256
        ):
            raise SnapshotError("campaign_group_recipe_frozen_protocol_mismatch")

    def groups_for(
        self,
        protocol: CampaignPortfolioProtocol,
        plan: CampaignForecastEvaluationPlan,
        census: CampaignForecastSegmentCensus,
    ) -> frozenset[GroupID]:
        self = type(self).model_validate_json(self.model_dump_json())
        protocol = CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())
        self.bind(protocol)
        original = next(
            (s for s in protocol.sources if s.content_sha256() == plan.source_recipe_sha256), None
        )
        if (
            original is None
            or self.segment_policy != census.policy
            or census.scope.segment_policy_sha256 != protocol.segment_policy_sha256
            or census.scope.source_recipe_sha256 != plan.source_recipe_sha256
            or plan.segment_policy_sha256 != protocol.segment_policy_sha256
            or census.scope.source_scenario_plan_sha256 != original.scenario_plan_sha256
            or original.phase != plan.phase
            or original.seed != census.scope.data_seed
            or census.scope.role != plan.role
        ):
            raise SnapshotError("campaign_group_recipe_original_source_or_scope_mismatch")
        return next(
            frozenset(g.identity() for g in v.required)
            for v in self.variants
            if v.variant == original.variant
        )


RequiredGroupPolicy = CampaignPortfolioRequiredGroupPolicy | CampaignRequiredGroupRecipe


def parse_required_group_policy(raw: bytes | str) -> RequiredGroupPolicy:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SnapshotError("campaign_required_group_policy_version_invalid")
    if value.get("version") == "ai09-portfolio-required-groups-1.0.0":
        return CampaignPortfolioRequiredGroupPolicy.model_validate_json(raw)
    if value.get("version") == "ai09-prospective-required-group-recipe-1.0.0":
        return CampaignRequiredGroupRecipe.model_validate_json(raw)
    raise SnapshotError("campaign_required_group_policy_version_invalid")
