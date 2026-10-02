"""Private bounded computations produce no persisted forecast artifact or successful queue run."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Units
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import ProfileID, ReleaseID
from retailops_ai.forecast_jobs.inputs import MAX_INPUT_BYTES, PreparedInputs
from retailops_ai.forecast_jobs.runtime import RuntimePin
from retailops_ai.model_lifecycle.contracts import MODEL, Release

MAX_EXECUTION_BYTES = MAX_INPUT_BYTES + 2 * 1024**2
MAX_RESULT_BYTES = 256 * 1024
MAX_STDERR_BYTES = 16 * 1024


class ExecutionLimits(Contract):
    wall_seconds: Annotated[float, Field(ge=0.1, le=120)] = 120.0
    cpu_seconds: Annotated[int, Field(ge=1, le=60)] = 60
    rss_bytes: Annotated[int, Field(ge=128 * 1024**2, le=1024 * 1024**2)] = 1024 * 1024**2


class RuntimeExecution(Contract):
    purpose: Literal["runtime_preflight_only", "qualified_forecast_computation"] = (
        "runtime_preflight_only"
    )
    environment: Literal["local", "test"]
    compose: bool
    inputs: PreparedInputs
    release: Release
    runtime_pin: RuntimePin
    limits: ExecutionLimits = ExecutionLimits()

    @model_validator(mode="after")
    def pins(self) -> Self:
        binding = self.release.binding
        q = binding.qualification
        if (
            binding.model_name != MODEL
            or q.purpose != "qualified_forecast"
            or any(g.status != "passed" for g in q.gates.values())
            or self.release.image_digest != self.runtime_pin.image_digest
            or q.dependency_lock_sha256 != self.runtime_pin.dependency_lock_sha256
            or self.inputs.feature_manifest.descriptor.code.dependency_lock_sha256
            != self.runtime_pin.dependency_lock_sha256
        ):
            raise ValueError("runtime_execution_requires_qualified_compatible_pins")
        return self


class RuntimeResult(Contract):
    purpose: Literal["runtime_preflight_only", "qualified_forecast_computation"] = (
        "runtime_preflight_only"
    )
    profile_id: ProfileID
    release_id: ReleaseID
    quantities: tuple[Units, ...] = Field(min_length=1, max_length=1400)
    quantities_sha256: Sha256
    cold_load_seconds: Annotated[float, Field(ge=0)]
    compute_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.quantities_sha256 != canonical_sha256(self.quantities):
            raise ValueError("runtime_result_checksum_mismatch")
        return self
