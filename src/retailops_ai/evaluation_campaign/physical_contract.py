"""Exact physical parent binding; resource ceilings are not measured qualification."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.label_contract import QualifiedForecastOutcome
from retailops_ai.evaluation_campaign.partition_contract import (
    ALL_ROLES,
    ROLES,
    ForecastPartitionPolicy,
    PartitionFile,
    PartitionMembership,
    PartitionRole,
    RoleWindow,
)
from retailops_ai.forecasting.contract import OriginWindow, Parent
from retailops_ai.forecasting.manifest_contract import FeatureDescriptor, FeaturePolicy


class PhysicalSourceSpec(Contract):
    schema_version: Literal["1.0.0", "1.1.0", "1.2.0"]
    parent: Parent
    source_parameters: dict[str, JsonValue]
    snapshot_manifest_sha256: Sha256
    curated_manifest_sha256: Sha256
    max_parent_bytes: Annotated[int, Field(ge=1024, le=2 * 1024**3)] = 2 * 1024**3
    max_parent_files: Annotated[int, Field(ge=2, le=10000)] = 10000
    max_rows_per_parent: Annotated[int, Field(ge=1, le=20000000)] = 20000000
    batch_rows: Annotated[int, Field(ge=1, le=8192)] = 256
    resource_qualified: FalseFlag = False
    execution_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False


class PhysicalForecastRecipe(Contract):
    """New physical wire; legacy population and index budgets stay unchanged."""

    version: Literal["ai09-physical-development-forecast-1.0.0"] = (
        "ai09-physical-development-forecast-1.0.0"
    )
    source: PhysicalSourceSpec
    origins: OriginWindow
    roles: tuple[RoleWindow, ...] = Field(min_length=5, max_length=5)
    purge_days: Annotated[int, Field(ge=15, le=90)] = 15
    label_delay_days: Annotated[int, Field(ge=1, le=30)] = 1
    features: FeaturePolicy = FeaturePolicy()
    max_population_rows: Annotated[int, Field(ge=5, le=10000000)] = 10000000
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=4096, le=65536)] = 32768
    sqlite_cache_kib: Literal[4096] = 4096
    exposure: Literal["complete_parent_including_late_and_out_of_role_rows"] = (
        "complete_parent_including_late_and_out_of_role_rows"
    )
    resource_qualified: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    def temporal_policy(self) -> ForecastPartitionPolicy:
        # Reuse chronological validation only. Legacy storage caps never
        # qualify this new, separately bounded physical representation.
        return ForecastPartitionPolicy(
            roles=self.roles,
            purge_days=self.purge_days,
            label_delay_days=self.label_delay_days,
        )

    @model_validator(mode="after")
    def chronology(self) -> Self:
        self.temporal_policy()
        if (
            self.origins.start != self.roles[0].origins.start
            or self.origins.end != self.roles[-1].origins.end
        ):
            raise ValueError("physical_forecast_origins_must_cover_exact_role_span")
        parameters = self.source.source_parameters
        start, end = parameters.get("start_date"), parameters.get("end_date")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("physical_forecast_explicit_source_history_required")
        history_start, history_end = date.fromisoformat(start), date.fromisoformat(end)
        if (
            history_start > self.origins.start
            or self.origins.end > history_end
            or any(r.label_knowledge_cutoff > end_of_day(history_end) for r in self.roles)
        ):
            raise ValueError("physical_forecast_roles_or_label_cutoffs_outside_source_history")
        return self


class PhysicalForecastExample(Contract):
    membership: PartitionMembership
    outcome: QualifiedForecastOutcome | None

    @model_validator(mode="after")
    def binding(self) -> Self:
        from retailops_ai.evaluation_campaign.partitions import membership_key

        if self.membership.role == "purged":
            if self.outcome is not None:
                raise ValueError("physical_forecast_purged_has_outcome")
        elif self.outcome is None or (
            membership_key(self.membership) != membership_key(self.outcome.label)
            or self.membership.role != self.outcome.label.role
            or self.membership.label_knowledge_cutoff != self.outcome.label.knowledge_cutoff
            or self.membership.feature_row_sha256 != self.outcome.feature_row_sha256
        ):
            raise ValueError("physical_forecast_example_binding_mismatch")
        return self


class PhysicalRoleFile(PartitionFile):
    eligible_rows: Annotated[int, Field(ge=0)]
    censored_rows: Annotated[int, Field(ge=0)]
    zero_label_rows: Annotated[int, Field(ge=0)]
    eligibility_reasons: dict[str, Annotated[int, Field(ge=1)]]

    @model_validator(mode="after")
    def coverage(self) -> Self:
        if (
            max(
                self.eligible_rows,
                self.censored_rows,
                self.zero_label_rows,
                *self.eligibility_reasons.values(),
                0,
            )
            > self.row_count
            or self.eligible_rows + self.censored_rows > self.row_count
        ):
            raise ValueError("physical_forecast_role_coverage_mismatch")
        if (
            set(self.eligibility_reasons)
            - {
                "insufficient_history",
                "stale_history",
                "unknown_calendar",
                "closed_target",
                "censored_label",
            }
            or self.eligibility_reasons.get("censored_label", 0) != self.censored_rows
        ):
            raise ValueError("physical_forecast_role_reason_inventory_mismatch")
        return self


class PhysicalForecastDescriptor(Contract):
    recipe: PhysicalForecastRecipe
    runtime: PreparationRuntime
    feature_set_id: str
    feature_descriptor: FeatureDescriptor
    populations: dict[PartitionRole, PhysicalRoleFile]
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    observation_rows: Annotated[int, Field(ge=1)]
    version_rows: Annotated[int, Field(ge=1)]
    version_inventory_sha256: Sha256
    version_quality_policy: Literal["exact_latest_quality_at_known_time_no_older_fallback"] = (
        "exact_latest_quality_at_known_time_no_older_fallback"
    )
    feature_and_label_binding: Literal["rebuilt_from_one_complete_private_source_replay"] = (
        "rebuilt_from_one_complete_private_source_replay"
    )

    @model_validator(mode="after")
    def population(self) -> Self:
        if (
            set(self.populations) != set(ALL_ROLES)
            or any(self.populations[r].row_count < 1 for r in ROLES)
            or sum(r.row_count for r in self.populations.values())
            != self.feature_descriptor.row_count
            or self.feature_descriptor.row_count > self.recipe.max_population_rows
            or self.feature_descriptor.parent != self.recipe.source.parent
            or self.feature_descriptor.source_parameters != self.recipe.source.source_parameters
            or self.feature_descriptor.resolved_policy != self.recipe.features
            or self.feature_set_id
            != "features-sha256-"
            + canonical_sha256(self.feature_descriptor.model_dump(mode="json"))
            or self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files)
            or not self.observation_rows
            <= self.version_rows
            <= self.recipe.source.max_rows_per_parent
        ):
            raise ValueError("physical_forecast_descriptor_population_or_parent_mismatch")
        purged = self.populations["purged"]
        if (
            purged.eligible_rows
            or purged.censored_rows
            or purged.zero_label_rows
            or purged.eligibility_reasons
        ):
            raise ValueError("physical_forecast_purged_coverage")
        return self


class PhysicalForecastManifest(Contract):
    version: Literal["ai09-physical-development-forecast-manifest-1.0.0"] = (
        "ai09-physical-development-forecast-manifest-1.0.0"
    )
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    descriptor: PhysicalForecastDescriptor
    campaign_audit_qualified: FalseFlag = False
    holdout_freshness_qualified: FalseFlag = False
    resource_qualified: FalseFlag = False
    quality_qualified: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.dataset_id != "ai09-physical-forecast-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("physical_forecast_identity_mismatch")
        return self
