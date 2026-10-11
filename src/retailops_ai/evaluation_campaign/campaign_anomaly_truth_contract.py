"""Versioned ordinary truth read: no invented scenario identity or access permission."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    FalseFlag,
    Sha256,
    SourceID,
    Symbol,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)

MAX_TRUTH_BYTES = 32 * 1024**2


class OrdinarySourceVerification(Contract):
    version: Literal["ai09-complete-ordinary-source-verification-1.0.0"] = (
        "ai09-complete-ordinary-source-verification-1.0.0"
    )
    source_dataset_id: SourceID
    source_schema_version: Literal["2.7.0"] = "2.7.0"
    source_manifest_sha256: Sha256
    source_descriptor_sha256: Sha256
    source_report_sha256: Sha256
    source_table_inventory_sha256: Sha256
    source_tables: Annotated[int, Field(ge=1, le=256)]
    source_rows: Annotated[int, Field(ge=1, le=20000000)]
    producer_commit: CommitSha
    producer_code_sha256: Sha256
    producer_lock_sha256: Sha256
    exporter_lock_sha256: Sha256
    producer_python_version: str = Field(pattern=r"^3\.11\.[0-9]+$")
    resolved_parameters: dict[str, JsonValue]
    window: Window
    complete_windows_sha256: Sha256
    complete_window_count: Annotated[int, Field(ge=1, le=10000)]
    complete_observation_count: Annotated[int, Field(ge=1, le=1000000)]
    population: Literal["all_native_sales_days_and_parent_purchase_return_tail"] = (
        "all_native_sales_days_and_parent_purchase_return_tail"
    )
    source_scenario_sha256: None = None
    native_reader: Literal["data.inventory.source_dataset_io.read_source_dataset"] = (
        "data.inventory.source_dataset_io.read_source_dataset"
    )
    complete_source_and_reports_replayed: Literal[True] = True
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.source_dataset_id != "source-sha256-" + self.source_descriptor_sha256:
            raise ValueError("campaign_ordinary_truth_source_identity")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignOrdinaryTruthPlan(Contract):
    version: Literal["ai09-campaign-ordinary-truth-plan-1.0.0"] = (
        "ai09-campaign-ordinary-truth-plan-1.0.0"
    )
    phase: Literal["development", "final"]
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    window: Window
    resources: CampaignGenerationResources
    population: Literal["all_native_sales_days_and_parent_purchase_return_tail"] = (
        "all_native_sales_days_and_parent_purchase_return_tail"
    )
    max_source_bytes: Annotated[int, Field(ge=1024, le=2 * 1024**3)] = 2 * 1024**3
    max_source_rows: Annotated[int, Field(ge=1, le=20000000)] = 20000000
    max_truth_bytes: Annotated[int, Field(ge=1024, le=MAX_TRUTH_BYTES)] = MAX_TRUTH_BYTES
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def limits(self) -> Self:
        if (
            self.resources.tree_rss_bytes > 12 * 1024**3
            or self.resources.minimum_available_memory_bytes < 1024**3
            or self.resources.minimum_free_disk_bytes < 6 * 1024**3
            or (self.window.end - self.window.start).days > 729
        ):
            raise ValueError("campaign_ordinary_truth_frozen_limits")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignOrdinaryTruthReceipt(Contract):
    version: Literal["ai09-campaign-ordinary-truth-receipt-1.0.0"] = (
        "ai09-campaign-ordinary-truth-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    runtime_code_sha256: Sha256
    plan: CampaignOrdinaryTruthPlan
    generated_parent_receipt_sha256: Sha256
    selection_sha256: Sha256 | None = None
    source_dataset_id: SourceID
    source_verification_sha256: Sha256
    truth_sha256: Sha256
    artifact_files: dict[str, Sha256]
    artifact_bytes: Annotated[int, Field(ge=1, le=MAX_TRUTH_BYTES + 2 * 1024**2)]
    worker_evidence: dict[str, JsonValue]
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def files(self) -> Self:
        if set(self.artifact_files) != {"truth.json", "verification.json", "plan.json"}:
            raise ValueError("campaign_ordinary_truth_bundle_inventory")
        if (self.plan.phase == "final") != (self.selection_sha256 is not None):
            raise ValueError("campaign_ordinary_truth_selection_binding")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))
