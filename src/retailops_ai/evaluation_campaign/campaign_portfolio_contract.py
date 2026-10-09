"""Full canonical source variants in one prospective campaign and one freeze.

The v10 base version and its old wire remain unchanged. The explicit portfolio
extension has its own v25 wire; expected scenario coverage is not outcome proof.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Sha256
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignOperationPlan,
    CampaignProtocol,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.contract import USE_CASES
from retailops_ai.source_snapshot.files import decode_json

if TYPE_CHECKING:
    from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan

SourceVariant = Literal["ordinary", "demand", "physical"]
VARIANTS: tuple[SourceVariant, ...] = ("ordinary", "demand", "physical")


class PortfolioSourceRecipe(CampaignSourceRecipe):
    """Original full profile plus an exact separate original plan binding."""

    exporter_lock_sha256: Sha256 = Field()
    variant: SourceVariant
    scenario_plan_sha256: Sha256 | None

    @model_validator(mode="after")
    def variant_plan(self) -> Self:
        if (self.variant == "ordinary") != (self.scenario_plan_sha256 is None):
            raise ValueError("campaign_portfolio_variant_plan_mismatch")
        return self


class CampaignPortfolioProtocol(CampaignProtocol):
    """Three full variants for development42 and each final42/137/2026."""

    portfolio_version: Literal["ai09-full-scenario-portfolio-1.0.0"] = (
        "ai09-full-scenario-portfolio-1.0.0"
    )
    sources: Annotated[tuple[PortfolioSourceRecipe, ...], Field(min_length=12, max_length=12)]
    training_source_recipe_sha256: dict[Literal["forecast", "anomaly", "stockout"], Sha256]

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        self._frozen_policies()
        expected = tuple(
            (phase, seed, variant)
            for phase, seed in (("development", 42), *(("final", s) for s in self.data_seeds))
            for variant in VARIANTS
        )
        if tuple((s.phase, s.seed, s.variant) for s in self.sources) != expected:
            raise ValueError("campaign_portfolio_complete_ordered_source_inventory_required")
        if (
            len(
                {
                    (s.producer_commit, s.producer_lock_sha256, s.exporter_lock_sha256)
                    for s in self.sources
                }
            )
            != 1
        ):
            raise ValueError("campaign_sources_require_one_producer_revision")
        development = [s for s in self.sources if s.phase == "development"]
        finals = [s for s in self.sources if s.phase == "final"]
        if len(
            {(s.history, s.generation_config_sha256, s.label_delay_days) for s in development}
        ) != 1 or any(
            len({s.generation_config_sha256 for s in finals if s.seed == seed}) != 1
            for seed in self.data_seeds
        ):
            raise ValueError("campaign_portfolio_variant_base_configuration_must_match")
        if len({(s.history, s.evaluation_origins, s.label_delay_days) for s in finals}) != 1:
            raise ValueError("campaign_final_seed_windows_must_match")
        if any(
            s.evaluation_origins is None
            or s.evaluation_origins.start
            <= development[0].history.end + timedelta(days=14 + s.label_delay_days)
            for s in finals
        ):
            raise ValueError("campaign_final_origins_overlap_development_exposure")
        sources = {s.content_sha256(): s for s in self.sources}
        if len(sources) != 12 or set(self.training_source_recipe_sha256) != set(USE_CASES):
            raise ValueError("campaign_portfolio_requires_distinct_sources_and_training_bindings")
        if any(
            digest not in sources or sources[digest].phase != "development"
            for digest in self.training_source_recipe_sha256.values()
        ):
            raise ValueError("campaign_portfolio_training_requires_development_source")
        for operation in self.operations:
            if operation.action in ("model_fit", "calibrator_fit") and (
                self._training_source(operation.use_case) != operation.source_recipe_sha256
            ):
                raise ValueError("campaign_portfolio_fit_training_source_mismatch")
        self._frozen_operations(self._development_dependency)
        for source in development:
            if {
                o.use_case
                for o in self.operations
                if o.source_recipe_sha256 == source.content_sha256()
                and o.action == "model_score"
                and o.role == "development_evaluation"
            } != set(USE_CASES):
                raise ValueError("campaign_portfolio_development_requires_three_use_cases")
        return self

    def _training_source(self, use_case: str) -> str | None:
        return next(
            (
                digest
                for key, digest in self.training_source_recipe_sha256.items()
                if key == use_case
            ),
            None,
        )

    def _development_dependency(
        self, operation: CampaignOperationPlan, parent: CampaignOperationPlan
    ) -> bool:
        # Own source generation/read remains mandatory through the inherited
        # reachability check. Only declared same-use-case training artifacts can
        # cross development variants; final never borrows a mutable dev branch.
        return (
            operation.phase == "development"
            and operation.action == "model_score"
            and operation.use_case == parent.use_case
            and parent.action in ("model_fit", "calibrator_fit", "model_score")
            and parent.source_recipe_sha256 == self._training_source(operation.use_case)
        )


class CampaignPortfolioJournal(CampaignJournal):
    """Reuse every durable event/cost guard and require complete development coverage."""

    protocol: CampaignPortfolioProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        self._replay_events()
        required = {
            o.operation_id
            for o in self.protocol.operations
            if o.phase == "development"
            and (
                o.action in ("source_generate", "source_read") or o.role == "development_evaluation"
            )
        }
        fits = {
            o.operation_id
            for o in self.protocol.operations
            if o.action in ("model_fit", "calibrator_fit")
        }
        attempted: set[str] = set()
        completed: set[str] = set()
        for event in self.events:
            if event.kind == "reserved":
                attempted.add(str(event.operation_id))
            elif event.kind == "finished" and event.result == "completed":
                completed.add(str(event.operation_id))
            elif event.kind == "selection_frozen" and (
                not required.issubset(completed) or not fits.issubset(attempted)
            ):
                raise ValueError("campaign_portfolio_freeze_requires_full_development_inventory")
        return self


def parse_campaign_protocol(raw: bytes) -> CampaignProtocol:
    document = decode_json(raw)
    if "resolved_development_preparation_version" in document:
        from retailops_ai.evaluation_campaign.development_variants_contract import (
            ResolvedDevelopmentPreparationProtocol,
        )

        return ResolvedDevelopmentPreparationProtocol.model_validate_json(canonical_bytes(document))
    if "native_development_planning_version" in document:
        from retailops_ai.evaluation_campaign.development_planning_contract import (
            NativeDevelopmentPlanningProtocol,
        )

        return NativeDevelopmentPlanningProtocol.model_validate_json(canonical_bytes(document))
    if "development_preparation_version" in document:
        # Lazy import keeps additive source/profile contracts out of the
        # original generation/portfolio contract import cycle.
        from retailops_ai.evaluation_campaign.development_preparation import (
            DevelopmentPreparationProtocol,
        )

        return DevelopmentPreparationProtocol.model_validate_json(canonical_bytes(document))
    model = CampaignPortfolioProtocol if "portfolio_version" in document else CampaignProtocol
    return model.model_validate_json(canonical_bytes(document))


def parse_campaign_journal(raw: bytes) -> CampaignJournal:
    document = decode_json(raw)
    protocol = document.get("protocol")
    if isinstance(protocol, dict) and "resolved_development_preparation_version" in protocol:
        from retailops_ai.evaluation_campaign.development_variants_contract import (
            ResolvedDevelopmentPreparationJournal,
        )

        return ResolvedDevelopmentPreparationJournal.model_validate_json(canonical_bytes(document))
    if isinstance(protocol, dict) and "native_development_planning_version" in protocol:
        from retailops_ai.evaluation_campaign.development_planning_contract import (
            NativeDevelopmentPlanningJournal,
        )

        return NativeDevelopmentPlanningJournal.model_validate_json(canonical_bytes(document))
    if isinstance(protocol, dict) and "development_preparation_version" in protocol:
        from retailops_ai.evaluation_campaign.development_preparation import (
            DevelopmentPreparationJournal,
        )

        return DevelopmentPreparationJournal.model_validate_json(canonical_bytes(document))
    model = (
        CampaignPortfolioJournal
        if isinstance(protocol, dict) and "portfolio_version" in protocol
        else CampaignJournal
    )
    return model.model_validate_json(canonical_bytes(document))


def bind_portfolio_generation(plan: CampaignGenerationPlan, source: PortfolioSourceRecipe) -> None:
    """Bind the actual existing public generation runner to the frozen variant."""
    expected_version = {
        "demand": "business-anomaly-plan-1.0.0",
        "physical": "business-physical-anomaly-plan-1.0.0",
    }
    if source.variant == "ordinary":
        valid = plan.entrypoint == "cached_inventory_v2" and plan.scenario_plan is None
    else:
        valid = (
            plan.entrypoint == "planned_anomaly"
            and plan.scenario_plan is not None
            and plan.scenario_plan.get("contract_version") == expected_version[source.variant]
            and canonical_sha256(plan.scenario_plan) == source.scenario_plan_sha256
        )
        if valid and source.variant == "physical":
            injections = plan.scenario_plan.get("injections") if plan.scenario_plan else None
            valid = isinstance(injections, list) and any(
                isinstance(row, dict) and row.get("injection_type") == "inventory_censored_episode"
                for row in injections
            )
    if not valid:
        raise ValueError("campaign_portfolio_generation_variant_or_plan_mismatch")
