"""Serving-run extension of the shared envelope, with immutable scope/data/release pins."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    ForecastKey,
    PredictionDatasetID,
    RunID,
    Sha256,
    Symbol,
    TrueFlag,
    Units,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.run import RunInput, RunOutput, RunRecord
from retailops_ai.model_lifecycle.contracts import TEST_MODEL, Binding

BatchErrorCode = Literal[
    "database-not-ready",
    "scope-denied",
    "batch-scope-limit",
    "forecast-run-not-found",
    "forecast-run-denied",
    "invalid-idempotency-key",
    "idempotency-conflict",
    "input-from-future",
    "model-not-approved",
    "model-decision-incomplete",
    "input-not-prepared",
    "input-origin-mismatch",
    "queue-full",
    "input-coverage-mismatch",
    "input-runtime-incompatible",
    "run-not-active",
]

ProfileID = Annotated[str, Field(pattern=r"^batch-profile-sha256-[0-9a-f]{64}$")]
ReleaseID = Annotated[str, Field(pattern=r"^model-release-sha256-[0-9a-f]{64}$")]


class BatchRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    profile_id: ProfileID
    as_of: UtcTime
    horizons_days: tuple[Literal[7, 14], ...] = (7, 14)
    product_ids: tuple[Symbol, ...] = Field(default=(), max_length=20)
    selling_location_ids: tuple[Symbol, ...] = Field(default=(), max_length=5)
    channel: Literal["store", "online"]
    model_alias: Literal["champion"] = "champion"

    @field_validator("horizons_days", "product_ids", "selling_location_ids", mode="before")
    @classmethod
    def json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def bounded(self) -> Self:
        for values in (self.horizons_days, self.product_ids, self.selling_location_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate_batch_filter")
        if not self.horizons_days or len(self.horizons_days) > 2:
            raise ValueError("batch_horizon_limit")
        if self.as_of.time().isoformat() != "23:59:59":
            raise ValueError("batch_origin_must_close_utc_day")
        return self

    def request_hash(self) -> str:
        raw = self.model_dump(mode="json")
        for key in ("horizons_days", "product_ids", "selling_location_ids"):
            raw[key] = sorted(raw[key])
        return canonical_sha256(raw)


class BatchScope(Contract):
    product_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=20)
    selling_location_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=5)
    channel: Literal["store", "online"]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if any(v != tuple(sorted(set(v))) for v in (self.product_ids, self.selling_location_ids)):
            raise ValueError("batch_scope_requires_sorted_unique_ids")
        return self


class QueuePolicy(Contract):
    schema_version: Literal["1.0"] = "1.0"
    max_attempts: Annotated[int, Field(ge=1, le=5)] = 3
    lease_seconds: Annotated[int, Field(ge=2, le=60)] = 30
    heartbeat_seconds: Annotated[float, Field(ge=0.1, le=10)] = 5.0
    attempt_timeout_seconds: Annotated[int, Field(ge=2, le=300)] = 120
    run_timeout_seconds: Annotated[int, Field(ge=2, le=900)] = 600
    retry_backoff_seconds: Annotated[int, Field(ge=0, le=30)] = 5
    max_pending: Annotated[int, Field(ge=1, le=100)] = 100
    max_pending_per_principal: Annotated[int, Field(ge=1, le=20)] = 20

    @model_validator(mode="after")
    def budgets(self) -> Self:
        if (
            self.heartbeat_seconds >= self.lease_seconds / 2
            or self.lease_seconds > self.attempt_timeout_seconds
            or self.attempt_timeout_seconds > self.run_timeout_seconds
            or self.max_pending_per_principal > self.max_pending
        ):
            raise ValueError("batch_policy_budget_mismatch")
        return self


class MechanicsInput(ForecastKey):
    value: Units


class MechanicsProfile(RunInput):
    """Private test fixture registration, never a substitute for a qualified feature package."""

    profile_id: ProfileID
    environment: Literal["test"] = "test"
    purpose: Literal["lifecycle_mechanics_only"] = "lifecycle_mechanics_only"
    label_dataset_id: None = None
    split_id: None = None
    rows: tuple[MechanicsInput, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.profile_id != "batch-profile-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_id"})
        ):
            raise ValueError("batch_profile_identity_mismatch")
        keys = [canonical_sha256(r.model_dump(mode="json", exclude={"value"})) for r in self.rows]
        if len(keys) != len(set(keys)) or any(
            r.forecast_origin != self.as_of_time for r in self.rows
        ):
            raise ValueError("batch_profile_grain_or_origin_mismatch")
        return self


class BatchInput(RunInput):
    label_dataset_id: None = None
    split_id: None = None
    profile_id: ProfileID
    request_hash: Sha256
    request: BatchRequest
    scope: BatchScope

    @model_validator(mode="after")
    def request_binding(self) -> Self:
        if (
            self.request_hash != self.request.request_hash()
            or self.request.profile_id != self.profile_id
            or self.request.as_of != self.as_of_time
            or self.request.channel != self.scope.channel
        ):
            raise ValueError("batch_request_input_binding_mismatch")
        return self


class BatchRun(RunRecord):
    """Keep MLRunRecord v1 closed; serving uses a namespaced extension of the same state machine."""

    run_type: Literal["forecast_batch"] = "forecast_batch"
    input_ref: BatchInput
    resolved_model: Binding
    output_ref: RunOutput | None
    release_id: ReleaseID
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    environment: Literal["local", "test"]
    purpose: Literal["qualified_forecast", "lifecycle_mechanics_only"]
    policy: QueuePolicy

    @model_validator(mode="after")
    def release_boundary(self) -> Self:
        if self.purpose != self.resolved_model.qualification.purpose or (
            (self.resolved_model.model_name == TEST_MODEL) and self.environment != "test"
        ):
            raise ValueError("batch_run_namespace_mismatch")
        return self


class MechanicsPrediction(ForecastKey):
    predicted_units: Units


class MechanicsOutput(Contract):
    artifact_id: PredictionDatasetID
    kind: Literal["predictions"] = "predictions"
    complete: TrueFlag = True
    purpose: Literal["lifecycle_mechanics_only"] = "lifecycle_mechanics_only"
    forecast_quality_approved: FalseFlag = False
    run_id: RunID
    release_id: ReleaseID
    profile_id: ProfileID
    predictions: tuple[MechanicsPrediction, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.artifact_id != "predictions-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"artifact_id"})
        ):
            raise ValueError("batch_output_identity_mismatch")
        return self
