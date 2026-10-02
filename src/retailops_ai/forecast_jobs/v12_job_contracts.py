"""Safe public job metadata; computation and published predictions are distinct resources."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, RunID, Sha256, Symbol, TrueFlag, UtcTime
from retailops_ai.data_contracts.run import RunError
from retailops_ai.forecast_jobs.contracts import BatchInput, QueuePolicy
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.forecast_jobs.v12_batch import ReceiptID
from retailops_ai.forecast_jobs.v12_publication import OutputID
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import DatabaseReleaseID, ModelName
from retailops_ai.model_lifecycle.v12_release_contracts import ImageDigest


class V12JobModel(Contract):
    model_name: ModelName
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    approval_sha256: Sha256
    runtime_pin_sha256: Sha256
    approval_valid_until: UtcTime


class V12JobOutput(Contract):
    kind: Literal["predictions"] = "predictions"
    artifact_id: OutputID
    complete: TrueFlag = True


class V12JobRun(Contract):
    version: Literal["forecast-v12-job-run-1.0.0"] = "forecast-v12-job-run-1.0.0"
    run_type: Literal["forecast_batch"] = "forecast_batch"
    run_id: RunID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempt: Annotated[int, Field(ge=1, le=5)]
    requested_at: UtcTime
    requested_by: Symbol
    started_at: UtcTime | None
    completed_at: UtcTime | None
    input_ref: BatchInput
    resolved_model: V12JobModel
    release_id: DatabaseReleaseID
    image_digest: ImageDigest
    policy: QueuePolicy
    computation_receipt_id: ReceiptID | None
    output_ref: V12JobOutput | None
    publication_status: Literal["not_computed", "awaiting_publication", "published"]
    error: RunError | None

    @model_validator(mode="after")
    def state(self) -> Self:
        if self.input_ref.as_of_time > self.requested_at or self.attempt > self.policy.max_attempts:
            raise ValueError("v12_job_input_or_attempt")
        if self.started_at is not None and self.started_at < self.requested_at:
            raise ValueError("v12_job_start")
        if self.completed_at is not None and self.completed_at < (
            self.started_at or self.requested_at
        ):
            raise ValueError("v12_job_completion")
        if self.status == "queued":
            valid = self.started_at is None and self.completed_at is None and self.error is None
        elif self.status == "running":
            valid = self.started_at is not None and self.completed_at is None and self.error is None
        elif self.status == "succeeded":
            valid = (
                self.started_at is not None
                and self.completed_at is not None
                and self.error is None
                and self.computation_receipt_id is not None
            )
        else:
            valid = (
                self.completed_at is not None
                and self.error is not None
                and (self.status == "cancelled" or self.started_at is not None)
                and (self.status == "cancelled") == (self.error.code == "cancelled")
            )
        if self.status != "succeeded":
            valid = valid and self.computation_receipt_id is None and self.output_ref is None
        expected = (
            "published"
            if self.output_ref is not None
            else "awaiting_publication"
            if self.status == "succeeded"
            else "not_computed"
        )
        if not valid or self.publication_status != expected:
            raise ValueError("v12_job_state_or_publication")
        return self


class V12JobAttempts(Contract):
    version: Literal["forecast-v12-job-attempts-1.0.0"] = "forecast-v12-job-attempts-1.0.0"
    run_id: RunID
    items: tuple[V12JobRun, ...] = Field(max_length=5)
    pagination: Pagination
    generated_at: UtcTime

    @model_validator(mode="after")
    def history(self) -> Self:
        if (
            any(r.run_id != self.run_id or r.status in {"queued", "running"} for r in self.items)
            or [r.attempt for r in self.items] != sorted({r.attempt for r in self.items})
            or self.pagination.limit != 5
            or self.pagination.offset != 0
            or self.pagination.total != len(self.items)
            or self.pagination.next_offset is not None
        ):
            raise ValueError("v12_job_attempt_history")
        return self
