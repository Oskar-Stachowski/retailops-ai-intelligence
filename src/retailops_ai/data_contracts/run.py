"""Recorded run state and legal transitions, not a job queue or retry engine."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DataLineage,
    FeatureID,
    LabelID,
    ModelID,
    PredictionDatasetID,
    RunID,
    SplitID,
    Symbol,
    TrueFlag,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.model import ModelRecord


class RunInput(DataLineage):
    feature_set_id: FeatureID
    label_dataset_id: LabelID | None
    split_id: SplitID | None
    as_of_time: UtcTime


class RunError(Contract):
    code: Literal["invalid_input", "dependency_unavailable", "execution_failed", "cancelled"]
    retryable: bool


class RunOutput(Contract):
    kind: Literal["model", "predictions"]
    artifact_id: ModelID | PredictionDatasetID
    complete: TrueFlag

    @model_validator(mode="after")
    def output_kind(self) -> Self:
        if not self.artifact_id.startswith(self.kind + "-sha256-"):
            raise ValueError("run_output_kind_mismatch")
        return self


class RunRecord(Versioned):
    contract_type: Literal["run"]
    run_id: RunID
    run_type: Literal["training", "forecast_batch"]
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempt: Annotated[int, Field(ge=1)]
    requested_at: UtcTime
    started_at: UtcTime | None
    completed_at: UtcTime | None
    requested_by: Symbol
    input_ref: RunInput
    resolved_model: ModelRecord | None
    output_ref: RunOutput | None
    error: RunError | None

    @model_validator(mode="after")
    def state_coherence(self) -> Self:
        if self.input_ref.as_of_time > self.requested_at:
            raise ValueError("run_input_from_future")
        if self.run_type == "training":
            if (
                self.resolved_model is not None
                or self.input_ref.label_dataset_id is None
                or self.input_ref.split_id is None
            ):
                raise ValueError("training_run_input_mismatch")
        elif (
            self.resolved_model is None
            or self.input_ref.label_dataset_id is not None
            or self.input_ref.split_id is not None
        ):
            raise ValueError("forecast_run_requires_pinned_model_without_labels")
        if (
            self.resolved_model is not None
            and self.resolved_model.selection_cutoff > self.input_ref.as_of_time
        ):
            raise ValueError("model_selected_after_inference_origin")
        if self.started_at is not None and self.started_at < self.requested_at:
            raise ValueError("run_started_before_request")
        if self.completed_at is not None and self.completed_at < (
            self.started_at or self.requested_at
        ):
            raise ValueError("run_completed_before_start")
        if self.status == "queued":
            if any(
                v is not None
                for v in (self.started_at, self.completed_at, self.output_ref, self.error)
            ):
                raise ValueError("queued_run_has_execution")
        elif self.status == "running":
            if self.started_at is None or any(
                v is not None for v in (self.completed_at, self.output_ref, self.error)
            ):
                raise ValueError("running_run_state_mismatch")
        elif self.status == "succeeded":
            if (
                self.started_at is None
                or self.completed_at is None
                or self.output_ref is None
                or self.error is not None
            ):
                raise ValueError("succeeded_run_requires_complete_output")
            expected = "model" if self.run_type == "training" else "predictions"
            if self.output_ref.kind != expected:
                raise ValueError("succeeded_run_output_mismatch")
        elif self.completed_at is None or self.output_ref is not None or self.error is None:
            raise ValueError("failed_cancelled_run_requires_safe_error_without_output")
        if self.status == "failed" and self.started_at is None:
            raise ValueError("failed_run_requires_start")
        if self.error is not None:
            if (self.status == "cancelled") != (self.error.code == "cancelled"):
                raise ValueError("run_error_status_mismatch")
        return self


def transition_run(current: RunRecord, following: RunRecord) -> RunRecord:
    if current == following:
        return following
    pinned = (
        "run_id",
        "run_type",
        "attempt",
        "requested_at",
        "requested_by",
        "input_ref",
        "resolved_model",
    )
    if any(getattr(current, name) != getattr(following, name) for name in pinned):
        raise ValueError("run_transition_changed_pinned_input")
    allowed = {"queued": {"running", "cancelled"}, "running": {"succeeded", "failed", "cancelled"}}
    if following.status not in allowed.get(current.status, set()):
        raise ValueError("illegal_run_transition")
    if current.started_at is not None and following.started_at != current.started_at:
        raise ValueError("run_transition_changed_start")
    return following
