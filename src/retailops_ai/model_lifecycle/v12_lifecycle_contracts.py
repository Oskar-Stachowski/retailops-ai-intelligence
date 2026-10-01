"""V12 registry bindings and database releases are distinct from offline/local approvals."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import GATES, DecisionID, Receipt, RunID, Version
from retailops_ai.model_lifecycle.v12_release_contracts import (
    ImageDigest,
    ReleaseID,
    V12InferenceRelease,
)
from retailops_ai.source_snapshot.files import relative_path

MODEL = "retailops-demand-forecast-v12"
TEST_MODEL = "retailops-demand-forecast-v12-mechanics"
ModelName = Literal["retailops-demand-forecast-v12", "retailops-demand-forecast-v12-mechanics"]
DatabaseReleaseID = Annotated[str, Field(pattern=r"^v12-model-release-sha256-[0-9a-f]{64}$")]


def capsule_names() -> set[str]:
    return {"qualification.json", "smoke.json", "inputs.json", "release.json"} | {
        f"reports/{gate}.json" for gate in GATES
    }


class V12RegistrySource(Contract):
    model_name: ModelName
    mlflow_run_id: RunID
    campaign_mlflow_run_id: RunID
    source_uri: str
    approval_sha256: Sha256
    approval: V12InferenceRelease
    files: dict[str, Receipt]

    @field_validator("source_uri")
    @classmethod
    def trusted_source(cls, value: str) -> str:
        if not value.startswith("mlflow-artifacts:/"):
            raise ValueError("v12_registry_untrusted_source")
        relative_path(value.removeprefix("mlflow-artifacts:/").lstrip("/"))
        if not value.endswith("/v12-release"):
            raise ValueError("v12_registry_source_boundary")
        return value

    @model_validator(mode="after")
    def artifact_inventory(self) -> Self:
        if (
            set(self.files) != capsule_names()
            or self.files["release.json"].sha256 != self.approval_sha256
            or any(ref.size_bytes > 4 * 1024**2 for ref in self.files.values())
        ):
            raise ValueError("v12_registry_capsule_inventory")
        return self


class V12Binding(V12RegistrySource):
    model_version: Version


class V12LifecycleRequest(Contract):
    decision_id: DecisionID
    action: Literal["register", "reject", "promote", "rollback"]
    model_name: ModelName = "retailops-demand-forecast-v12"
    mlflow_run_id: RunID | None = None
    model_version: Version | None = None
    approval_id: ReleaseID
    approval_sha256: Sha256
    image_digest: ImageDigest | None = None
    reason: Annotated[str, Field(min_length=12, max_length=500)]

    @model_validator(mode="after")
    def decision_inputs(self) -> Self:
        registering = self.action == "register"
        if (
            registering != (self.mlflow_run_id is not None)
            or registering == (self.model_version is not None)
            or (self.action == "promote") != (self.image_digest is not None)
            or self.reason != self.reason.strip()
        ):
            raise ValueError("v12_lifecycle_request_boundary")
        return self


class V12ModelRelease(Contract):
    version: Literal["forecast-v12-database-release-1.0.0"] = "forecast-v12-database-release-1.0.0"
    release_id: DatabaseReleaseID
    decision_id: DecisionID
    binding: V12Binding
    image_digest: ImageDigest
    previous_release_id: DatabaseReleaseID | None
    previous_version: Version | None
    restored_from_release_id: DatabaseReleaseID | None

    @model_validator(mode="after")
    def release_identity(self) -> Self:
        if (
            (self.previous_release_id is None) != (self.previous_version is None)
            or self.image_digest != self.binding.approval.approval.image_digest
            or self.release_id
            != "v12-model-release-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"release_id"}))
        ):
            raise ValueError("v12_database_release_identity")
        return self


def database_release(**content: object) -> V12ModelRelease:
    from retailops_ai.data_contracts.identity import canonical_bytes

    raw = {"version": "forecast-v12-database-release-1.0.0", **content}
    raw["release_id"] = "v12-model-release-sha256-" + canonical_sha256(raw)
    return V12ModelRelease.model_validate_json(canonical_bytes(raw))
