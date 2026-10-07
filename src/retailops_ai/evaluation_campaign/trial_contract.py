"""A bounded development registry; historical observations are never preregistration."""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.development_contract import (
    ComparisonFile,
    DevelopmentProtocol,
)

AttemptID = Annotated[str, Field(pattern=r"^attempt-[0-9a-f]{32}$")]


def absolute_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError("trial_absolute_canonical_path_required")
    return value


OutputPath = Annotated[str, Field(min_length=1, max_length=1024), AfterValidator(absolute_path)]


class AttemptSnapshot(Contract):
    output: OutputPath
    protocol_sha256: Sha256
    status: Literal["completed_development_diagnostic", "failed", "interrupted", "unresolved"]
    files: dict[str, ComparisonFile]
    model_starts: Annotated[int, Field(ge=0, le=4)]
    model_completions: Annotated[int, Field(ge=0, le=4)]
    cost_files: tuple[str, ...]
    cost_scope: Literal["checksummed_original_receipts_missing_cost_is_unknown"] = (
        "checksummed_original_receipts_missing_cost_is_unknown"
    )
    qualification: Literal["inventory_only_no_model_replay_or_quality_acceptance"] = (
        "inventory_only_no_model_replay_or_quality_acceptance"
    )
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if (
            not self.files
            or len(self.files) > 10000
            or self.model_completions > self.model_starts
            or any(name not in self.files for name in self.cost_files)
            or self.cost_files != tuple(sorted(set(self.cost_files)))
        ):
            raise ValueError("trial_snapshot_inventory")
        return self


class TrialPlan(Contract):
    version: Literal["ai09-development-trial-plan-1.0.0"] = "ai09-development-trial-plan-1.0.0"
    registry_path: OutputPath
    protocols: Annotated[tuple[DevelopmentProtocol, ...], Field(min_length=1, max_length=12)]
    maximum_new_attempts: Annotated[int, Field(ge=1, le=24)]
    maximum_attempts_per_protocol: Annotated[int, Field(ge=1, le=2)] = 1
    fit_slots_per_attempt: Literal[4] = 4
    failure_budget: Literal["failed_and_unresolved_reservations_consume_all_four_fit_slots"] = (
        "failed_and_unresolved_reservations_consume_all_four_fit_slots"
    )
    selection: Literal["none_development_diagnostic_only"] = "none_development_diagnostic_only"
    historical_attempts: Annotated[tuple[AttemptSnapshot, ...], Field(max_length=64)] = ()
    historical_scope: Literal[
        "retrospective_inventory_not_a_pre_fit_budget_or_complete_machine_log"
    ] = "retrospective_inventory_not_a_pre_fit_budget_or_complete_machine_log"
    audit_code_sha256: Sha256
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        hashes = [canonical_sha256(p.model_dump(mode="json")) for p in self.protocols]
        paths = [row.output for row in self.historical_attempts]
        if (
            len(set(hashes)) != len(hashes)
            or len(set(paths)) != len(paths)
            or self.maximum_new_attempts > len(hashes) * self.maximum_attempts_per_protocol
        ):
            raise ValueError("trial_plan_duplicate_or_unreachable_budget")
        return self


class TrialEvent(Contract):
    sequence: Annotated[int, Field(ge=1, le=48)]
    previous_sha256: Sha256
    at: UtcTime
    kind: Literal["reserved", "finished"]
    attempt_id: AttemptID
    protocol_sha256: Sha256
    output: OutputPath
    outcome: Literal["completed_development_diagnostic", "failed"] | None = None
    error_code: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,128}$")] | None = None
    wall_seconds: Annotated[float, Field(ge=0)] | None = None
    snapshot: AttemptSnapshot | None = None

    @model_validator(mode="after")
    def transition(self) -> Self:
        if self.kind == "reserved":
            if any(
                v is not None
                for v in (self.outcome, self.error_code, self.wall_seconds, self.snapshot)
            ):
                raise ValueError("trial_reservation_has_result")
        elif (
            self.outcome is None
            or self.wall_seconds is None
            or (self.outcome == "failed") != (self.error_code is not None)
            or self.outcome == "completed_development_diagnostic"
            and self.snapshot is None
        ):
            raise ValueError("trial_finish_missing_result")
        if self.snapshot is not None and (
            self.snapshot.output != self.output
            or self.snapshot.protocol_sha256 != self.protocol_sha256
            or self.snapshot.status != self.outcome
        ):
            raise ValueError("trial_result_snapshot_mismatch")
        return self


class TrialLedger(Contract):
    version: Literal["ai09-development-trial-ledger-1.0.0"] = "ai09-development-trial-ledger-1.0.0"
    plan: TrialPlan
    events: Annotated[tuple[TrialEvent, ...], Field(max_length=48)] = ()
    head_sha256: Sha256

    @model_validator(mode="after")
    def chain_and_budget(self) -> Self:
        head = canonical_sha256(self.plan.model_dump(mode="json"))
        recipes = {canonical_sha256(p.model_dump(mode="json")) for p in self.plan.protocols}
        reserved: dict[str, TrialEvent] = {}
        finished: set[str] = set()
        paths = {h.output for h in self.plan.historical_attempts}
        counts = dict.fromkeys(recipes, 0)
        for sequence, event in enumerate(self.events, 1):
            if event.sequence != sequence or event.previous_sha256 != head:
                raise ValueError("trial_ledger_chain_mismatch")
            if event.protocol_sha256 not in recipes:
                raise ValueError("trial_ledger_unplanned_protocol")
            if event.kind == "reserved":
                if event.attempt_id in reserved or event.output in paths:
                    raise ValueError("trial_ledger_duplicate_attempt_or_output")
                reserved[event.attempt_id] = event
                paths.add(event.output)
                counts[event.protocol_sha256] += 1
            else:
                start = reserved.get(event.attempt_id)
                if (
                    start is None
                    or event.attempt_id in finished
                    or start.protocol_sha256 != event.protocol_sha256
                    or start.output != event.output
                ):
                    raise ValueError("trial_ledger_invalid_finish")
                finished.add(event.attempt_id)
            head = canonical_sha256(event.model_dump(mode="json"))
        if (
            head != self.head_sha256
            or len(reserved) > self.plan.maximum_new_attempts
            or any(n > self.plan.maximum_attempts_per_protocol for n in counts.values())
        ):
            raise ValueError("trial_ledger_head_or_budget_mismatch")
        return self
