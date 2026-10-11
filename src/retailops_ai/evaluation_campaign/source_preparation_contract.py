"""Prospective options for original native preparation; no model/final authority."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.development_policy_freeze import FrozenDevelopmentPolicies
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy


class SourcePreparationOptions(Contract):
    version: Literal["ai09-original-source-preparation-options-1.0.0"] = (
        "ai09-original-source-preparation-options-1.0.0"
    )
    products: Literal[25, 50, 100]
    producer: str
    producer_python: str
    partitions: ForecastPartitionPolicy
    generation_resources: CampaignGenerationResources
    planning_resources: CampaignGenerationResources
    reuse_resources: CampaignGenerationResources
    maximum_total_wall_seconds: Annotated[int, Field(ge=5, le=10800)] = 10800
    final_test_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def bounded(self) -> Self:
        for value in (self.producer, self.producer_python):
            if (
                not Path(value).is_absolute()
                or str(Path(value)) != value
                or ".." in Path(value).parts
            ):
                raise ValueError("source_preparation_absolute_normalized_paths_required")
        for resources in (self.generation_resources, self.planning_resources, self.reuse_resources):
            if (
                resources.wall_seconds > self.maximum_total_wall_seconds
                or resources.tree_rss_bytes > 12 * 1024**3
                or resources.scratch_bytes > 8 * 1024**3
                or resources.minimum_available_memory_bytes < 1024**3
                or resources.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("source_preparation_original_resource_ceiling")
        return self


class SourcePreparationProducerPin(Contract):
    producer_commit: CommitSha
    producer_lock_sha256: Sha256
    exporter_lock_sha256: Sha256
    producer_planner_sha256: Sha256


class SourcePreparationSession(Contract):
    version: Literal["ai09-original-source-preparation-session-1.0.0"] = (
        "ai09-original-source-preparation-session-1.0.0"
    )
    root: str
    options: SourcePreparationOptions
    policies: FrozenDevelopmentPolicies
    producer_pin: SourcePreparationProducerPin

    @model_validator(mode="after")
    def original_policy_producer(self) -> Self:
        if (
            not Path(self.root).is_absolute()
            or str(Path(self.root)) != self.root
            or ".." in Path(self.root).parts
        ):
            raise ValueError("source_preparation_absolute_session_root_required")
        self.policies.require_producer(self.producer_pin.producer_commit)
        return self
