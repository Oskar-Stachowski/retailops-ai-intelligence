"""Anomaly-specific evidence and release pins; forecast namespaces stay independent."""

import json
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.contract import Family
from retailops_ai.data_contracts.common import Contract, Sha256, SourceID, Symbol, UtcTime
from retailops_ai.model_lifecycle.contracts import (
    GATE_SCHEMA,
    GATES,
    DecisionID,
    Gate,
    Receipt,
    RunID,
    Version,
)
from retailops_ai.source_snapshot.files import json_sha256

MODEL: Literal["retailops-sales-anomaly"] = "retailops-sales-anomaly"


class Qualification(Contract):
    version: Literal["anomaly-qualification-1.0.0"] = "anomaly-qualification-1.0.0"
    purpose: Literal["qualified_bounded_anomaly_portfolio"] = "qualified_bounded_anomaly_portfolio"
    evidence_id: Symbol
    evaluation_id: Symbol
    reference_id: Symbol
    source_dataset_id: SourceID
    qualified_anomaly_input_id: Annotated[
        str, Field(pattern=r"^qualified-anomaly-inputs-sha256-[0-9a-f]{64}$")
    ]
    feature_schema_version: Literal["qualified-anomaly-inputs-1.0.0"] = (
        "qualified-anomaly-inputs-1.0.0"
    )
    original_started_at: UtcTime
    original_completed_at: UtcTime
    source_code_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    ai_code_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    dependency_lock_sha256: Sha256
    model_family: Family
    model_seed: Annotated[int, Field(ge=0)]
    data_seed: Literal[42] = 42
    model: Receipt
    config: Receipt
    signature: Receipt
    input_example: Receipt
    expected_output_sha256: Sha256
    gates: dict[str, Gate] = Field(json_schema_extra=GATE_SCHEMA)
    truth_access: Literal["excluded_from_model_and_batch"] = "excluded_from_model_and_batch"
    qualification_scope: Literal[
        "synthetic_ai_07_portfolio_v1",
        "synthetic_ai_07_portfolio_v2",
        "synthetic_ai_07_portfolio_v3",
        "synthetic_ai_07_portfolio_v4",
    ] = "synthetic_ai_07_portfolio_v1"

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if set(self.gates) != GATES or self.original_completed_at < self.original_started_at:
            raise ValueError("anomaly_qualification_gates_or_times")
        return self


class Binding(Contract):
    model_name: Literal["retailops-sales-anomaly"] = MODEL
    model_version: Version
    mlflow_run_id: RunID
    source_uri: str
    qualification_sha256: Sha256
    qualification: Qualification


class Request(Contract):
    decision_id: DecisionID
    action: Literal["register", "reject", "promote", "rollback"]
    model_name: Literal["retailops-sales-anomaly"] = MODEL
    mlflow_run_id: RunID | None = None
    model_version: Version | None = None
    evidence_id: Symbol
    qualification_sha256: Sha256
    reason: Annotated[str, Field(min_length=12, max_length=500)]
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def inputs(self) -> Self:
        if self.reason != self.reason.strip():
            raise ValueError("anomaly_decision_reason_whitespace")
        if self.action == "register":
            if self.mlflow_run_id is None or self.model_version is not None:
                raise ValueError("anomaly_registration_requires_run")
        elif self.model_version is None or self.mlflow_run_id is not None:
            raise ValueError("anomaly_decision_requires_version")
        if (self.action == "promote") != (self.image_digest is not None):
            raise ValueError("anomaly_promotion_requires_image")
        return self


class Release(Contract):
    release_id: Annotated[str, Field(pattern=r"^anomaly-release-sha256-[0-9a-f]{64}$")]
    decision_id: DecisionID
    binding: Binding
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    previous_release_id: str | None
    previous_version: Version | None
    restored_from_release_id: str | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (self.previous_release_id is None) != (self.previous_version is None):
            raise ValueError("anomaly_release_previous_pin")
        if self.release_id != "anomaly-release-sha256-" + json_sha256(
            self.model_dump(mode="json", exclude={"release_id"})
        ):
            raise ValueError("anomaly_release_identity")
        return self


def release_for(**fields: object) -> Release:
    return Release.model_validate_json(
        json.dumps({"release_id": "anomaly-release-sha256-" + json_sha256(fields), **fields})
    )
