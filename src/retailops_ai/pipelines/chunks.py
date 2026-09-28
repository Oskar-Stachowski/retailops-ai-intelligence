"""Rebuild complete immutable chunk candidates from validated pinned sources."""

import hashlib
import json
from bisect import bisect_right
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from retailops_ai.adapters.git_documents import CorpusError, GitDocuments
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import (
    MAX_DOCUMENT_CHUNKS,
    MAX_TOTAL_CHUNKS,
    ChunkerConfig,
    ChunkManifest,
    DocumentChunkMap,
    MarkdownChunk,
    OmittedBlock,
    SourceLocation,
    chunk_id,
    line_source_ref,
)
from retailops_ai.knowledge.contracts import CorpusRegistry, Repository
from retailops_ai.knowledge.markdown import bounded_pieces, line_offsets, parse_blocks
from retailops_ai.pipelines.corpus import build_candidate, decode_json


def load_chunker_config(path: Path) -> ChunkerConfig:
    with path.open("rb") as source:
        raw = source.read(64001)
    if len(raw) > 64000:
        raise CorpusError("chunker_config_too_large")
    decode_json(raw)
    return ChunkerConfig.model_validate_json(raw)


def source_location(reference: str, offsets: list[int], start: int, end: int) -> SourceLocation:
    first, last = bisect_right(offsets, start), bisect_right(offsets, end - 1)
    return SourceLocation(
        start_offset=start,
        end_offset=end,
        start_line=first,
        end_line=last,
        source_ref=line_source_ref(reference, first, last),
    )


def build_chunks(
    registry: CorpusRegistry, config: ChunkerConfig, repositories: dict[Repository, Path]
) -> ChunkManifest:
    try:
        installed_version = version(config.parser)
    except PackageNotFoundError as exc:
        raise CorpusError("markdown_parser_unavailable") from exc
    if installed_version != config.parser_version:
        raise CorpusError("markdown_parser_version_mismatch")
    corpus = build_candidate(registry, repositories)
    readers = {name: GitDocuments(path, name) for name, path in repositories.items()}
    chunks: list[MarkdownChunk] = []
    mappings = []
    for document in corpus.documents:
        raw = readers[document.repository].read(document.commit_sha, document.path)
        if hashlib.sha256(raw).hexdigest() != document.byte_sha256:
            raise CorpusError("document_checksum_mismatch")
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if "markdown-sha256-" + hashlib.sha256(text.encode()).hexdigest() != document.content_id:
            raise CorpusError("document_content_identity_mismatch")
        offsets = line_offsets(text)

        blocks, omissions = parse_blocks(text)
        document_chunks: dict[str, MarkdownChunk] = {}
        for block in blocks:
            for start, end in bounded_pieces(text, block.start, block.end, config.max_utf8_bytes):
                body = text[start:end]
                checksum = hashlib.sha256(body.encode()).hexdigest()
                cid = chunk_id(
                    document.document_id,
                    block.heading_path,
                    block.block_type,
                    checksum,
                    config.config_id(),
                )
                occurrence = source_location(document.source_ref, offsets, start, end)
                if cid in document_chunks:
                    existing = document_chunks[cid]
                    document_chunks[cid] = MarkdownChunk(
                        **existing.model_dump(exclude={"occurrences"}),
                        occurrences=(*existing.occurrences, occurrence),
                    )
                else:
                    if len(document_chunks) >= MAX_DOCUMENT_CHUNKS:
                        raise CorpusError("document_chunk_limit_exceeded")
                    document_chunks[cid] = MarkdownChunk(
                        **document.model_dump(),
                        chunk_id=cid,
                        chunk_index=len(document_chunks),
                        chunker_version=config.chunker_version,
                        chunker_config_id=config.config_id(),
                        heading_path=block.heading_path,
                        block_type=block.block_type,
                        text=body,
                        content_checksum=checksum,
                        token_estimate=(len(body.encode()) + 3) // 4,
                        occurrences=(occurrence,),
                    )
        chunks.extend(document_chunks.values())
        if len(chunks) > MAX_TOTAL_CHUNKS:
            raise CorpusError("corpus_chunk_limit_exceeded")
        mappings.append(
            DocumentChunkMap(
                document_id=document.document_id,
                source_characters=len(text),
                source_lines=len(offsets),
                chunk_ids=tuple(document_chunks),
                omitted_blocks=tuple(
                    OmittedBlock(
                        location=source_location(document.source_ref, offsets, o.start, o.end),
                        reason=o.reason,
                    )
                    for o in omissions
                ),
                outcome="chunked" if document_chunks else "no_retrievable_content",
            )
        )
    payload = {
        "schema_version": "1.0",
        "lifecycle": "candidate",
        "content_trust": "untrusted_reference",
        "corpus_id": corpus.corpus_id,
        "corpus": corpus.model_dump(mode="json"),
        "chunker": config.model_dump(mode="json"),
        "chunker_config_id": config.config_id(),
        "documents": [mapping.model_dump(mode="json") for mapping in mappings],
        "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
    }
    payload["chunk_manifest_id"] = "chunks-sha256-" + canonical_sha256(payload)
    return ChunkManifest.model_validate_json(json.dumps(payload))
