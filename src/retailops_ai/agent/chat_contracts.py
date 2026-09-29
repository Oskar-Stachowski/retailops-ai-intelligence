"""Provider-independent planning and answer drafts; identities stay server-owned."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, TypeAdapter, field_validator, model_validator

from retailops_ai.agent.tools import ToolInput
from retailops_ai.data_contracts.common import CommitSha, Contract, Symbol, TrueFlag, UtcTime
from retailops_ai.knowledge.chunks import ChunkID
from retailops_ai.knowledge.contracts import DocumentStatus, Repository, safe_path

ShortText = Annotated[str, Field(min_length=1, max_length=2000)]
EvidenceRef = Annotated[str, Field(min_length=1, max_length=2048)]


class PlanDraft(Contract):
    kind: Literal["tool_plan"]
    tools: list[ToolInput] = Field(max_length=6)


class EvidenceClaim(Contract):
    claim: ShortText
    source_type: Literal["tool", "document", "calculation"]
    source_ref: EvidenceRef
    as_of: UtcTime | None
    supporting_refs: list[EvidenceRef] = Field(max_length=8)
    calculation_id: Symbol | None = None

    @model_validator(mode="after")
    def source_shape(self) -> Self:
        if (self.source_type == "document") != (self.as_of is None):
            raise ValueError("claim_as_of_mismatch")
        if self.source_type == "calculation":
            if self.calculation_id is None or len(self.supporting_refs) < 2:
                raise ValueError("calculation_requires_registered_formula_and_operands")
        elif self.calculation_id is not None:
            raise ValueError("unexpected_calculation_id")
        return self


class DraftCitation(Contract):
    repository: Repository
    commit_sha: CommitSha
    path: Annotated[str, Field(max_length=240), AfterValidator(safe_path)]
    heading: Annotated[str, Field(max_length=1200)]
    chunk_id: ChunkID
    document_status: DocumentStatus
    source_ref: EvidenceRef


class DraftAction(Contract):
    action: ShortText
    priority: Literal["low", "medium", "high"]
    rationale: ShortText
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=8)
    requires_human_review: TrueFlag


class DomainFreshness(Contract):
    as_of: UtcTime | None
    status: Literal["current", "stale", "missing", "unavailable", "not_requested"]

    @model_validator(mode="after")
    def timestamp(self) -> Self:
        if (self.status in {"current", "stale"}) != (self.as_of is not None):
            raise ValueError("freshness_timestamp_mismatch")
        return self


class AnswerFreshness(Contract):
    sales: DomainFreshness
    inventory: DomainFreshness
    predictions: DomainFreshness


class AnswerDraft(Contract):
    kind: Literal["answer"]
    outcome: Literal["answered", "insufficient_evidence", "refused"]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    evidence: list[EvidenceClaim] = Field(max_length=20)
    recommended_actions: list[DraftAction] = Field(max_length=5)
    confidence: Literal["low", "medium", "high"]
    data_freshness: AnswerFreshness
    citations: list[DraftCitation] = Field(max_length=5)
    limitations: list[ShortText] = Field(max_length=10)

    @field_validator("summary")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("empty_answer_summary")
        return value

    @model_validator(mode="after")
    def outcome_shape(self) -> Self:
        if self.outcome == "answered" and not self.evidence:
            raise ValueError("answered_requires_evidence")
        if self.outcome != "answered" and self.confidence != "low":
            raise ValueError("limited_outcome_requires_low_confidence")
        if self.outcome == "refused" and self.recommended_actions:
            raise ValueError("refusal_has_no_actions")
        if len({c.chunk_id for c in self.citations}) != len(self.citations):
            raise ValueError("duplicate_draft_citation")
        return self


ChatDraft = Annotated[PlanDraft | AnswerDraft, Field(discriminator="kind")]
DRAFT: TypeAdapter[ChatDraft] = TypeAdapter(ChatDraft)


class ProviderUsage(Contract):
    input_tokens: Annotated[int, Field(ge=0, le=16000)]
    output_tokens: Annotated[int, Field(ge=0, le=1500)]


class ProviderReply(Contract):
    body: Annotated[str, Field(max_length=131072)]
    usage: ProviderUsage
