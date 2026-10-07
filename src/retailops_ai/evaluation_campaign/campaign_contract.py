"""A new prospective campaign's ordering and budget, not quality or freshness proof."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    DateWindow,
    FalseFlag,
    Sha256,
    Symbol,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.contract import SCENARIOS, USE_CASES, PreparationRuntime
from retailops_ai.evaluation_campaign.legacy_carryover import LegacyCampaignCarryover

Phase = Literal["development", "final"]
Action = Literal["source_generate", "source_read", "model_fit", "calibrator_fit", "model_score"]
UseCase = Literal["source", "forecast", "anomaly", "stockout"]
Role = Literal[
    "all_parent_data",
    "train",
    "early_stopping",
    "tune",
    "calibration",
    "development_evaluation",
    "final_evaluation",
]


class CampaignSourceRecipe(Contract):
    """A planned source scope; this is not a generated or qualified dataset."""

    phase: Phase
    seed: Literal[42, 137, 2026]
    producer_commit: CommitSha
    producer_lock_sha256: Sha256
    exporter_lock_sha256: Sha256 | None = None
    generation_config_sha256: Sha256
    profile: Literal["ai-dev", "ai-training"]
    history: DateWindow
    products: Annotated[int, Field(ge=1)]
    selling_pairs: Annotated[int, Field(ge=1)]
    stock_locations: Annotated[int, Field(ge=1)]
    evaluation_origins: DateWindow | None = None
    label_delay_days: Annotated[int, Field(ge=1, le=30)] = 1

    @model_validator(mode="after")
    def canonical_profile(self) -> Self:
        days = (self.history.end - self.history.start).days + 1
        size = (days, self.products, self.selling_pairs, self.stock_locations)
        if self.phase == "development":
            if self.seed != 42 or self.profile != "ai-dev" or size != (365, 100, 5, 3):
                raise ValueError("campaign_development_profile_mismatch")
            if self.evaluation_origins is not None:
                raise ValueError("campaign_development_does_not_declare_final_origins")
        else:
            if self.profile != "ai-training" or size != (730, 200, 10, 4):
                raise ValueError("campaign_final_profile_mismatch")
            origins = self.evaluation_origins
            if (
                origins is None
                or origins.start < self.history.start
                or origins.end + timedelta(days=14 + self.label_delay_days) > self.history.end
            ):
                raise ValueError("campaign_final_origins_or_maturity_invalid")
        return self

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignOperationPlan(Contract):
    operation_id: Symbol
    phase: Phase
    action: Action
    use_case: UseCase
    role: Role
    source_recipe_sha256: Sha256
    execution_recipe_sha256: Sha256
    forecast_family: Literal["rf", "hgb", "tensorflow"] | None = None
    initialization_seed: Annotated[int, Field(ge=0, le=2**31 - 1)] | None = None
    maximum_attempts: Annotated[int, Field(ge=1, le=3)] = 1
    prerequisites: tuple[Symbol, ...] = ()

    @model_validator(mode="after")
    def purpose(self) -> Self:
        if self.action in ("source_generate", "source_read"):
            if self.use_case != "source" or self.role != "all_parent_data":
                raise ValueError("campaign_parent_exposure_must_be_explicit")
        elif self.use_case == "source" or self.role == "all_parent_data":
            raise ValueError("campaign_model_operation_requires_use_case_and_role")
        if self.action == "source_generate" and self.maximum_attempts != 1:
            raise ValueError("campaign_generation_cannot_be_silently_repeated")
        if self.action == "model_fit" and (self.phase != "development" or self.role != "train"):
            raise ValueError("campaign_model_fit_only_on_development_train")
        if self.action == "calibrator_fit" and (
            self.phase != "development" or self.role != "calibration"
        ):
            raise ValueError("campaign_calibrator_fit_only_on_development_calibration")
        if (self.action == "model_fit") != (self.initialization_seed is not None):
            raise ValueError("campaign_initialization_seed_required_for_model_fit_only")
        if (self.action == "model_fit" and self.use_case == "forecast") != (
            self.forecast_family is not None
        ):
            raise ValueError("campaign_forecast_fit_family_required")
        if self.phase == "final" and self.role not in ("all_parent_data", "final_evaluation"):
            raise ValueError("campaign_final_operation_role_mismatch")
        if self.phase == "development" and self.role == "final_evaluation":
            raise ValueError("campaign_development_cannot_read_final_role")
        if len(set(self.prerequisites)) != len(self.prerequisites):
            raise ValueError("campaign_duplicate_prerequisite")
        return self


class CampaignProtocol(Contract):
    version: Literal["ai09-prospective-campaign-1.0.0"] = "ai09-prospective-campaign-1.0.0"
    scope: Literal["new_prospective_scope_with_unavailable_legacy_budgets"] = (
        "new_prospective_scope_with_unavailable_legacy_budgets"
    )
    journal_path: Annotated[str, Field(pattern=r"^/", min_length=2, max_length=1024)]
    legacy: LegacyCampaignCarryover
    legacy_sha256: Sha256
    runtime: PreparationRuntime
    sources: Annotated[tuple[CampaignSourceRecipe, ...], Field(min_length=4, max_length=4)]
    operations: Annotated[tuple[CampaignOperationPlan, ...], Field(min_length=7, max_length=256)]
    selection_policy_sha256: Sha256
    use_case_quality_policy_sha256: dict[Literal["forecast", "anomaly", "stockout"], Sha256]
    segment_policy_sha256: Sha256
    uncertainty_policy_sha256: Sha256
    data_seeds: tuple[Literal[42], Literal[137], Literal[2026]] = (42, 137, 2026)
    seed_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]
    scenario_weights: dict[str, Annotated[int, Field(ge=1, le=1000)]]
    maximum_new_attempts: Annotated[int, Field(ge=7, le=768)]
    maximum_new_fit_attempts: Annotated[int, Field(ge=0, le=256)]
    final_test_freshness: Literal["requires_separate_generation_and_access_verification"] = (
        "requires_separate_generation_and_access_verification"
    )
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def freeze_scope(self) -> Self:
        if self.legacy_sha256 != self.legacy.content_sha256():
            raise ValueError("campaign_legacy_binding_mismatch")
        if self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files):
            raise ValueError("campaign_runtime_identity_mismatch")
        if set(self.use_case_quality_policy_sha256) != set(USE_CASES):
            raise ValueError("campaign_requires_three_quality_policies")
        if set(self.seed_weights) != {str(s) for s in self.data_seeds} or set(
            self.scenario_weights
        ) != set(SCENARIOS):
            raise ValueError("campaign_complete_seed_and_scenario_weights_required")
        development = [s for s in self.sources if s.phase == "development"]
        finals = [s for s in self.sources if s.phase == "final"]
        if len(development) != 1 or tuple(s.seed for s in finals) != self.data_seeds:
            raise ValueError("campaign_source_inventory_mismatch")
        if len({(s.producer_commit, s.producer_lock_sha256) for s in self.sources}) != 1:
            raise ValueError("campaign_sources_require_one_producer_revision")
        if len({(s.history, s.evaluation_origins, s.label_delay_days) for s in finals}) != 1:
            raise ValueError("campaign_final_seed_windows_must_match")
        if any(
            s.evaluation_origins is None
            or s.evaluation_origins.start
            <= development[0].history.end + timedelta(days=14 + s.label_delay_days)
            for s in finals
        ):
            raise ValueError("campaign_final_origins_overlap_development_exposure")
        sources = {s.content_sha256(): s for s in self.sources}
        plans: dict[str, CampaignOperationPlan] = {}
        for operation in self.operations:
            if operation.operation_id in plans:
                raise ValueError("campaign_duplicate_operation")
            source = sources.get(operation.source_recipe_sha256)
            if source is None or source.phase != operation.phase:
                raise ValueError("campaign_operation_source_scope_mismatch")
            if any(p not in plans for p in operation.prerequisites):
                raise ValueError("campaign_prerequisites_require_topological_order")
            if any(
                plans[p].phase != operation.phase
                or plans[p].source_recipe_sha256 != operation.source_recipe_sha256
                for p in operation.prerequisites
            ):
                raise ValueError("campaign_prerequisite_source_scope_mismatch")
            plans[operation.operation_id] = operation
        for digest in sources:
            generation = [
                o
                for o in self.operations
                if o.source_recipe_sha256 == digest and o.action == "source_generate"
            ]
            if len(generation) != 1 or generation[0].prerequisites:
                raise ValueError("campaign_requires_one_root_generation_per_source")
            reached = {generation[0].operation_id}
            for operation in self.operations:
                if (
                    operation.source_recipe_sha256 == digest
                    and operation.action != "source_generate"
                ):
                    if not any(p in reached for p in operation.prerequisites):
                        raise ValueError("campaign_operation_requires_source_generation")
                    reached.add(operation.operation_id)
            if sources[digest].phase == "final" and {
                o.use_case
                for o in self.operations
                if o.source_recipe_sha256 == digest
                and o.action == "model_score"
                and o.role == "final_evaluation"
            } != set(USE_CASES):
                raise ValueError("campaign_final_source_requires_three_use_case_evaluations")
        if self.maximum_new_attempts != sum(o.maximum_attempts for o in self.operations):
            raise ValueError("campaign_attempt_budget_must_equal_frozen_operation_inventory")
        if self.maximum_new_fit_attempts != sum(
            o.maximum_attempts
            for o in self.operations
            if o.action in ("model_fit", "calibrator_fit")
        ):
            raise ValueError("campaign_fit_budget_must_equal_frozen_operation_inventory")
        forecast_budgets = {
            family: sorted(
                (o.initialization_seed, o.maximum_attempts)
                for o in self.operations
                if o.forecast_family == family
            )
            for family in ("rf", "hgb", "tensorflow")
        }
        if not all(forecast_budgets.values()) or any(
            values != forecast_budgets["rf"] for values in forecast_budgets.values()
        ):
            raise ValueError("campaign_forecast_families_require_equal_trial_and_seed_budgets")
        return self

    def content_sha256(self) -> str:
        document = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(document))
        return canonical_sha256(document)


class SelectionBundle(Contract):
    use_case: Literal["forecast", "anomaly", "stockout"]
    model_artifact_sha256: Sha256
    preprocessing_sha256: Sha256
    calibration_sha256: Sha256
    threshold_policy_sha256: Sha256
    feature_schema_sha256: Sha256
    selection_evidence_sha256: Sha256


class SelectionFreeze(Contract):
    bundles: Annotated[tuple[SelectionBundle, ...], Field(min_length=3, max_length=3)]
    development_journal_head_sha256: Sha256
    quality_and_freshness_qualified: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if tuple(b.use_case for b in self.bundles) != USE_CASES:
            raise ValueError("campaign_selection_requires_three_ordered_bundles")
        return self


class CampaignCost(Contract):
    wall_seconds: Annotated[float, Field(ge=0)]
    peak_process_tree_rss_bytes: Annotated[int, Field(ge=1)] | None = None
    artifact_bytes: Annotated[int, Field(ge=1)] | None = None


class CampaignEvent(Contract):
    sequence: Annotated[int, Field(ge=1)]
    previous_sha256: Sha256
    at: UtcTime
    kind: Literal["reserved", "finished", "selection_frozen", "closed"]
    operation_id: Symbol | None = None
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")] | None = (
        None
    )
    result: Literal["completed", "failed"] | None = None
    evidence_sha256: Sha256 | None = None
    cost: CampaignCost | None = None
    error_code: Symbol | None = None
    selection: SelectionFreeze | None = None

    @model_validator(mode="after")
    def payload(self) -> Self:
        if self.kind in ("reserved", "finished"):
            if (
                self.operation_id is None
                or self.reservation_id is None
                or self.selection is not None
            ):
                raise ValueError("campaign_event_operation_binding_required")
            if self.kind == "reserved" and any(
                v is not None
                for v in (self.result, self.evidence_sha256, self.cost, self.error_code)
            ):
                raise ValueError("campaign_reservation_cannot_claim_result")
            if self.kind == "finished":
                if self.result is None:
                    raise ValueError("campaign_finished_result_required")
                if self.result == "completed" and (
                    self.evidence_sha256 is None or self.cost is None or self.error_code is not None
                ):
                    raise ValueError("campaign_completed_evidence_and_cost_required")
                if self.result == "failed" and self.error_code is None:
                    raise ValueError("campaign_failed_error_required")
        elif any(
            v is not None
            for v in (
                self.operation_id,
                self.reservation_id,
                self.result,
                self.cost,
                self.error_code,
            )
        ):
            raise ValueError("campaign_transition_cannot_claim_operation")
        if (self.kind == "selection_frozen") != (self.selection is not None):
            raise ValueError("campaign_selection_payload_mismatch")
        if self.kind == "selection_frozen" and self.evidence_sha256 is not None:
            raise ValueError("campaign_selection_evidence_is_in_bundles")
        if self.kind == "closed" and self.evidence_sha256 is None:
            raise ValueError("campaign_close_report_binding_required")
        return self


class CampaignJournal(Contract):
    scope: Literal["local_ordering_and_budget_audit_not_quality_or_global_exposure_proof"] = (
        "local_ordering_and_budget_audit_not_quality_or_global_exposure_proof"
    )
    protocol: CampaignProtocol
    protocol_sha256: Sha256
    events: Annotated[tuple[CampaignEvent, ...], Field(max_length=1538)] = ()
    head_sha256: Sha256
    quality_qualified: FalseFlag = False
    final_holdout_freshness_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def replay(self) -> Self:
        if self.protocol_sha256 != self.protocol.content_sha256():
            raise ValueError("campaign_protocol_identity_mismatch")
        head = self.protocol_sha256
        plans = {p.operation_id: p for p in self.protocol.operations}
        starts: dict[str, CampaignEvent] = {}
        finished: dict[str, CampaignEvent] = {}
        completed: set[str] = set()
        frozen = closed = False
        previous_at: UtcTime | None = None
        for index, event in enumerate(self.events, 1):
            if event.sequence != index or event.previous_sha256 != head:
                raise ValueError("campaign_event_chain_mismatch")
            if closed or (previous_at is not None and event.at < previous_at):
                raise ValueError("campaign_event_after_close_or_time_reversal")
            previous_at = event.at
            if event.kind == "reserved":
                operation = plans.get(str(event.operation_id))
                if operation is None or (operation.phase == "final") != frozen:
                    raise ValueError("campaign_operation_phase_mismatch")
                if str(event.reservation_id) in starts:
                    raise ValueError("campaign_duplicate_reservation")
                if not set(operation.prerequisites).issubset(completed):
                    raise ValueError("campaign_prerequisites_not_completed")
                if any(
                    e.operation_id == operation.operation_id and key not in finished
                    for key, e in starts.items()
                ):
                    raise ValueError("campaign_operation_has_unresolved_attempt")
                attempts = sum(e.operation_id == operation.operation_id for e in starts.values())
                if attempts >= operation.maximum_attempts:
                    raise ValueError("campaign_operation_budget_exhausted")
                starts[str(event.reservation_id)] = event
            elif event.kind == "finished":
                start = starts.get(str(event.reservation_id))
                if (
                    start is None
                    or event.reservation_id in finished
                    or (start.operation_id != event.operation_id)
                ):
                    raise ValueError("campaign_unknown_duplicate_or_changed_completion")
                operation = plans[str(event.operation_id)]
                if event.result == "completed":
                    if operation.action in ("model_fit", "calibrator_fit") and (
                        event.cost is None
                        or event.cost.peak_process_tree_rss_bytes is None
                        or event.cost.artifact_bytes is None
                    ):
                        raise ValueError("campaign_completed_fit_requires_measured_resource_cost")
                    completed.add(operation.operation_id)
                finished[str(event.reservation_id)] = event
            elif event.kind == "selection_frozen":
                if frozen or set(starts) != set(finished) or event.selection is None:
                    raise ValueError("campaign_selection_requires_resolved_development")
                if event.selection.development_journal_head_sha256 != head:
                    raise ValueError("campaign_selection_development_head_mismatch")
                if {plans[o].forecast_family for o in completed} - {None} != {
                    "rf",
                    "hgb",
                    "tensorflow",
                }:
                    raise ValueError(
                        "campaign_selection_requires_three_completed_forecast_families"
                    )
                frozen = True
            else:
                final_operations = {o.operation_id for o in plans.values() if o.phase == "final"}
                if (
                    not frozen
                    or set(starts) != set(finished)
                    or not final_operations.issubset(completed)
                ):
                    raise ValueError("campaign_close_requires_resolved_final_execution")
                closed = True
            head = canonical_sha256(event.model_dump(mode="json"))
        if head != self.head_sha256:
            raise ValueError("campaign_journal_head_mismatch")
        return self
