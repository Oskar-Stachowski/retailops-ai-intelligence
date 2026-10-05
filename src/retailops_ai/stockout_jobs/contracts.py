"""Stockout job contracts; physical scope and original approved pins are immutable."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, RunID, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.data_contracts.run import RunError
from retailops_ai.forecast_jobs.contracts import QueuePolicy
from retailops_ai.stockout_jobs.public_contracts import (
    OutputID as OutputID,
)
from retailops_ai.stockout_jobs.public_contracts import (
    ProfileID as ProfileID,
)
from retailops_ai.stockout_jobs.public_contracts import (
    StockoutErrorCode as StockoutErrorCode,
)
from retailops_ai.stockout_jobs.public_contracts import (
    StockoutInputRef as StockoutInputRef,
)
from retailops_ai.stockout_jobs.public_contracts import (
    StockoutJobModel as StockoutJobModel,
)
from retailops_ai.stockout_jobs.public_contracts import (
    StockoutJobRun as StockoutJobRun,
)
from retailops_ai.stockout_jobs.public_contracts import (
    StockoutRequest as StockoutRequest,
)
from retailops_ai.stockout_lifecycle.contract import StockoutModelRelease


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
