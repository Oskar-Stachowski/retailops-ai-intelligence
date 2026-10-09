"""Distinct full-history development profiles; canonical/final wires stay fixed.

These plans use the producer's supported explicit size overrides. They never
truncate an existing parent or relabel a smaller source as the canonical one.
Actual completeness, scenario effects and critical coverage require evidence
from the prepared data, not just these declarations.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, DateWindow, FalseFlag, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    VARIANTS,
    PortfolioSourceRecipe,
)


class DevelopmentProfile(Contract):
    version: Literal["ai09-full-history-development-profile-1.0.0"] = (
        "ai09-full-history-development-profile-1.0.0"
    )
    name: Literal["ai09-development-25-v1", "ai09-development-50-v1"]
    products: Literal[25, 50]
    days: Literal[365] = 365
    selling_pairs: Literal[5] = 5
    stock_locations: Literal[3] = 3
    data_seed: Literal[42] = 42
    source_base_profile: Literal["ai-dev"] = "ai-dev"
    history: DateWindow = DateWindow(start=date(2025, 8, 1), end=date(2026, 7, 31))
    forecast_plan_days: Literal[14] = 14
    variants: tuple[Literal["ordinary"], Literal["demand"], Literal["physical"]] = (
        "ordinary",
        "demand",
        "physical",
    )
    population: Literal["complete_native_generation_no_parent_filter_or_history_sampling"] = (
        "complete_native_generation_no_parent_filter_or_history_sampling"
    )
    canonical_development_qualified: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        if self.name != f"ai09-development-{self.products}-v1" or self.history != DateWindow(
            start=date(2025, 8, 1), end=date(2026, 7, 31)
        ):
            raise ValueError("development_profile_frozen_name_or_exposed_history")
        return self

    def requested_parameters(self) -> dict[str, JsonValue]:
        return {
            "profile": self.source_base_profile,
            "seed": self.data_seed,
            "days": self.days,
            "products": self.products,
            "stores": self.selling_pairs,
            "warehouses": self.stock_locations,
            "start_date": self.history.start.isoformat(),
            "end_date": self.history.end.isoformat(),
            "max_daily_rows": self.days * self.products * self.selling_pairs,
            "forecast_plan_days": self.forecast_plan_days,
        }

    def resolved_parameters(self) -> dict[str, JsonValue]:
        return self.requested_parameters() | {
            "business_timezone": "UTC",
            "output_format": "csv",
            "warmup_days": 0,
            "origin_days": 0,
            "label_tail_days": 0,
            "forecast_plan_version": "known-forecast-plans-1.0.0",
        }

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class DevelopmentProfileSource(PortfolioSourceRecipe):
    """Separate additive wire, rejected by both canonical source contracts."""

    development_profile: DevelopmentProfile

    @model_validator(mode="after")
    def canonical_profile(self) -> Self:
        # Deliberately override only this new subtype's profile validator.
        # CampaignSourceRecipe and PortfolioSourceRecipe still require 100/200.
        profile = self.development_profile
        if (
            self.phase != "development"
            or self.seed != profile.data_seed
            or self.profile != profile.source_base_profile
            or self.history != profile.history
            or self.products != profile.products
            or self.selling_pairs != profile.selling_pairs
            or self.stock_locations != profile.stock_locations
            or self.evaluation_origins is not None
            or self.generation_config_sha256 != canonical_sha256(profile.resolved_parameters())
        ):
            raise ValueError("development_profile_source_scope_mismatch")
        return self


class DevelopmentProfilePreparation(Contract):
    version: Literal["ai09-development-profile-preparation-1.0.0"] = (
        "ai09-development-profile-preparation-1.0.0"
    )
    profile: DevelopmentProfile
    sources: Annotated[tuple[DevelopmentProfileSource, ...], Field(min_length=3, max_length=3)]
    generations: Annotated[tuple[CampaignGenerationPlan, ...], Field(min_length=3, max_length=3)]
    critical_coverage: Literal[
        "requires_complete_native_source_and_all_use_case_group_evidence"
    ] = "requires_complete_native_source_and_all_use_case_group_evidence"
    actual_coverage_verified: FalseFlag = False
    project_campaign_authorized: FalseFlag = False
    model_fit_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_variants(self) -> Self:
        if tuple(s.variant for s in self.sources) != VARIANTS or any(
            s.development_profile != self.profile for s in self.sources
        ):
            raise ValueError("development_profile_requires_three_ordered_full_variants")
        if (
            len(
                {
                    (s.producer_commit, s.producer_lock_sha256, s.exporter_lock_sha256)
                    for s in self.sources
                }
            )
            != 1
        ):
            raise ValueError("development_profile_requires_one_pinned_producer")
        if len({p.resources.model_dump_json() for p in self.generations}) != 1:
            raise ValueError("development_profile_variant_resource_budgets_must_match")
        for source, plan in zip(self.sources, self.generations, strict=True):
            plan.bind(source)
            if plan.requested_parameters != self.profile.requested_parameters():
                raise ValueError("development_profile_requested_parameters_mismatch")
            limits = plan.resources
            if (
                limits.tree_rss_bytes > 12 * 1024**3
                or limits.scratch_bytes > 8 * 1024**3
                or limits.wall_seconds > 10800
                or limits.minimum_available_memory_bytes < 1024**3
                or limits.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("development_profile_preparation_resource_ceiling")
            if source.variant != "ordinary":
                scenario = plan.scenario_plan or {}
                expected = (
                    {"one_day_spike", "multi_day_spike", "sustained_drop"}
                    if source.variant == "demand"
                    else {"return_spike", "inventory_censored_episode"}
                )
                injections = scenario.get("injections")
                if (
                    scenario.get("seed") != self.profile.data_seed
                    or not isinstance(injections, list)
                    or {i.get("injection_type") for i in injections if isinstance(i, dict)}
                    != expected
                ):
                    raise ValueError("development_profile_all_planned_anomaly_types_required")
                controls = scenario.get("controls")
                if not isinstance(controls, list) or not controls:
                    raise ValueError("development_profile_clean_controls_required")
                for window in (*injections, *controls):
                    if not isinstance(window, dict):
                        raise ValueError("development_profile_scenario_window_required")
                    start = date.fromisoformat(str(window.get("start_date", "")))
                    end = date.fromisoformat(str(window.get("end_date", "")))
                    if not self.profile.history.start <= start <= end <= self.profile.history.end:
                        raise ValueError("development_profile_scenario_outside_history")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


def prepare_development_profile(
    profile: DevelopmentProfile,
    *,
    producer_commit: CommitSha,
    producer_lock_sha256: Sha256,
    exporter_lock_sha256: Sha256,
    scenario_plans: dict[Literal["demand", "physical"], dict[str, JsonValue]],
    resources: CampaignGenerationResources,
) -> DevelopmentProfilePreparation:
    """Compile exact existing native generation requests, without reading Source.

    The original producer parser must independently accept both scenario plans;
    this compiler checks portfolio completeness, not a replacement native schema.
    No campaign journal or final-access authorization is created here.
    """
    profile = DevelopmentProfile.model_validate_json(profile.model_dump_json())
    if set(scenario_plans) != {"demand", "physical"}:
        raise ValueError("development_profile_scenario_inventory")
    sources, generations = [], []
    for variant in VARIANTS:
        scenario = None if variant == "ordinary" else scenario_plans[variant]
        source = DevelopmentProfileSource(
            phase="development",
            seed=profile.data_seed,
            producer_commit=producer_commit,
            producer_lock_sha256=producer_lock_sha256,
            exporter_lock_sha256=exporter_lock_sha256,
            generation_config_sha256=canonical_sha256(profile.resolved_parameters()),
            profile=profile.source_base_profile,
            history=profile.history,
            products=profile.products,
            selling_pairs=profile.selling_pairs,
            stock_locations=profile.stock_locations,
            variant=variant,
            scenario_plan_sha256=canonical_sha256(scenario) if scenario is not None else None,
            development_profile=profile,
        )
        sources.append(source)
        generations.append(
            CampaignGenerationPlan(
                source_recipe_sha256=source.content_sha256(),
                exporter_lock_sha256=exporter_lock_sha256,
                requested_parameters=profile.requested_parameters(),
                resolved_parameters=profile.resolved_parameters(),
                entrypoint="cached_inventory_v2" if variant == "ordinary" else "planned_anomaly",
                scenario_plan=scenario,
                snapshot_schema_version="1.1.0" if variant == "ordinary" else "1.2.0",
                required_use_cases=("forecast_source", "inventory_source")
                + (("anomaly_source",) if variant != "ordinary" else ()),
                resources=resources,
            )
        )
    return DevelopmentProfilePreparation(
        profile=profile, sources=tuple(sources), generations=tuple(generations)
    )
