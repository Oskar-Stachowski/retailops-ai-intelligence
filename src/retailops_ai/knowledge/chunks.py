"""Candidate chunks with content identity separate from revision citations."""

import hashlib
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import (
    MAX_MARKDOWN_BYTES,
    CorpusID,
    CorpusManifest,
    DocumentID,
    ManifestDocument,
)

ChunkID = Annotated[str, Field(pattern=r"^chunk-sha256-[0-9a-f]{64}$")]
ChunkConfigID = Annotated[str, Field(pattern=r"^chunker-config-sha256-[0-9a-f]{64}$")]
ChunkManifestID = Annotated[str, Field(pattern=r"^chunks-sha256-[0-9a-f]{64}$")]
BlockType = Literal[
    "paragraph",
    "bullet_list",
    "ordered_list",
    "blockquote",
    "fence",
    "code_block",
    "table",
    "html_block",
    "reference_definition",
]
MAX_DOCUMENT_CHUNKS = 4096
MAX_TOTAL_CHUNKS = 32768


class ChunkerConfig(Contract):
    schema_version: Literal["1.0"]
    chunker_version: Literal["markdown-blocks-v1"]
    parser: Literal["markdown-it-py"]
    parser_version: Literal["4.2.0"]
    parser_preset: Literal["commonmark-table-v1"]
    normalization: Literal["utf8-lf-v1"]
    max_utf8_bytes: Annotated[int, Field(ge=256, le=16000)]
    size_estimator: Literal["utf8-bytes-div4-v1"]
    navigation_policy: Literal["toc-headings-and-anchor-only-blocks-v1"]
    duplicate_policy: Literal["same-document-heading-block-content-v1"]

    def config_id(self) -> str:
        return "chunker-config-sha256-" + canonical_sha256(self.model_dump(mode="json"))


class Heading(Contract):
    level: Annotated[int, Field(ge=1, le=6)]
    title: Annotated[str, Field(min_length=1, max_length=200)]


class SourceLocation(Contract):
    start_offset: Annotated[int, Field(ge=0, le=MAX_MARKDOWN_BYTES)]
    end_offset: Annotated[int, Field(gt=0, le=MAX_MARKDOWN_BYTES)]
    start_line: Annotated[int, Field(ge=1, le=MAX_MARKDOWN_BYTES)]
    end_line: Annotated[int, Field(ge=1, le=MAX_MARKDOWN_BYTES)]
    source_ref: str

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_offset >= self.end_offset or self.start_line > self.end_line:
            raise ValueError("source_range_invalid")
        return self


class MarkdownChunk(ManifestDocument):
    chunk_id: ChunkID
    chunk_index: Annotated[int, Field(ge=0, lt=MAX_DOCUMENT_CHUNKS)]
    chunker_version: Literal["markdown-blocks-v1"]
    chunker_config_id: ChunkConfigID
    heading_path: Annotated[tuple[Heading, ...], Field(max_length=6)]
    block_type: BlockType
    text: Annotated[str, Field(min_length=1, max_length=16000)]
    content_checksum: Sha256
    token_estimate: Annotated[int, Field(ge=1, le=4000)]
    occurrences: Annotated[tuple[SourceLocation, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def content_binding(self) -> Self:
        raw = self.text.encode("utf-8")
        if not self.text.strip() or "\0" in self.text or "\r" in self.text:
            raise ValueError("chunk_content_invalid")
        if self.content_checksum != hashlib.sha256(raw).hexdigest():
            raise ValueError("chunk_checksum_mismatch")
        if self.token_estimate != (len(raw) + 3) // 4:
            raise ValueError("chunk_token_estimate_mismatch")
        levels = [h.level for h in self.heading_path]
        if levels != sorted(set(levels)):
            raise ValueError("heading_path_invalid")
        if self.chunk_id != chunk_id(
            self.document_id,
            self.heading_path,
            self.block_type,
            self.content_checksum,
            self.chunker_config_id,
        ):
            raise ValueError("chunk_identity_mismatch")
        offsets = [location.start_offset for location in self.occurrences]
        if offsets != sorted(set(offsets)):
            raise ValueError("chunk_occurrences_invalid")
        for location in self.occurrences:
            if location.end_offset - location.start_offset != len(self.text):
                raise ValueError("chunk_source_length_mismatch")
            if location.source_ref != line_source_ref(
                self.source_ref, location.start_line, location.end_line
            ):
                raise ValueError("chunk_citation_binding_mismatch")
        return self


def chunk_id(
    document_id: str,
    heading_path: tuple[Heading, ...],
    block_type: str,
    content_checksum: str,
    config_id: str,
) -> str:
    return "chunk-sha256-" + canonical_sha256(
        {
            "document_id": document_id,
            "heading_path": [heading.model_dump(mode="json") for heading in heading_path],
            "block_type": block_type,
            "content_checksum": content_checksum,
            "chunker_config_id": config_id,
        }
    )


def line_source_ref(document_ref: str, start: int, end: int) -> str:
    return f"{document_ref}#L{start}-L{end}"


class OmittedBlock(Contract):
    location: SourceLocation
    reason: Literal[
        "structural_heading",
        "navigation_section",
        "anchor_navigation",
        "html_comment",
        "thematic_break",
    ]


class DocumentChunkMap(Contract):
    document_id: DocumentID
    source_characters: Annotated[int, Field(gt=0, le=MAX_MARKDOWN_BYTES)]
    source_lines: Annotated[int, Field(gt=0, le=MAX_MARKDOWN_BYTES + 1)]
    chunk_ids: Annotated[tuple[ChunkID, ...], Field(max_length=MAX_DOCUMENT_CHUNKS)]
    omitted_blocks: Annotated[tuple[OmittedBlock, ...], Field(max_length=8192)]
    outcome: Literal["chunked", "no_retrievable_content"]

    @model_validator(mode="after")
    def outcome_binding(self) -> Self:
        if (self.outcome == "chunked") != bool(self.chunk_ids):
            raise ValueError("document_chunk_outcome_mismatch")
        if len(set(self.chunk_ids)) != len(self.chunk_ids):
            raise ValueError("duplicate_document_chunk")
        return self


class ChunkManifest(Contract):
    schema_version: Literal["1.0"]
    lifecycle: Literal["candidate"]
    content_trust: Literal["untrusted_reference"]
    corpus_id: CorpusID
    corpus: CorpusManifest
    chunker: ChunkerConfig
    chunker_config_id: ChunkConfigID
    chunk_manifest_id: ChunkManifestID
    documents: Annotated[tuple[DocumentChunkMap, ...], Field(min_length=2, max_length=128)]
    chunks: Annotated[tuple[MarkdownChunk, ...], Field(max_length=MAX_TOTAL_CHUNKS)]

    @model_validator(mode="after")
    def graph(self) -> Self:
        if self.chunk_manifest_id != "chunks-sha256-" + canonical_sha256(self.identity_data()):
            raise ValueError("chunk_manifest_identity_mismatch")
        if self.corpus_id != self.corpus.corpus_id:
            raise ValueError("chunk_corpus_identity_mismatch")
        if self.chunker_config_id != self.chunker.config_id():
            raise ValueError("chunk_config_identity_mismatch")
        expected = [d.document_id for d in self.corpus.documents]
        if [d.document_id for d in self.documents] != expected:
            raise ValueError("chunk_document_coverage_mismatch")
        ids = [chunk.chunk_id for chunk in self.chunks]
        flattened = [chunk for document in self.documents for chunk in document.chunk_ids]
        if flattened != ids or len(set(ids)) != len(ids):
            raise ValueError("chunk_coverage_or_order_mismatch")
        chunk_by_id = {chunk.chunk_id: chunk for chunk in self.chunks}
        for mapping, source in zip(self.documents, self.corpus.documents, strict=True):
            locations = [omitted.location for omitted in mapping.omitted_blocks]
            for ordinal, cid in enumerate(mapping.chunk_ids):
                chunk = chunk_by_id[cid]
                if chunk.chunk_index != ordinal or (
                    chunk.model_dump(include=set(ManifestDocument.model_fields))
                    != source.model_dump()
                ):
                    raise ValueError("chunk_source_metadata_mismatch")
                if (
                    chunk.chunker_config_id != self.chunker_config_id
                    or chunk.chunker_version != self.chunker.chunker_version
                    or len(chunk.text.encode("utf-8")) > self.chunker.max_utf8_bytes
                ):
                    raise ValueError("chunk_config_binding_mismatch")
                locations.extend(chunk.occurrences)
            ordered = sorted(locations, key=lambda location: location.start_offset)
            previous_end = 0
            for location in ordered:
                if (
                    location.start_offset < previous_end
                    or location.end_offset > mapping.source_characters
                    or location.end_line > mapping.source_lines
                    or location.source_ref
                    != line_source_ref(source.source_ref, location.start_line, location.end_line)
                ):
                    raise ValueError("chunk_source_range_mismatch")
                previous_end = location.end_offset
        return self

    def identity_data(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"chunk_manifest_id"})
