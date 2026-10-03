"""Immutable evidence and release pins, independent of mutable MLflow aliases."""

import json
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    LabelID,
    Sha256,
    SourceID,
    SplitID,
    Symbol,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256

MODEL = "retailops-demand-forecast"
TEST_MODEL = "retailops-demand-forecast-mechanics"
ModelName = Literal["retailops-demand-forecast", "retailops-demand-forecast-mechanics"]
Version = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,9}$")]
DecisionID = Annotated[str, Field(pattern=r"^decision-[a-z0-9-]{8,64}$")]
RunID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
GATES = frozenset(
    {
        "source",
        "features",
        "pit",
        "protocol",
        "segments",
        "signature",
        "resources",
        "security_license",
        "model_card",
        "freshness_drift_compatibility",
    }
)
GATE_SCHEMA: dict[str, Any] = {
    "minProperties": len(GATES),
    "maxProperties": len(GATES),
    "propertyNames": {"enum": sorted(GATES)},
}


class Receipt(Contract):
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=1, le=256 * 1024**2)]


class Gate(Contract):
    status: Literal["passed", "failed", "not_ready", "not_evaluable"]
    report: Receipt


class Qualification(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    purpose: Literal["qualified_forecast", "lifecycle_mechanics_only"]
    evidence_id: Symbol
    evaluation_id: Symbol
    reference_id: Symbol
    original_run_kind: Literal["historical_evidence", "training", "mechanics_fixture"]
    original_started_at: UtcTime
    original_completed_at: UtcTime
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    label_dataset_id: LabelID
    split_id: SplitID
    feature_set_id: Annotated[str, Field(pattern=r"^features-sha256-[0-9a-f]{64}$")]
    feature_schema_version: Literal["forecast-features-v1"]
    config_sha256: Sha256
    config: Receipt
    source_code_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    ai_code_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    dependency_lock_sha256: Sha256
    model_seed: Annotated[int, Field(ge=0)]
    data_seed: Annotated[int, Field(ge=0)]
    model_family: Literal["baseline", "random_forest", "hist_gradient_boosting", "mechanics"]
    flavor: Literal["forecast-json-v1", "baseline-json-v1", "mechanics-json-v1"]
    model: Receipt
    signature: Receipt
    input_example: Receipt
    expected_output_sha256: Sha256
    gates: dict[str, Gate] = Field(json_schema_extra=GATE_SCHEMA)

    @model_validator(mode="after")
    def boundary(self) -> Self:
        if set(self.gates) != GATES:
            raise ValueError("qualification_gate_inventory")
        if self.original_completed_at < self.original_started_at:
            raise ValueError("qualification_original_time_order")
        if self.config.sha256 != self.config_sha256:
            raise ValueError("qualification_config_receipt_mismatch")
        fixture = self.purpose == "lifecycle_mechanics_only"
        if (
            fixture != (self.flavor == "mechanics-json-v1")
            or fixture != (self.original_run_kind == "mechanics_fixture")
            or fixture != (self.model_family == "mechanics")
        ):
            raise ValueError("qualification_fixture_boundary")
        if (self.model_family == "baseline") != (self.flavor == "baseline-json-v1"):
            raise ValueError("qualification_baseline_flavor_binding")
        return self


class Binding(Contract):
    model_name: ModelName
    model_version: Version
    mlflow_run_id: RunID
    source_uri: str
    qualification_sha256: Sha256
    qualification: Qualification

    @model_validator(mode="after")
    def namespace(self) -> Self:
        if (self.model_name == TEST_MODEL) != (
            self.qualification.purpose == "lifecycle_mechanics_only"
        ):
            raise ValueError("model_namespace_qualification_mismatch")
        return self


class Request(Contract):
    decision_id: DecisionID
    action: Literal["register", "reject", "promote", "rollback"]
    model_name: ModelName = "retailops-demand-forecast"
    mlflow_run_id: RunID | None = None
    model_version: Version | None = None
    evidence_id: Symbol
    qualification_sha256: Sha256
    reason: Annotated[str, Field(min_length=12, max_length=500)]
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def inputs(self) -> Self:
        if self.reason != self.reason.strip():
            raise ValueError("decision_reason_whitespace")
        if self.action == "register":
            if self.mlflow_run_id is None or self.model_version is not None:
                raise ValueError("registration_requires_run_without_version")
        elif self.model_version is None or self.mlflow_run_id is not None:
            raise ValueError("decision_requires_immutable_version")
        if (self.action == "promote") != (self.image_digest is not None):
            raise ValueError("promotion_requires_image_digest")
        return self


class Release(Contract):
    release_id: str
    decision_id: DecisionID
    binding: Binding
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    previous_release_id: str | None
    previous_version: Version | None
    restored_from_release_id: str | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.previous_release_id is None) != (self.previous_version is None):
            raise ValueError("release_previous_version_binding")
        if self.release_id != "model-release-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"release_id"})
        ):
            raise ValueError("release_identity_mismatch")
        return self


def release_for(**fields: object) -> Release:
    return Release.model_validate_json(
        json.dumps({"release_id": "model-release-sha256-" + canonical_sha256(fields), **fields})
    )
