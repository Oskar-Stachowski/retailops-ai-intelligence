"""Strict stockout-only approvals, registry bindings and active release pins."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    Sha256,
    Symbol,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.model_lifecycle.contracts import GATES, DecisionID, Gate, Receipt, RunID
from retailops_ai.source_snapshot.files import relative_path
from retailops_ai.stockout_runtime.contracts import RuntimeReleasePin, ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.inputs import PhysicalScope

MODEL = "retailops-stockout-risk"
TEST_MODEL = "retailops-stockout-risk-test-mechanics"
ModelName = Literal["retailops-stockout-risk", "retailops-stockout-risk-test-mechanics"]
Version = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,8}$")]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
QualificationID = Annotated[
    str, Field(pattern=r"^stockout-qualification-serving-sha256-[0-9a-f]{64}$")
]
ApprovalID = Annotated[str, Field(pattern=r"^stockout-approval-sha256-[0-9a-f]{64}$")]
ReleaseID = Annotated[str, Field(pattern=r"^stockout-release-sha256-[0-9a-f]{64}$")]
REVIEW_GATES = GATES | {"calibration", "threshold_capacity"}
Purpose = Literal["qualified_stockout", "stockout_mechanics_only"]


class StockoutQualification(Contract):
    version: Literal["stockout-serving-qualification-1.0.0"] = (
        "stockout-serving-qualification-1.0.0"
    )
    qualification_id: QualificationID
    purpose: Purpose
    recipe: ScoringRecipe
    policy: ScoringPolicy
    model_card: Receipt
    final_quality: Receipt | None
    final_campaign_id: (
        Annotated[str, Field(pattern=r"^stockout-final-campaign-sha256-[0-9a-f]{64}$")] | None
    )
    quality_status: Literal["passed_independent_final_campaign", "not_evaluated_mechanics_only"]
    public_inputs: Receipt
    smoke: Receipt
    signature: Receipt
    smoke_scope: PhysicalScope
    smoke_as_of: UtcTime
    smoke_rows: Annotated[int, Field(ge=1, le=100)]
    created_at: UtcTime
    valid_until: UtcTime
    source_packages_verified: TrueFlag = True
    complete_pipeline_verified: TrueFlag = True
    repeatability_verified: TrueFlag = True
    serving_eligible: FalseFlag = False

    @model_validator(mode="after")
    def qualification_identity(self) -> Self:
        qualified = self.purpose == "qualified_stockout"
        if (
            self.recipe.pin != self.policy.pin
            or qualified != (self.final_quality is not None and self.final_campaign_id is not None)
            or (self.final_quality is None) != (self.final_campaign_id is None)
            or qualified != (self.quality_status == "passed_independent_final_campaign")
            or self.smoke_as_of < self.recipe.pin.selection_known_at
            or self.smoke_rows
            != len(self.smoke_scope.product_ids) * len(self.smoke_scope.stock_location_ids)
            or not self.created_at < self.valid_until <= self.created_at + timedelta(days=7)
            or self.qualification_id
            != "stockout-qualification-serving-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"qualification_id"}))
        ):
            raise ValueError("stockout_qualification_identity_quality_or_recipe")
        return self


class ApprovalRequest(Contract):
    qualification_id: QualificationID
    image_digest: ImageDigest
    gates: dict[str, Gate]
    reason: Annotated[str, Field(min_length=12, max_length=500)]

    @model_validator(mode="after")
    def reviewed(self) -> Self:
        if (
            set(self.gates) != REVIEW_GATES
            or any(g.status != "passed" for g in self.gates.values())
            or self.reason != self.reason.strip()
        ):
            raise ValueError("stockout_approval_requires_all_review_gates")
        return self


class StockoutApproval(Contract):
    version: Literal["stockout-inference-approval-1.0.0"] = "stockout-inference-approval-1.0.0"
    release_id: ApprovalID
    qualification: StockoutQualification
    approval: ApprovalRequest
    reviewed_by: Symbol
    reviewed_at: UtcTime
    serving_eligible: TrueFlag = True
    registered_in_mlflow: FalseFlag = False
    activated_as_champion: FalseFlag = False

    @model_validator(mode="after")
    def review_binding(self) -> Self:
        q = self.qualification
        if (
            self.approval.qualification_id != q.qualification_id
            or not q.created_at <= self.reviewed_at < q.valid_until
            or self.release_id
            != "stockout-approval-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"release_id"}))
        ):
            raise ValueError("stockout_approval_identity_or_review_binding")
        return self


def capsule_names(*, final: bool) -> set[str]:
    names = {
        "approval.json",
        "qualification.json",
        "recipe.json",
        "policy.json",
        "model_card.json",
        "smoke.json",
        "inputs.json",
        "signature.json",
    } | {f"reports/{gate}.json" for gate in REVIEW_GATES}
    return names | {"final_quality.json", "campaign_freeze.json"} if final else names


class StockoutRegistrySource(Contract):
    model_name: ModelName
    mlflow_run_id: RunID
    source_uri: str
    approval_sha256: Sha256
    approval: StockoutApproval
    files: dict[str, Receipt]

    @field_validator("source_uri")
    @classmethod
    def trusted_source(cls, value: str) -> str:
        if not value.startswith("mlflow-artifacts:/"):
            raise ValueError("stockout_registry_untrusted_source")
        relative_path(value.removeprefix("mlflow-artifacts:/").lstrip("/"))
        if not value.endswith("/stockout-release"):
            raise ValueError("stockout_registry_source_boundary")
        return value

    @model_validator(mode="after")
    def namespace_and_inventory(self) -> Self:
        final = self.approval.qualification.purpose == "qualified_stockout"
        if (
            final != (self.model_name == MODEL)
            or set(self.files) != capsule_names(final=final)
            or self.files["approval.json"].sha256 != self.approval_sha256
            or any(r.size_bytes > 16 * 1024**2 for r in self.files.values())
        ):
            raise ValueError("stockout_registry_namespace_or_capsule_inventory")
        return self


class StockoutBinding(StockoutRegistrySource):
    model_version: Version


class StockoutLifecycleRequest(Contract):
    decision_id: DecisionID
    action: Literal["register", "reject", "promote", "rollback"]
    model_name: ModelName
    mlflow_run_id: RunID | None = None
    model_version: Version | None = None
    approval_id: ApprovalID
    approval_sha256: Sha256
    image_digest: ImageDigest | None = None
    reason: Annotated[str, Field(min_length=12, max_length=500)]

    @model_validator(mode="after")
    def request_boundary(self) -> Self:
        registering = self.action == "register"
        if (
            registering != (self.mlflow_run_id is not None)
            or registering == (self.model_version is not None)
            or (self.action == "promote") != (self.image_digest is not None)
            or self.reason != self.reason.strip()
        ):
            raise ValueError("stockout_lifecycle_request_boundary")
        return self


class StockoutModelRelease(Contract):
    version: Literal["stockout-database-release-1.0.0"] = "stockout-database-release-1.0.0"
    release_id: ReleaseID
    decision_id: DecisionID
    binding: StockoutBinding
    image_digest: ImageDigest
    previous_release_id: ReleaseID | None
    previous_version: Version | None
    restored_from_release_id: ReleaseID | None

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (
            (self.previous_release_id is None) != (self.previous_version is None)
            or self.image_digest != self.binding.approval.approval.image_digest
            or self.release_id
            != "stockout-release-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"release_id"}))
        ):
            raise ValueError("stockout_database_release_identity")
        return self

    def runtime_pin(self) -> RuntimeReleasePin:
        q = self.binding.approval.qualification
        return RuntimeReleasePin(
            model_name=self.binding.model_name,
            model_version=self.binding.model_version,
            release_id=self.release_id,
            image_digest=self.image_digest,
            recipe_content_sha256=canonical_sha256(q.recipe.model_dump(mode="json")),
            policy_content_sha256=canonical_sha256(q.policy.model_dump(mode="json")),
        )


def database_release(**content: object) -> StockoutModelRelease:
    raw = {"version": "stockout-database-release-1.0.0", **content}
    raw["release_id"] = "stockout-release-sha256-" + canonical_sha256(raw)
    return StockoutModelRelease.model_validate_json(canonical_bytes(raw))
