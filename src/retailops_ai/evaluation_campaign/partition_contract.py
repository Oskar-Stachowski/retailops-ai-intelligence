"""Five chronological development roles; key preparation grants no outcome access."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DateWindow,
    FalseFlag,
    FeatureID,
    ForecastKey,
    Sha256,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.forecasting.manifest_contract import FeatureDescriptor

DevelopmentRole = Literal[
    "train", "early_stopping", "tune", "calibration", "development_evaluation"
]
PartitionRole = Literal[
    "train", "early_stopping", "tune", "calibration", "development_evaluation", "purged"
]
Purpose = Literal[
    "preprocessing_fit",
    "model_fit",
    "early_stopping",
    "recipe_selection",
    "calibrator_fit",
    "independent_evaluation",
]
ROLES: tuple[DevelopmentRole, ...] = (
    "train",
    "early_stopping",
    "tune",
    "calibration",
    "development_evaluation",
)
ALL_ROLES: tuple[PartitionRole, ...] = (*ROLES, "purged")


class RoleWindow(Contract):
    role: DevelopmentRole
    origins: DateWindow
    label_knowledge_cutoff: UtcTime


class ForecastPartitionPolicy(Contract):
    version: Literal["ai09-independent-forecast-partitions-1.0.0"] = (
        "ai09-independent-forecast-partitions-1.0.0"
    )
    roles: tuple[RoleWindow, ...] = Field(min_length=5, max_length=5)
    max_horizon_days: Literal[14] = 14
    label_delay_days: Annotated[int, Field(ge=1, le=30)] = 1
    purge_days: Annotated[int, Field(ge=15, le=90)] = 15
    preprocessing: Literal["fit_on_train_only"] = "fit_on_train_only"
    calibration: Literal["fit_after_recipe_selection_on_calibration_only"] = (
        "fit_after_recipe_selection_on_calibration_only"
    )
    independence: Literal["chronological_roles_not_a_statistical_independence_claim"] = (
        "chronological_roles_not_a_statistical_independence_claim"
    )
    holdout_freshness: Literal["not_asserted_requires_outcome_access_audit"] = (
        "not_asserted_requires_outcome_access_audit"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    max_population_rows: Annotated[int, Field(ge=5, le=100000)] = 100000
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=128 * 1024**2)] = 128 * 1024**2
    max_index_bytes: Annotated[int, Field(ge=4096, le=128 * 1024**2)] = 128 * 1024**2
    development_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if tuple(r.role for r in self.roles) != ROLES:
            raise ValueError("forecast_partition_role_inventory_or_order")
        maturity = self.max_horizon_days + self.label_delay_days
        if self.purge_days < maturity:
            raise ValueError("forecast_partition_purge_shorter_than_maturity")
        for index, current in enumerate(self.roles):
            if current.label_knowledge_cutoff < end_of_day(
                current.origins.end + timedelta(days=maturity)
            ):
                raise ValueError("forecast_partition_label_cutoff_before_maturity")
            if index + 1 < len(self.roles):
                following = self.roles[index + 1]
                if following.origins.start <= current.origins.end + timedelta(days=self.purge_days):
                    raise ValueError("forecast_partition_overlap_or_short_purge")
                if current.label_knowledge_cutoff >= end_of_day(following.origins.start):
                    raise ValueError("forecast_partition_label_cutoff_crosses_next_role")
        return self

    def role_for(self, day: date) -> PartitionRole:
        for role in self.roles:
            if role.origins.start <= day <= role.origins.end:
                return role.role
        return "purged"

    def cutoff_for(self, role: PartitionRole) -> UtcTime | None:
        return next((r.label_knowledge_cutoff for r in self.roles if r.role == role), None)


class PartitionMembership(ForecastKey):
    role: PartitionRole
    label_knowledge_cutoff: UtcTime | None
    feature_row_sha256: Sha256

    @model_validator(mode="after")
    def no_purged_outcome(self) -> Self:
        if (self.role == "purged") != (self.label_knowledge_cutoff is None):
            raise ValueError("forecast_partition_role_cutoff_missing_or_unexpected")
        return self


class PartitionFile(Contract):
    row_count: Annotated[int, Field(ge=0)]
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Sha256
    keys_sha256: Sha256


class PartitionDescriptor(Contract):
    policy: ForecastPartitionPolicy
    feature_set_id: FeatureID
    feature_descriptor: FeatureDescriptor
    runtime: PreparationRuntime
    populations: dict[PartitionRole, PartitionFile]
    outcome_eligibility: Literal["not_checked_no_label_files_read"] = (
        "not_checked_no_label_files_read"
    )
    evaluation_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def complete_population(self) -> Self:
        if (
            set(self.populations) != set(ALL_ROLES)
            or any(self.populations[r].row_count == 0 for r in ROLES)
            or sum(f.row_count for f in self.populations.values())
            != self.feature_descriptor.row_count
            or self.feature_descriptor.row_count > self.policy.max_population_rows
            or sum(f.size_bytes for f in self.populations.values()) > self.policy.max_artifact_bytes
        ):
            raise ValueError("forecast_partition_population_incomplete_or_over_budget")
        if self.feature_set_id != "features-sha256-" + canonical_sha256(
            self.feature_descriptor.model_dump(mode="json")
        ):
            raise ValueError("forecast_partition_feature_identity_mismatch")
        if self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files):
            raise ValueError("forecast_partition_runtime_identity_mismatch")
        return self


class ForecastPartitionManifest(Contract):
    schema_version: Literal["ai09-independent-forecast-partitions-1.0.0"] = (
        "ai09-independent-forecast-partitions-1.0.0"
    )
    partition_id: Annotated[str, Field(pattern=r"^ai09-partitions-sha256-[0-9a-f]{64}$")]
    descriptor: PartitionDescriptor
    labels_accessed: FalseFlag = False
    development_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.partition_id != "ai09-partitions-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("forecast_partition_identity_mismatch")
        if (
            sum(f.size_bytes for f in self.descriptor.populations.values())
            + len(canonical_bytes(self.model_dump(mode="json")))
            + 1
            > self.descriptor.policy.max_artifact_bytes
        ):
            raise ValueError("forecast_partition_complete_artifact_budget")
        return self
