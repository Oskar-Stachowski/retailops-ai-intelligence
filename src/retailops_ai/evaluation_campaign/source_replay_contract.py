"""Complete source exposure and transform replay are separate from role labels."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.outcome_contract import AccessID, OutcomePopulation
from retailops_ai.evaluation_campaign.partition_contract import ROLES
from retailops_ai.forecasting.contract import Parent


class ForecastSourceReplayPolicy(Contract):
    max_parent_bytes: Literal[268435456] = 268435456
    max_parent_files: Literal[4096] = 4096
    max_rows_per_parent: Annotated[int, Field(ge=1, le=100000)] = 100000
    batch_rows: Literal[256] = 256
    exposure_scope: Literal[
        "all_snapshot_facts_and_all_curated_rows_including_late_purged_and_outside_role_dates"
    ] = "all_snapshot_facts_and_all_curated_rows_including_late_purged_and_outside_role_dates"
    accounting: Literal["reserve_all_five_development_roles_before_any_parent_io"] = (
        "reserve_all_five_development_roles_before_any_parent_io"
    )
    outcome_artifact_identity: Literal["full_curated_manifest_not_scoped_outcome_file"] = (
        "full_curated_manifest_not_scoped_outcome_file"
    )
    replay: Literal["current_pinned_transform_exact_complete_logical_document"] = (
        "current_pinned_transform_exact_complete_logical_document"
    )
    membership_qualification: Literal[
        "declared_metadata_only_no_feature_or_partition_row_replay"
    ] = "declared_metadata_only_no_feature_or_partition_row_replay"
    resource_qualification: Literal["bounded_inputs_and_batches_larger_profiles_unqualified"] = (
        "bounded_inputs_and_batches_larger_profiles_unqualified"
    )


class ForecastSourceReplayProtocol(Contract):
    version: Literal["ai09-forecast-source-replay-1.0.0"] = "ai09-forecast-source-replay-1.0.0"
    schema_version: Literal["1.0.0"] = "1.0.0"
    parent: Parent
    source_parameters: dict[str, JsonValue]
    snapshot_manifest_sha256: Sha256
    curated_manifest_sha256: Sha256
    populations: Annotated[tuple[OutcomePopulation, ...], Field(min_length=5, max_length=5)]
    training_initialization_seed: Annotated[int, Field(ge=0, lt=2**32)]
    replay_recipe_sha256: Sha256
    policy: ForecastSourceReplayPolicy = ForecastSourceReplayPolicy()
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def complete_exposure(self) -> Self:
        seed = self.source_parameters.get("seed")
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("forecast_source_replay_data_seed_required")
        if tuple(p.role for p in self.populations) != ROLES:
            raise ValueError("forecast_source_replay_all_five_roles_required")
        first = self.populations[0]
        if any(
            p.data_seed != seed
            or p.source_dataset_id != self.parent.source_dataset_id
            or p.snapshot_id != self.parent.snapshot_id
            or p.curated_dataset_id != self.parent.curated_dataset_id
            or p.outcome_artifact_sha256 != self.curated_manifest_sha256
            or p.feature_set_id != first.feature_set_id
            or p.partition_id != first.partition_id
            for p in self.populations
        ):
            raise ValueError("forecast_source_replay_population_parent_mismatch")
        return self


class ForecastSourceReplayReceipt(Contract):
    version: Literal["ai09-forecast-source-replay-receipt-1.0.0"] = (
        "ai09-forecast-source-replay-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    access_plan_sha256: Sha256
    access_ids: Annotated[tuple[AccessID, ...], Field(min_length=5, max_length=5)]
    parent: Parent
    runtime_code_sha256: Sha256
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    source_tables: Annotated[int, Field(ge=1)]
    source_rows: Annotated[int, Field(ge=1, le=100000)]
    curated_tables: Annotated[int, Field(ge=1)]
    curated_rows: Annotated[int, Field(ge=1, le=100000)]
    source_qualification: Literal["typed_snapshot_and_complete_curated_transform_replay_passed"] = (
        "typed_snapshot_and_complete_curated_transform_replay_passed"
    )
    external_source_truth: Literal["not_established_by_consumer_replay"] = (
        "not_established_by_consumer_replay"
    )
    exposure_scope: Literal[
        "all_snapshot_facts_and_all_curated_rows_including_late_purged_and_outside_role_dates"
    ] = "all_snapshot_facts_and_all_curated_rows_including_late_purged_and_outside_role_dates"
    cache_authority: Literal["diagnostic_receipt_never_authorizes_a_later_read_without_replay"] = (
        "diagnostic_receipt_never_authorizes_a_later_read_without_replay"
    )
    feature_and_partition_rows_verified: FalseFlag = False
    scoped_outcome_evidence_verified: FalseFlag = False
    project_data_fits: Annotated[int, Field(ge=0, le=0)] = 0
    freshness: Literal["not_established_partial_access_audit"] = (
        "not_established_partial_access_audit"
    )
    evaluation_status: Literal["not_ready"] = "not_ready"
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def unique_accesses(self) -> Self:
        if len(set(self.access_ids)) != 5:
            raise ValueError("forecast_source_replay_duplicate_access_id")
        if self.source_tables != self.curated_tables or self.source_rows != self.curated_rows:
            raise ValueError("forecast_source_replay_row_reconciliation_mismatch")
        return self
