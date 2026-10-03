"""Separate v12 inference acceptance and operator approval; neither changes tracking evidence."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    Sha256,
    Symbol,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchScope, ProfileID
from retailops_ai.forecast_jobs.v12_contracts import MAX_ROWS, V12ExecutionLimits, V12RuntimePin
from retailops_ai.model_lifecycle.contracts import GATE_SCHEMA, GATES, Gate, Receipt
from retailops_ai.model_lifecycle.v12_development import V12DevelopmentAcceptance

QualificationID = Annotated[str, Field(pattern=r"^v12-qualification-sha256-[0-9a-f]{64}$")]
ReleaseID = Annotated[str, Field(pattern=r"^v12-inference-release-sha256-[0-9a-f]{64}$")]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class V12SourcePolicy(Contract):
    version: Literal["forecast-v12-source-policy-1.0.0", "forecast-v12-source-policy-1.1.0"] = (
        "forecast-v12-source-policy-1.0.0"
    )
    mode: Literal["same_verified_feature_package", "verified_inference_snapshot"] = (
        "same_verified_feature_package"
    )
    source_dataset_id: Annotated[str, Field(pattern=r"^source-sha256-[0-9a-f]{64}$")] | None = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    feature_set_id: Annotated[str, Field(pattern=r"^features-sha256-[0-9a-f]{64}$")]
    curated_descriptor_sha256: Sha256
    input_role: Literal["inference"] = "inference"
    input_schema_sha256: Sha256
    source_change: Literal["new_qualification_and_review_required"] = (
        "new_qualification_and_review_required"
    )

    @model_validator(mode="after")
    def source_boundary(self) -> Self:
        legacy = self.version == "forecast-v12-source-policy-1.0.0"
        if (
            self.mode
            != ("same_verified_feature_package" if legacy else "verified_inference_snapshot")
            or (self.source_dataset_id is not None) != (not legacy)
            or (self.snapshot_id is not None) != (not legacy)
        ):
            raise ValueError("v12_source_policy_version_or_lineage")
        return self


class V12InferenceContext(Contract):
    version: Literal["forecast-v12-inference-context-1.0.0"] = (
        "forecast-v12-inference-context-1.0.0"
    )
    purpose: Literal["serving_load_predict_acceptance", "qualified_forecast_v12"]
    source_policy: V12SourcePolicy
    development_acceptance: V12DevelopmentAcceptance | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    qualification_id: QualificationID | None = None
    release_id: ReleaseID | None = None
    serving_eligible: FalseFlag | TrueFlag

    @model_validator(mode="after")
    def authorization_boundary(self) -> Self:
        approved = self.purpose == "qualified_forecast_v12"
        if (
            self.serving_eligible != approved
            or (self.qualification_id is not None) != approved
            or (self.release_id is not None) != approved
        ):
            raise ValueError("v12_inference_approval_binding")
        return self


class V12Qualification(Contract):
    version: Literal["forecast-v12-qualification-1.0.0"] = "forecast-v12-qualification-1.0.0"
    qualification_id: QualificationID
    purpose: Literal["serving_load_predict_acceptance"] = "serving_load_predict_acceptance"
    pin: V12RuntimePin
    source_policy: V12SourcePolicy
    development_acceptance: V12DevelopmentAcceptance | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    limits: V12ExecutionLimits
    smoke_profile_id: ProfileID
    smoke_scope: BatchScope
    smoke_rows: Annotated[int, Field(ge=1, le=MAX_ROWS)]
    smoke: Receipt
    inputs: Receipt
    created_at: UtcTime
    valid_until: UtcTime
    source_packages_verified: TrueFlag = True
    full_export_verified: TrueFlag = True
    repeatability_verified: TrueFlag = True
    serving_eligible: FalseFlag = False

    @model_validator(mode="after")
    def qualification_identity(self) -> Self:
        if self.development_acceptance is not None:
            self.development_acceptance.verify_pin(self.pin)
        if (
            (self.pin.forecast_model_status != "ready" and self.development_acceptance is None)
            or not self.created_at < self.valid_until <= self.created_at + timedelta(days=7)
            or self.qualification_id
            != "v12-qualification-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"qualification_id"}))
        ):
            raise ValueError("v12_qualification_identity_or_readiness")
        return self


class V12ApprovalRequest(Contract):
    qualification_id: QualificationID
    image_digest: ImageDigest
    gates: dict[str, Gate] = Field(json_schema_extra=GATE_SCHEMA)
    reason: Annotated[str, Field(min_length=12, max_length=500)]

    @model_validator(mode="after")
    def complete_review(self) -> Self:
        if (
            set(self.gates) != GATES
            or any(gate.status != "passed" for gate in self.gates.values())
            or self.reason != self.reason.strip()
        ):
            raise ValueError("v12_approval_requires_all_review_gates")
        return self


class V12InferenceRelease(Contract):
    """Private local approval capsule, not an MLflow version or an active release head."""

    version: Literal["forecast-v12-inference-release-1.0.0"] = (
        "forecast-v12-inference-release-1.0.0"
    )
    release_id: ReleaseID
    purpose: Literal["qualified_forecast_v12"] = "qualified_forecast_v12"
    qualification: V12Qualification
    approval: V12ApprovalRequest
    reviewed_by: Symbol
    reviewed_at: UtcTime
    serving_eligible: TrueFlag = True
    registered_in_mlflow: FalseFlag = False
    activated_as_champion: FalseFlag = False

    @model_validator(mode="after")
    def release_identity(self) -> Self:
        if (
            self.approval.qualification_id != self.qualification.qualification_id
            or not self.qualification.created_at
            <= self.reviewed_at
            < self.qualification.valid_until
            or self.release_id
            != "v12-inference-release-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"release_id"}))
        ):
            raise ValueError("v12_release_identity_or_review_binding")
        return self

    def context(self) -> V12InferenceContext:
        return V12InferenceContext(
            purpose="qualified_forecast_v12",
            source_policy=self.qualification.source_policy,
            development_acceptance=self.qualification.development_acceptance,
            qualification_id=self.qualification.qualification_id,
            release_id=self.release_id,
            serving_eligible=True,
        )
