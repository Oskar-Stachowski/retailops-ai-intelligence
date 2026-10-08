"""Pre-generation configuration and resource limits for an audited source attempt."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignParentBudget
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    PortfolioSourceRecipe,
    bind_portfolio_generation,
)


class CampaignGenerationResources(Contract):
    wall_seconds: Annotated[int, Field(ge=1, le=86400)]
    tree_rss_bytes: Annotated[int, Field(ge=1024, le=64 * 1024**3)]
    scratch_bytes: Annotated[int, Field(ge=1024, le=128 * 1024**3)]
    minimum_free_disk_bytes: Annotated[int, Field(ge=1024)]
    minimum_available_memory_bytes: Annotated[int, Field(ge=1024)]
    sample_seconds: Annotated[float, Field(ge=0.01, le=1)] = 0.2
    max_log_bytes: Annotated[int, Field(ge=1024, le=16 * 1024**2)] = 16 * 1024**2
    cpu_threads: Literal[1] = 1


class CampaignGenerationPlan(Contract):
    version: Literal["ai09-campaign-generation-plan-1.0.0"] = "ai09-campaign-generation-plan-1.0.0"
    source_recipe_sha256: Sha256
    exporter_lock_sha256: Sha256
    requested_parameters: dict[str, JsonValue]
    resolved_parameters: dict[str, JsonValue]
    entrypoint: Literal["cached_inventory_v2", "planned_anomaly"]
    scenario_plan: dict[str, JsonValue] | None = None
    snapshot_schema_version: Literal["1.1.0", "1.2.0"]
    parent_budget: CampaignParentBudget = CampaignParentBudget()
    resources: CampaignGenerationResources
    chunk_rows: Annotated[int, Field(ge=1, le=8192)] = 256
    include_truth: Literal[False] = False
    required_use_cases: Annotated[
        tuple[Literal["forecast_source", "inventory_source", "anomaly_source"], ...],
        Field(min_length=2, max_length=3),
    ] = ("forecast_source", "inventory_source")

    @model_validator(mode="after")
    def scenario(self) -> Self:
        if (self.entrypoint == "planned_anomaly") != (self.scenario_plan is not None):
            raise ValueError("campaign_generation_scenario_required_for_anomaly_only")
        expected = "1.2.0" if self.entrypoint == "planned_anomaly" else "1.1.0"
        if self.snapshot_schema_version != expected:
            raise ValueError("campaign_generation_snapshot_schema_mismatch")
        use_cases = ("forecast_source", "inventory_source") + (
            ("anomaly_source",) if self.entrypoint == "planned_anomaly" else ()
        )
        if self.required_use_cases != use_cases:
            raise ValueError("campaign_generation_required_use_cases_mismatch")
        return self

    def bind(self, source: CampaignSourceRecipe) -> None:
        expected = {
            "profile": source.profile,
            "seed": source.seed,
            "days": (source.history.end - source.history.start).days + 1,
            "products": source.products,
            "stores": source.selling_pairs,
            "warehouses": source.stock_locations,
            "start_date": source.history.start.isoformat(),
            "end_date": source.history.end.isoformat(),
            "business_timezone": "UTC",
        }
        if (
            self.source_recipe_sha256 != source.content_sha256()
            or source.exporter_lock_sha256 != self.exporter_lock_sha256
            or canonical_sha256(self.resolved_parameters) != source.generation_config_sha256
            or any(
                type(self.resolved_parameters.get(k)) is not type(v)
                or self.resolved_parameters.get(k) != v
                for k, v in expected.items()
            )
        ):
            raise ValueError("campaign_generation_frozen_source_configuration_mismatch")
        if isinstance(source, PortfolioSourceRecipe):
            bind_portfolio_generation(self, source)

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))
