"""Explicit corpus decisions and offline-only index release contracts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import ConfigID, CorpusID
from retailops_ai.knowledge.indexes import IndexID, IndexManifest, SpaceID

ReviewID = Annotated[str, Field(pattern=r"^corpus-review-sha256-[0-9a-f]{64}$")]
ValidationID = Annotated[str, Field(pattern=r"^index-validation-sha256-[0-9a-f]{64}$")]
ChangeID = Annotated[str, Field(pattern=r"^rag-change-[0-9a-f]{32}$")]
Lane = Literal["retrieval", "offline_test"]


class CorpusApproval(Contract):
    schema_version: Literal["1.0"]
    review_id: ReviewID
    corpus_id: CorpusID
    corpus_config_id: ConfigID
    environment: Literal["local", "test"]
    review_owner: Symbol
    reviewer: Symbol
    reviewer_kind: Literal["human", "approved_pipeline"]
    decision: Literal["approved"]
    scope: Literal["sources_status_access_and_exclusions"]
    reviewed_at: UtcTime

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.review_id != "corpus-review-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"review_id"})
        ):
            raise ValueError("corpus_review_identity_mismatch")
        if self.reviewer_kind == "human" and self.reviewer != self.review_owner:
            raise ValueError("corpus_owner_review_required")
        return self


class MechanicalChecks(Contract):
    complete_graph: bool
    source_metadata: bool
    citation_binding: bool
    vector_binding: bool
    deterministic_fake_vectors: bool

    def passed(self) -> bool:
        return all(self.model_dump().values())


class IndexValidation(Contract):
    schema_version: Literal["1.0"]
    validation_id: ValidationID
    policy_version: Literal["fake-mechanical-validation-v1"]
    index_id: IndexID
    corpus_id: CorpusID
    space_id: SpaceID
    environment: Literal["local", "test"]
    provider: Literal["fake"]
    golden_evaluation: Literal["not_evaluated_fake_vectors"]
    checks: MechanicalChecks
    result: Literal["passed", "failed"]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.validation_id != "index-validation-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"validation_id"})
        ):
            raise ValueError("index_validation_identity_mismatch")
        if (self.result == "passed") != self.checks.passed():
            raise ValueError("index_validation_result_mismatch")
        return self


class SwitchRequest(Contract):
    schema_version: Literal["1.0"]
    request_id: ChangeID
    environment: Literal["local", "test"]
    lane: Lane
    operation: Literal["activate", "rollback"]
    target_index_id: IndexID
    expected_generation: Annotated[int, Field(ge=0, le=2**63 - 2)]
    actor: Symbol


class IndexPin(Contract):
    schema_version: Literal["1.0"]
    environment: Literal["test"]
    lane: Literal["offline_test"]
    purpose: Literal["lifecycle_validation_only"]
    generation: Annotated[int, Field(ge=1, le=2**63 - 1)]
    request_id: ChangeID
    review_id: ReviewID
    validation_id: ValidationID
    manifest: IndexManifest

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.manifest.environment != self.environment:
            raise ValueError("index_pin_environment_mismatch")
        return self


class SwitchResult(Contract):
    status: Literal["applied", "replayed"]
    pin: IndexPin


def approval_matches(
    approval: CorpusApproval, corpus_id: str, config_id: str, owner: str, environment: str
) -> bool:
    return (
        approval.corpus_id,
        approval.corpus_config_id,
        approval.review_owner,
        approval.environment,
    ) == (corpus_id, config_id, owner, environment)
