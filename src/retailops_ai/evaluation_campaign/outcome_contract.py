"""Outcome access records: partial history never establishes an untouched holdout."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    DateWindow,
    FalseFlag,
    FeatureID,
    Sha256,
    SourceID,
    SplitID,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.partition_contract import DevelopmentRole
from retailops_ai.evaluation_campaign.trial_contract import OutputPath
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.manifest_contract import FoldPlan

AccessID = Annotated[str, Field(pattern=r"^outcome-access-[0-9a-f]{32}$")]
AccessPurpose = Literal[
    "preprocessing_fit",
    "model_fit",
    "early_stopping",
    "recipe_selection",
    "calibrator_fit",
    "verification",
    "independent_evaluation",
]


class HistoricalOutcomeProtocol(Contract):
    parent: Parent
    feature_set_id: FeatureID
    split_id: SplitID
    fold: FoldPlan
    source_parameters: dict[str, JsonValue]
    existing_development_holdout_freshness: Literal[
        "opened_during_parent_verification_not_untouched"
    ]
    provenance: Literal["retrospective_metadata_and_preparation_receipt_not_pre_read_journal"]

    @model_validator(mode="after")
    def data_seed(self) -> Self:
        seed = self.source_parameters.get("seed")
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError("outcome_history_data_seed_required")
        return self


class HistoricalOutcomeAttempt(Contract):
    output: OutputPath
    protocol_file_sha256: Sha256
    protocol_sha256: Sha256
    status: Literal["completed_development_diagnostic", "failed", "interrupted", "unresolved"]


class HistoricalOutcomeInventory(Contract):
    """Exact metadata-only declaration from 09.6, retaining every original attempt."""

    scope: Literal["historical_outcome_access_declaration_from_existing_metadata_only"]
    source_ledger: OutputPath
    source_ledger_sha256: Sha256
    source_preparation_receipt: OutputPath
    source_preparation_sha256: Sha256
    observed_existing_attempts: Annotated[
        tuple[HistoricalOutcomeAttempt, ...], Field(max_length=64)
    ]
    observed_protocols: dict[Sha256, HistoricalOutcomeProtocol]
    freshness_of_unlisted_data: Literal["unknown_not_automatically_unseen"]
    prior_global_access_audit_completed: FalseFlag
    historical_files_mutated: FalseFlag
    new_project_labels_read: FalseFlag
    project_data_fits: Annotated[int, Field(ge=0, le=0)]
    portfolio_final_test_authorized: FalseFlag

    @model_validator(mode="after")
    def complete_inventory(self) -> Self:
        attempts = self.observed_existing_attempts
        if (
            not attempts
            or len({a.output for a in attempts}) != len(attempts)
            or {a.protocol_sha256 for a in attempts} != set(self.observed_protocols)
            or len(self.observed_protocols) > 64
        ):
            raise ValueError("outcome_history_incomplete_protocol_inventory")
        return self


class OutcomePopulation(Contract):
    use_case: Literal["forecast"] = "forecast"
    data_seed: Annotated[int, Field(ge=0, lt=2**32)]
    source_dataset_id: SourceID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    partition_id: Annotated[str, Field(pattern=r"^ai09-partitions-sha256-[0-9a-f]{64}$")]
    role: DevelopmentRole
    origins: DateWindow
    label_knowledge_cutoff: UtcTime
    membership_keys_sha256: Sha256
    outcome_artifact_sha256: Sha256


class OutcomeAccessBinding(Contract):
    population: OutcomePopulation
    purpose: AccessPurpose
    training_initialization_seed: Annotated[int, Field(ge=0, lt=2**32)]
    recipe_sha256: Sha256
    fitted_candidate_sha256: Sha256 | None = None
    calibrator_sha256: Sha256 | None = None
    thresholds_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def role_purpose(self) -> Self:
        roles = {
            "preprocessing_fit": "train",
            "model_fit": "train",
            "early_stopping": "early_stopping",
            "recipe_selection": "tune",
            "calibrator_fit": "calibration",
            "independent_evaluation": "development_evaluation",
        }
        if self.purpose != "verification" and self.population.role != roles[self.purpose]:
            raise ValueError("outcome_access_role_purpose_mismatch")
        if self.purpose == "calibrator_fit" and self.fitted_candidate_sha256 is None:
            raise ValueError("outcome_access_calibration_requires_fitted_candidate")
        return self


class OutcomeAccessPlan(Contract):
    version: Literal["ai09-outcome-access-plan-1.0.0"] = "ai09-outcome-access-plan-1.0.0"
    protocol_sha256: Sha256
    runtime: PreparationRuntime
    bindings: Annotated[tuple[OutcomeAccessBinding, ...], Field(min_length=1, max_length=16)]
    qualification: Literal["development_diagnostic_only_no_freshness_or_quality_acceptance"] = (
        "development_diagnostic_only_no_freshness_or_quality_acceptance"
    )
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files):
            raise ValueError("outcome_access_plan_runtime_identity_mismatch")
        hashes = [canonical_sha256(b.model_dump(mode="json")) for b in self.bindings]
        if len(set(hashes)) != len(hashes):
            raise ValueError("outcome_access_duplicate_binding")
        if any(b.purpose == "independent_evaluation" for b in self.bindings):
            raise ValueError("outcome_independent_evaluation_requires_complete_access_audit")
        return self


class OutcomeJournalPolicy(Contract):
    version: Literal["ai09-outcome-journal-policy-1.0.0"] = "ai09-outcome-journal-policy-1.0.0"
    journal_path: OutputPath
    historical_inventory_path: OutputPath
    historical_inventory_file_sha256: Sha256
    history: HistoricalOutcomeInventory
    runtime: PreparationRuntime
    audit_code_sha256: Sha256
    maximum_plans: Annotated[int, Field(ge=1, le=32)] = 16
    maximum_new_reads: Annotated[int, Field(ge=1, le=128)] = 64
    maximum_reads_per_binding: Annotated[int, Field(ge=1, le=16)] = 4
    audit_scope: Literal["cooperating_development_readers_partial_historical_inventory"] = (
        "cooperating_development_readers_partial_historical_inventory"
    )
    failure_policy: Literal["reserved_failed_or_unresolved_reads_remain_potentially_exposed"] = (
        "reserved_failed_or_unresolved_reads_remain_potentially_exposed"
    )
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def runtime_identity(self) -> Self:
        if self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files):
            raise ValueError("outcome_journal_runtime_identity_mismatch")
        return self


class OutcomeAccessEvent(Contract):
    sequence: Annotated[int, Field(ge=1, le=288)]
    previous_sha256: Sha256
    at: UtcTime
    kind: Literal["plan_registered", "reserved", "finished"]
    access_plan_sha256: Sha256
    plan: OutcomeAccessPlan | None = None
    binding_sha256: Sha256 | None = None
    access_id: AccessID | None = None
    result: Literal["completed", "failed"] | None = None
    error_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,128}$")] | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.kind == "plan_registered":
            if (
                self.plan is None
                or self.access_plan_sha256 != canonical_sha256(self.plan.model_dump(mode="json"))
                or any(
                    v is not None
                    for v in (self.binding_sha256, self.access_id, self.result, self.error_code)
                )
            ):
                raise ValueError("outcome_journal_invalid_plan_event")
        elif self.plan is not None or self.binding_sha256 is None or self.access_id is None:
            raise ValueError("outcome_journal_missing_access_binding")
        elif self.kind == "reserved" and (self.result is not None or self.error_code is not None):
            raise ValueError("outcome_journal_reservation_has_result")
        elif self.kind == "finished" and (
            self.result is None or (self.result == "failed") != (self.error_code is not None)
        ):
            raise ValueError("outcome_journal_finish_missing_result")
        return self


class OutcomeJournal(Contract):
    version: Literal["ai09-outcome-journal-1.0.0"] = "ai09-outcome-journal-1.0.0"
    policy: OutcomeJournalPolicy
    events: Annotated[tuple[OutcomeAccessEvent, ...], Field(max_length=288)] = ()
    head_sha256: Sha256

    @model_validator(mode="after")
    def transitions(self) -> Self:
        head = canonical_sha256(self.policy.model_dump(mode="json"))
        plans: dict[str, OutcomeAccessPlan] = {}
        reserved: dict[str, OutcomeAccessEvent] = {}
        finished: set[str] = set()
        counts: dict[str, int] = {}
        for sequence, event in enumerate(self.events, 1):
            if event.sequence != sequence or event.previous_sha256 != head:
                raise ValueError("outcome_journal_chain_mismatch")
            if event.kind == "plan_registered":
                if event.plan is None or event.access_plan_sha256 in plans:
                    raise ValueError("outcome_journal_duplicate_plan")
                plans[event.access_plan_sha256] = event.plan
            else:
                plan = plans.get(event.access_plan_sha256)
                if (
                    plan is None
                    or event.binding_sha256
                    not in {canonical_sha256(b.model_dump(mode="json")) for b in plan.bindings}
                    or event.access_id is None
                ):
                    raise ValueError("outcome_journal_unplanned_access")
                if event.kind == "reserved":
                    if event.access_id in reserved:
                        raise ValueError("outcome_journal_duplicate_access")
                    reserved[event.access_id] = event
                    # The same binding under another protocol cannot reset the read budget.
                    binding = str(event.binding_sha256)
                    counts[binding] = counts.get(binding, 0) + 1
                else:
                    start = reserved.get(event.access_id)
                    if (
                        start is None
                        or event.access_id in finished
                        or start.access_plan_sha256 != event.access_plan_sha256
                        or start.binding_sha256 != event.binding_sha256
                    ):
                        raise ValueError("outcome_journal_invalid_finish")
                    finished.add(event.access_id)
            head = canonical_sha256(event.model_dump(mode="json"))
        if (
            head != self.head_sha256
            or len(plans) > self.policy.maximum_plans
            or len(reserved) > self.policy.maximum_new_reads
            or any(n > self.policy.maximum_reads_per_binding for n in counts.values())
        ):
            raise ValueError("outcome_journal_head_or_budget_mismatch")
        return self
