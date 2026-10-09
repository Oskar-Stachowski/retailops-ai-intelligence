"""Server-owned question requirements bound to exact retrieved evidence, never to rank."""

import unicodedata
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import ChunkID, MarkdownChunk


def question_key(question: str) -> str:
    # Only presentation differences are normalized; no inferred intent or keyword match.
    return " ".join(unicodedata.normalize("NFC", question).casefold().split())


class DocumentSupport(Contract):
    chunk_id: ChunkID
    chunk_sha256: Sha256
    quote: Annotated[str, Field(min_length=1, max_length=800)]

    @field_validator("quote")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\0" in value:
            raise ValueError("invalid_support_quote")
        return value

    def matches(self, chunk: MarkdownChunk) -> bool:
        return (
            chunk.chunk_id == self.chunk_id
            and canonical_sha256(chunk.model_dump(mode="json")) == self.chunk_sha256
            and self.quote in chunk.text
        )


class DocumentRequirement(Contract):
    requirement_id: Symbol
    description: Annotated[str, Field(min_length=1, max_length=400)]
    supports: Annotated[tuple[DocumentSupport, ...], Field(min_length=1, max_length=5)]


class DocumentEvidenceRule(Contract):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    intent: Literal["documentation", "verified_state"]
    requirements: Annotated[tuple[DocumentRequirement, ...], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def unambiguous(self) -> Self:
        if not question_key(self.question) or "\0" in self.question:
            raise ValueError("invalid_document_question")
        if len({row.requirement_id for row in self.requirements}) != len(self.requirements):
            raise ValueError("duplicate_document_requirement")
        return self

    def matching_quotes(self, chunk: MarkdownChunk) -> dict[str, tuple[str, ...]]:
        """A quote can cover several requirements; an unrelated high-score hit covers none."""
        matches: dict[str, list[str]] = {}
        for requirement in self.requirements:
            for support in requirement.supports:
                if support.matches(chunk):
                    covered = matches.setdefault(support.quote, [])
                    if requirement.requirement_id not in covered:
                        covered.append(requirement.requirement_id)
        return {quote: tuple(ids) for quote, ids in matches.items()}
