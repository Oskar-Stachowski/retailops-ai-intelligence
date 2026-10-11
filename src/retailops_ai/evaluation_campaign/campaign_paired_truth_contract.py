"""One declared paired read, two exact Source parents, one measured total cost."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.paired_source_comparison import POLICY
from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    FalseFlag,
    Sha256,
    SourceID,
    Symbol,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_truth_contract import MAX_TRUTH_BYTES
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)


class PairedSourceParent(Contract):
    source_dataset_id: SourceID
    source_schema_version: Literal["2.7.0", "2.8.0"]
    source_manifest_sha256: Sha256
    source_descriptor_sha256: Sha256
    source_report_sha256: Sha256
    source_table_inventory_sha256: Sha256
    source_tables: Literal[58] = 58
    source_rows: Annotated[int, Field(ge=1, le=20000000)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.source_dataset_id != "source-sha256-" + self.source_descriptor_sha256:
            raise ValueError("campaign_paired_truth_source_identity")
        return self


class PairedSourceVerification(Contract):
    version: Literal["ai09-complete-paired-source-verification-1.0.0"] = (
        "ai09-complete-paired-source-verification-1.0.0"
    )
    ordinary: PairedSourceParent
    planned: PairedSourceParent
    producer_commit: CommitSha
    producer_code_sha256: Sha256
    producer_lock_sha256: Sha256
    exporter_lock_sha256: Sha256
    producer_python_version: str = Field(pattern=r"^3\.11\.[0-9]+$")
    resolved_parameters: dict[str, JsonValue]
    inventory_configuration_sha256: Sha256
    source_context_sha256: Sha256
    source_scenario_sha256: Sha256
    scenario_plan_sha256: Sha256
    window: Window
    comparison_sha256: Sha256
    comparison_policy_sha256: Sha256
    clean_windows_sha256: Sha256
    clean_window_count: Annotated[int, Field(ge=0, le=10000)]
    complete_windows_sha256: Sha256
    complete_window_count: Annotated[int, Field(ge=0, le=10000)]
    complete_observation_count: Annotated[int, Field(ge=0, le=1000000)]
    episodes_sha256: Sha256
    episode_count: Annotated[int, Field(ge=0, le=10000)]
    native_reader: Literal["data.inventory.source_dataset_io.read_source_dataset"] = (
        "data.inventory.source_dataset_io.read_source_dataset"
    )
    both_complete_sources_and_reports_replayed: Literal[True] = True
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def pair(self) -> Self:
        if (
            (self.ordinary.source_schema_version, self.planned.source_schema_version)
            != ("2.7.0", "2.8.0")
            or self.ordinary.source_dataset_id == self.planned.source_dataset_id
            or self.comparison_policy_sha256 != canonical_sha256(POLICY)
            or self.complete_window_count != self.clean_window_count + self.episode_count
        ):
            raise ValueError("campaign_paired_truth_verification_binding")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignPairedTruthPlan(Contract):
    """Both accesses are frozen in this execution recipe before the one reservation.

    The operation belongs to the planned parent. Its ordinary parent is also a
    source in the same protocol, with a completed generation before reservation.
    Neither Source may be read just because its path was supplied by a caller.
    """

    version: Literal["ai09-campaign-paired-truth-plan-1.0.0"] = (
        "ai09-campaign-paired-truth-plan-1.0.0"
    )
    phase: Literal["development", "final"]
    source_recipe_sha256: Sha256
    ordinary_source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    ordinary_generation_operation_id: Symbol
    scenario_plan_sha256: Sha256
    window: Window
    resources: CampaignGenerationResources
    native_source_reads: Literal[2] = 2
    max_source_bytes: Annotated[int, Field(ge=1024, le=2 * 1024**3)] = 2 * 1024**3
    max_source_rows: Annotated[int, Field(ge=1, le=20000000)] = 20000000
    max_truth_bytes: Annotated[int, Field(ge=1024, le=MAX_TRUTH_BYTES)] = MAX_TRUTH_BYTES
    quality_qualified: FalseFlag = False

    @model_validator(mode="after")
    def pair(self) -> Self:
        if (
            self.source_recipe_sha256 == self.ordinary_source_recipe_sha256
            or self.generation_operation_id == self.ordinary_generation_operation_id
            or self.resources.tree_rss_bytes > 12 * 1024**3
            or self.resources.scratch_bytes > 8 * 1024**3
            or self.resources.minimum_available_memory_bytes < 1024**3
            or self.resources.minimum_free_disk_bytes < 6 * 1024**3
            or (self.window.end - self.window.start).days > 729
        ):
            raise ValueError("campaign_paired_truth_frozen_pair_or_limits")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignPairedTruthReceipt(Contract):
    version: Literal["ai09-campaign-paired-truth-receipt-1.0.0"] = (
        "ai09-campaign-paired-truth-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    runtime_code_sha256: Sha256
    plan: CampaignPairedTruthPlan
    generated_parent_receipt_sha256: Sha256
    ordinary_generation_receipt_sha256: Sha256
    selection_sha256: Sha256 | None = None
    source_dataset_id: SourceID
    ordinary_source_dataset_id: SourceID
    source_verification_sha256: Sha256
    truth_sha256: Sha256
    artifact_files: dict[str, Sha256]
    artifact_bytes: Annotated[int, Field(ge=1, le=MAX_TRUTH_BYTES + 4 * 1024**2)]
    worker_evidence: dict[str, JsonValue]
    native_source_reads: Literal[2] = 2
    cost_scope: Literal["one_total_for_both_native_reads_comparison_and_publication"] = (
        "one_total_for_both_native_reads_comparison_and_publication"
    )
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def files(self) -> Self:
        if set(self.artifact_files) != {
            "truth.json",
            "verification.json",
            "comparison.json",
            "plan.json",
        }:
            raise ValueError("campaign_paired_truth_bundle_inventory")
        if (self.plan.phase == "final") != (
            self.selection_sha256 is not None
        ) or self.source_dataset_id == self.ordinary_source_dataset_id:
            raise ValueError("campaign_paired_truth_selection_or_pair_binding")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))
