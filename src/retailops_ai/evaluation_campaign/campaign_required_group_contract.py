"""Preregister critical group ownership without removing any diagnostic report.

The selection-policy digest is frozen in the protocol before source generation.
Group ownership spans the full portfolio; no observed counts select an owner.
This policy does not qualify native interventions or unknown-category encoding.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    CampaignPortfolioProtocol,
    SourceVariant,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastSegmentCensus,
    CampaignForecastSegmentPolicy,
    Dimension,
    required_population_ids,
)
from retailops_ai.source_snapshot.files import SnapshotError

GroupID = tuple[Dimension, str]

# These populations are always reported, including empty/invalid metrics.
# Their mere presence is not a required scientific intervention. Meaningful
# zero sales, cold start, missing/late history, inventory and planned anomalies
# remain mandatory across every seed's portfolio below.
STRUCTURAL_DIAGNOSTICS: frozenset[GroupID] = frozenset(
    {
        ("category", "[missing]"),
        ("volume", "unknown"),
        ("history", "insufficient_known_history"),
        ("availability", "missing_known_plan"),
        ("availability", "other_missing_input"),
        ("inventory", "stale"),
        ("inventory", "unknown"),
        ("lead_time", "unknown"),
        ("intermittency", "incomplete_history"),
        ("intermittency", "no_known_open_days"),
        ("anomaly", "unannotated"),
    }
)


class CampaignRequiredGroup(Contract):
    dimension: Dimension
    value: Annotated[str, Field(min_length=1, max_length=256)]

    def identity(self) -> GroupID:
        return self.dimension, self.value


class CampaignSourceRequiredGroups(Contract):
    phase: Literal["development", "final"]
    seed: Literal[42, 137, 2026]
    variant: SourceVariant
    source_recipe_sha256: Sha256
    required: Annotated[tuple[CampaignRequiredGroup, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def unambiguous(self) -> Self:
        identities = tuple(g.identity() for g in self.required)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("campaign_required_groups_sorted_unique_inventory")
        return self


class CampaignPortfolioRequiredGroupPolicy(Contract):
    version: Literal["ai09-portfolio-required-groups-1.0.0"] = (
        "ai09-portfolio-required-groups-1.0.0"
    )
    segment_policy: CampaignForecastSegmentPolicy
    sources: Annotated[
        tuple[CampaignSourceRequiredGroups, ...], Field(min_length=12, max_length=12)
    ]
    coverage_rule: Literal["every_seed_preregistered_source_owners_without_pooling"] = (
        "every_seed_preregistered_source_owners_without_pooling"
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
        phases = (("development", 42), ("final", 42), ("final", 137), ("final", 2026))
        expected = {(phase, seed, variant) for phase, seed in phases for variant in VARIANTS}
        if {(s.phase, s.seed, s.variant) for s in self.sources} != expected or len(
            {s.source_recipe_sha256 for s in self.sources}
        ) != 12:
            raise ValueError("campaign_required_groups_full_source_inventory")
        inventory = frozenset(required_population_ids(self.segment_policy))
        mandatory = inventory - STRUCTURAL_DIAGNOSTICS
        every_source = frozenset(g for g in inventory if g[0] in {"global", "horizon", "channel"})
        every_source |= frozenset(("category", c) for c in self.segment_policy.category_inventory)
        scenario_owner = {
            "normal": "ordinary",
            "promotion": "ordinary",
            "demand_shock": "demand",
            "inventory_constraint": "physical",
        }
        for source in self.sources:
            required = {g.identity() for g in source.required}
            if not every_source <= required <= inventory:
                raise ValueError("campaign_required_groups_missing_common_or_unknown_group")
            for scenario, owner in scenario_owner.items():
                if source.variant == owner and ("scenario", scenario) not in required:
                    raise ValueError("campaign_required_groups_scenario_owner_missing")
        for phase, seed in phases:
            covered = {
                g.identity()
                for s in self.sources
                if (s.phase, s.seed) == (phase, seed)
                for g in s.required
            }
            if not mandatory <= covered:
                raise ValueError("campaign_required_groups_original_critical_coverage_missing")
        return self

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))

    def bind(self, protocol: CampaignPortfolioProtocol) -> None:
        self = type(self).model_validate_json(self.model_dump_json())
        protocol = CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())
        declared = {(s.phase, s.seed, s.variant, s.source_recipe_sha256) for s in self.sources}
        actual = {(s.phase, s.seed, s.variant, s.content_sha256()) for s in protocol.sources}
        if (
            declared != actual
            or self.content_sha256() != protocol.selection_policy_sha256
            or self.segment_policy.content_sha256() != protocol.segment_policy_sha256
        ):
            raise SnapshotError("campaign_required_groups_frozen_protocol_mismatch")

    def groups_for(
        self,
        protocol: CampaignPortfolioProtocol,
        plan: CampaignForecastEvaluationPlan,
        census: CampaignForecastSegmentCensus,
    ) -> frozenset[GroupID]:
        """Reject rebinding before metrics; receipt flags grant no authority."""
        self = type(self).model_validate_json(self.model_dump_json())
        protocol = CampaignPortfolioProtocol.model_validate_json(protocol.model_dump_json())
        self.bind(protocol)
        source = next(
            (s for s in self.sources if s.source_recipe_sha256 == plan.source_recipe_sha256), None
        )
        original = next(
            (s for s in protocol.sources if s.content_sha256() == plan.source_recipe_sha256), None
        )
        if (
            self.segment_policy != census.policy
            or census.scope.segment_policy_sha256 != protocol.segment_policy_sha256
            or census.scope.source_recipe_sha256 != plan.source_recipe_sha256
            or plan.segment_policy_sha256 != protocol.segment_policy_sha256
            or source is None
            or original is None
            or census.scope.source_scenario_plan_sha256 != original.scenario_plan_sha256
            or source.phase != plan.phase
            or source.seed != census.scope.data_seed
            or census.scope.role != plan.role
        ):
            raise SnapshotError("campaign_required_groups_frozen_protocol_or_source_mismatch")
        return frozenset(g.identity() for g in source.required)
