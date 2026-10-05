"""Stockout job contracts; physical scope and original approved pins are immutable."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, RunID, Sha256, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.data_contracts.run import RunError
from retailops_ai.forecast_jobs.contracts import QueuePolicy
from retailops_ai.stockout_lifecycle.contract import StockoutModelRelease
from retailops_ai.stockout_runtime.contracts import RuntimeLineage, RuntimeReleasePin
from retailops_ai.stockout_runtime.inputs import PhysicalScope

ProfileID = Annotated[str, Field(pattern=r"^stockout-inputs-sha256-[0-9a-f]{64}$")]
OutputID = Annotated[str, Field(pattern=r"^stockout-output-sha256-[0-9a-f]{64}$")]
StockoutErrorCode = Literal[
    "stockout-run-not-found",
    "stockout-run-denied",
    "stockout-risk-not-found",
    "stockout-scope-denied",
    "stockout-input-not-prepared",
    "stockout-input-origin-mismatch",
    "stockout-input-coverage-mismatch",
    "stockout-input-runtime-incompatible",
    "stockout-input-from-future",
    "stockout-model-not-approved",
    "stockout-model-decision-incomplete",
    "stockout-queue-full",
    "stockout-idempotency-conflict",
    "stockout-invalid-idempotency-key",
    "stockout-run-not-active",
    "stockout-database-not-ready",
    "stockout-view-changed",
    "stockout-read-budget",
]


class StockoutRequest(Contract):
    version: Literal["stockout-batch-request-1.0.0"] = "stockout-batch-request-1.0.0"
    profile_id: ProfileID
    as_of: UtcTime
    scope: PhysicalScope
    horizon_days: Literal[7] = 7
    model_alias: Literal["champion"] = "champion"

    def request_hash(self) -> str:
        raw = self.model_dump(mode="json")
        raw["scope"] = {k: sorted(v) for k, v in raw["scope"].items()}
        return canonical_sha256(raw)


class StockoutInputRef(Contract):
    request: StockoutRequest
    request_sha256: Sha256
    lineage: RuntimeLineage

    @model_validator(mode="after")
    def hash(self) -> Self:
        if self.request_sha256 != self.request.request_hash():
            raise ValueError("stockout_job_request_hash")
        return self


class StockoutRun(Contract):
    version: Literal["stockout-batch-run-1.0.0"] = "stockout-batch-run-1.0.0"
    run_type: Literal["stockout_batch"] = "stockout_batch"
    run_id: RunID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempt: Annotated[int, Field(ge=1, le=5)]
    requested_at: UtcTime
    requested_by: Symbol
    started_at: UtcTime | None = None
    completed_at: UtcTime | None = None
    input_ref: StockoutInputRef
    release: StockoutModelRelease
    environment: Literal["local", "test"]
    policy: QueuePolicy
    output_id: OutputID | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def coherence(self) -> Self:
        if (
            self.release.binding.model_name.endswith("test-mechanics")
            and self.environment != "test"
            or self.input_ref.request.as_of > self.requested_at
            or self.input_ref.request.as_of
            < self.release.binding.approval.qualification.recipe.pin.selection_known_at
            or self.attempt > self.policy.max_attempts
            or not self.release.binding.approval.reviewed_at
            <= self.requested_at
            < self.release.binding.approval.qualification.valid_until
            or self.started_at is not None
            and self.started_at < self.requested_at
            or self.completed_at is not None
            and self.completed_at < (self.started_at or self.requested_at)
        ):
            raise ValueError("stockout_job_model_origin_or_time")
        if self.status == "queued":
            valid = all(
                v is None for v in (self.started_at, self.completed_at, self.output_id, self.error)
            )
        elif self.status == "running":
            valid = self.started_at is not None and all(
                v is None for v in (self.completed_at, self.output_id, self.error)
            )
        elif self.status == "succeeded":
            valid = (
                self.started_at is not None
                and self.completed_at is not None
                and self.output_id is not None
                and self.error is None
            )
        else:
            valid = (
                self.completed_at is not None
                and self.output_id is None
                and self.error is not None
                and (self.status != "failed" or self.started_at is not None)
            )
            valid = valid and (self.status == "cancelled") == (
                self.error is not None and self.error.code == "cancelled"
            )
        if not valid:
            raise ValueError("stockout_job_state")
        return self


class StockoutJobModel(Contract):
    release: RuntimeReleasePin
    model_id: Annotated[str, Field(pattern=r"^risk-model-sha256-[0-9a-f]{64}$")]
    calibrator_sha256: Sha256
    policy_id: Annotated[str, Field(pattern=r"^stockout-scoring-policy-sha256-[0-9a-f]{64}$")]
    approval_sha256: Sha256
    approval_valid_until: UtcTime


class StockoutJobRun(Contract):
    version: Literal["stockout-job-run-1.0.0"] = "stockout-job-run-1.0.0"
    run_type: Literal["stockout_batch"] = "stockout_batch"
    run_id: RunID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempt: Annotated[int, Field(ge=1, le=5)]
    requested_at: UtcTime
    requested_by: Symbol
    started_at: UtcTime | None
    completed_at: UtcTime | None
    input_ref: StockoutInputRef
    resolved_model: StockoutJobModel
    output_id: OutputID | None
    publication_status: Literal["not_published", "published"]
    error: RunError | None

    @model_validator(mode="after")
    def publication(self) -> Self:
        if (
            self.input_ref.request.as_of > self.requested_at
            or (self.started_at is not None and self.started_at < self.requested_at)
            or (
                self.completed_at is not None
                and self.completed_at < (self.started_at or self.requested_at)
            )
        ):
            raise ValueError("stockout_public_job_time")
        if self.status == "queued":
            valid = self.started_at is None and self.completed_at is None and self.error is None
        elif self.status == "running":
            valid = self.started_at is not None and self.completed_at is None and self.error is None
        elif self.status == "succeeded":
            valid = (
                self.started_at is not None and self.completed_at is not None and self.error is None
            )
        else:
            valid = (
                self.completed_at is not None
                and self.error is not None
                and (self.status != "failed" or self.started_at is not None)
            )
            valid = valid and (self.status == "cancelled") == (
                self.error is not None and self.error.code == "cancelled"
            )
        if not valid:
            raise ValueError("stockout_public_job_state")
        if (self.output_id is not None) != (self.status == "succeeded") or (
            self.publication_status == "published"
        ) != (self.output_id is not None):
            raise ValueError("stockout_public_job_output_state")
        return self


def public_run(run: StockoutRun) -> StockoutJobRun:
    run = StockoutRun.model_validate_json(run.model_dump_json())
    approval = run.release.binding.approval
    q = approval.qualification
    raw = run.model_dump(mode="json", exclude={"version", "release", "environment", "policy"})
    return StockoutJobRun.model_validate_json(
        canonical_bytes(
            {
                **raw,
                "resolved_model": StockoutJobModel(
                    release=run.release.runtime_pin(),
                    model_id=q.recipe.pin.model_id,
                    calibrator_sha256=q.recipe.pin.calibrator_sha256,
                    policy_id=q.policy.policy_id,
                    approval_sha256=run.release.binding.approval_sha256,
                    approval_valid_until=q.valid_until,
                ).model_dump(mode="json"),
                "publication_status": "published" if run.output_id else "not_published",
            }
        )
    )
