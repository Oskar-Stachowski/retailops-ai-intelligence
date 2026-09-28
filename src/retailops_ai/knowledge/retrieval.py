"""Bounded retrieval contracts; source text is never an instruction or an answer."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import MarkdownChunk
from retailops_ai.knowledge.contracts import (
    AccessClass,
    CorpusID,
    DocumentID,
    DocumentStatus,
    Repository,
)
from retailops_ai.knowledge.indexes import IndexID

DocumentType = Literal[
    "architecture", "contract", "guide", "runbook", "policy", "plan", "evidence", "model_card"
]
RetrievalConfigID = Annotated[str, Field(pattern=r"^retrieval-config-sha256-[0-9a-f]{64}$")]


class RetrievalConfig(Contract):
    schema_version: Literal["1.0"]
    retrieval_version: Literal["pgvector-cosine-exact-v1"]
    max_top_k: Literal[5]
    max_context_tokens: Literal[6000]
    max_context_bytes: Literal[24000]
    max_chunks_per_document: Literal[2]
    candidate_limit: Literal[256]
    min_cosine_score: Annotated[float, Field(ge=-1, le=1)]
    diversification: Literal["repository-then-document-v1", "score-then-document-v1"]
    size_estimator: Literal["serialized-hits-utf8-div4-v1"]

    def config_id(self) -> str:
        return "retrieval-config-sha256-" + canonical_sha256(self.model_dump(mode="json"))


class RetrievalFilters(Contract):
    repositories: Annotated[tuple[Repository, ...], Field(min_length=1, max_length=2)] | None = None
    document_types: (
        Annotated[tuple[DocumentType, ...], Field(min_length=1, max_length=8)] | None
    ) = None
    document_statuses: (
        Annotated[tuple[DocumentStatus, ...], Field(min_length=1, max_length=5)] | None
    ) = None
    access_classes: Annotated[tuple[AccessClass, ...], Field(min_length=1, max_length=3)] | None = (
        None
    )

    @field_validator(
        "repositories", "document_types", "document_statuses", "access_classes", mode="before"
    )
    @classmethod
    def json_arrays(cls, value: object) -> object:
        # FastAPI validates decoded JSON; preserve immutable tuples internally.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def unique(self) -> Self:
        for values in (
            self.repositories,
            self.document_types,
            self.document_statuses,
            self.access_classes,
        ):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("duplicate_retrieval_filter")
        return self


class RetrievalRequest(Contract):
    schema_version: Literal["1.0"]
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    purpose: Literal["documentation", "implementation", "verified_state", "history"] = (
        "documentation"
    )
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: Annotated[int, Field(ge=1, le=5)] = 5
    max_context_tokens: Annotated[int, Field(ge=1, le=6000)] = 6000

    @field_validator("question")
    @classmethod
    def bounded_question(cls, value: str) -> str:
        if not value.strip() or "\0" in value or len(value.encode("utf-8")) > 8000:
            raise ValueError("invalid_retrieval_question")
        return value


class KnowledgeHit(Contract):
    score: Annotated[float, Field(ge=-1, le=1)]
    chunk: MarkdownChunk
    claim_kind: Literal["plan", "implementation", "verified_evidence", "historical_reference"]


class RetrievalResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok", "insufficient_evidence"]
    index_id: IndexID
    corpus_id: CorpusID
    retrieval_config_id: RetrievalConfigID
    retrieval_version: Literal["pgvector-cosine-exact-v1"] = "pgvector-cosine-exact-v1"
    provider: Literal["fake"] = "fake"
    semantic_quality: Literal["not_evaluated_fake_vectors"] = "not_evaluated_fake_vectors"
    content_trust: Literal["untrusted_reference"] = "untrusted_reference"
    answer_generation: Literal["not_implemented"] = "not_implemented"
    items: Annotated[tuple[KnowledgeHit, ...], Field(max_length=5)]
    context_tokens: Annotated[int, Field(ge=0, le=6000)]
    context_bytes: Annotated[int, Field(ge=0, le=24000)]

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.status == "ok") != bool(self.items):
            raise ValueError("retrieval_outcome_mismatch")
        if len({hit.chunk.chunk_id for hit in self.items}) != len(self.items):
            raise ValueError("duplicate_retrieval_chunk")
        return self


class DocumentDenial(Contract):
    schema_version: Literal["1.0"]
    environment: Literal["local", "test"]
    document_id: DocumentID
    actor: Symbol
    reason: Symbol
