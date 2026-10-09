"""Frozen complete-parent anomaly fitting and its durable measured receipt."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.protocol import Scope, Window
from retailops_ai.anomaly_detectors.rows import ALL_FEATURES
from retailops_ai.anomaly_portfolio.model import EventCapacity
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)


class CampaignAnomalyFitPlan(Contract):
    version: Literal["ai09-campaign-anomaly-fit-1.0.0"] = "ai09-campaign-anomaly-fit-1.0.0"
    phase: Literal["development"] = "development"
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    train: Window
    validation: Window
    test: Window
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    policy: CensusFitPolicy = CensusFitPolicy.model_validate({"features": ALL_FEATURES})
    event_capacities: tuple[EventCapacity, ...] = Field(min_length=2, max_length=2)
    resources: CampaignGenerationResources
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_role_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_days: Annotated[int, Field(ge=1, le=20000000)] = 20000000
    max_series: Annotated[int, Field(ge=1, le=65536)] = 65536
    max_series_rows: Annotated[int, Field(ge=1, le=1000000)] = 100000
    max_series_bytes: Annotated[int, Field(ge=4096, le=256 * 1024**2)] = 64 * 1024**2
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=64 * 1024**2)] = 32 * 1024**2
    max_capture_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    capture_policy: Literal["complete_native_public_parent_at_actual_fact_ingestion_time"] = (
        "complete_native_public_parent_at_actual_fact_ingestion_time"
    )
    population: Literal["all_native_declared_series_days_no_sampling"] = (
        "all_native_declared_series_days_no_sampling"
    )
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def causal(self) -> Self:
        if tuple(c.event_type for c in self.event_capacities) != (
            "sale_completed",
            "return_completed",
        ):
            raise ValueError("campaign_anomaly_fit_event_capacity_inventory")
        # Only clocks are validated here. Actual scope IDs come from the full
        # verified parent after generation and can never select a smaller subset.
        for event in ("sale_completed", "return_completed"):
            PortfolioProtocol(
                scopes=(
                    Scope(
                        event_type=event,
                        product_id="00000000-0000-0000-0000-000000000001",
                        selling_location_id="00000000-0000-0000-0000-000000000002",
                        channel="store",
                        currency="PLN",
                    ),
                ),
                train=self.train,
                validation=self.validation,
                test=self.test,
                training_cutoff=self.training_cutoff,
                selection_cutoff=self.selection_cutoff,
            )
        if (
            self.resources.minimum_available_memory_bytes < 1024**3
            or self.resources.minimum_free_disk_bytes < 6 * 1024**3
            or self.resources.tree_rss_bytes > 12 * 1024**3
        ):
            raise ValueError("campaign_anomaly_fit_host_reserve_or_rss_budget")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignAnomalyFitReceipt(Contract):
    version: Literal["ai09-campaign-anomaly-fit-receipt-1.0.0"] = (
        "ai09-campaign-anomaly-fit-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignAnomalyFitPlan
    export_receipt_sha256: Sha256
    runtime_code_sha256: Sha256
    model_id: Annotated[str, Field(pattern=r"^anomaly-detector-sha256-[0-9a-f]{64}$")]
    feature_manifest_sha256: Sha256
    model_artifact_sha256: Sha256
    model_artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def artifact(self) -> Self:
        if (
            set(self.artifact_files)
            != {"model.json", "feature-manifest.json", "parent-completion.json", "plan.json"}
            or self.model_artifact_sha256 != canonical_sha256(self.artifact_files)
            or self.model_artifact_bytes > self.plan.max_artifact_bytes
        ):
            raise ValueError("campaign_anomaly_fit_artifact_identity_or_budget")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
