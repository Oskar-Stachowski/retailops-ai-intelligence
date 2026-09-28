"""Build closed, reproducible index candidates; never activate or call AWS."""

import json
from collections.abc import Mapping
from pathlib import Path

from retailops_ai.adapters.embeddings import EmbeddingProvider, FakeEmbeddingProvider
from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.registry import _invalid_constant, _pairs
from retailops_ai.knowledge.chunks import ChunkManifest
from retailops_ai.knowledge.indexes import (
    MAX_INDEX_BYTES,
    EmbeddingConfig,
    EmbeddingRecord,
    IndexCandidate,
    embedding_id,
    vector_checksum,
)
from retailops_ai.pipelines.corpus import decode_json


def load_embedding_config(path: Path) -> EmbeddingConfig:
    with path.open("rb") as source:
        raw = source.read(64001)
    if len(raw) > 64000:
        raise CorpusError("embedding_config_too_large")
    decode_json(raw)
    return EmbeddingConfig.model_validate_json(raw)


def load_index_candidate(path: Path) -> IndexCandidate:
    with path.open("rb") as source:
        raw = source.read(MAX_INDEX_BYTES + 1)
    return validate_index_bytes(raw)


def validate_index_bytes(raw: bytes) -> IndexCandidate:
    if len(raw) > MAX_INDEX_BYTES:
        raise CorpusError("index_candidate_too_large")
    json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
    return IndexCandidate.model_validate_json(raw)


def build_index(
    chunks: ChunkManifest,
    config: EmbeddingConfig,
    *,
    provider: EmbeddingProvider | None = None,
    cache: Mapping[str, EmbeddingRecord] | None = None,
) -> IndexCandidate:
    chunks = ChunkManifest.model_validate_json(chunks.model_dump_json())
    config = EmbeddingConfig.model_validate_json(config.model_dump_json())
    if chunks.corpus.environment not in {"local", "test"}:
        raise CorpusError("fake_index_environment_forbidden")
    if not chunks.chunks:
        raise CorpusError("empty_index_forbidden")
    provider = provider or FakeEmbeddingProvider(config)
    if provider.config != config:
        raise CorpusError("embedding_provider_config_mismatch")
    records: dict[str, EmbeddingRecord] = {}
    entries = []
    for chunk in chunks.chunks:
        key = embedding_id(chunk.content_checksum, config.space_id())
        if key not in records:
            cached = cache.get(key) if cache is not None else None
            if cached is not None:
                record = EmbeddingRecord.model_validate_json(cached.model_dump_json())
                if record.embedding_id != key or record.dimension != config.dimension:
                    raise CorpusError("embedding_cache_binding_mismatch")
            else:
                vector = provider.embed(chunk.text)
                if len(vector) != config.dimension:
                    raise CorpusError("embedding_dimension_mismatch")
                record = EmbeddingRecord(
                    embedding_id=key,
                    space_id=config.space_id(),
                    content_checksum=chunk.content_checksum,
                    dimension=config.dimension,
                    vector=vector,
                    vector_checksum=vector_checksum(vector),
                )
            records[key] = record
        entries.append(
            {
                "chunk_id": chunk.chunk_id,
                "embedding_id": key,
                "vector_checksum": records[key].vector_checksum,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "lifecycle": "candidate",
        "environment": chunks.corpus.environment,
        "semantic_quality": "not_evaluated_fake_vectors",
        "corpus_id": chunks.corpus_id,
        "chunk_manifest_id": chunks.chunk_manifest_id,
        "embedding_config": config.model_dump(mode="json"),
        "space_id": config.space_id(),
        "storage_version": "pgvector-checked-dimension-v1",
        "pgvector_version": "0.8.6",
        "migration_revision": "0002_rag_candidates",
        "retrieval_version": "not_implemented",
        "chunk_count": len(entries),
        "embedding_count": len(records),
        "entries": entries,
    }
    manifest["index_id"] = "index-sha256-" + canonical_sha256(manifest)
    candidate = IndexCandidate.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "manifest": manifest,
                "chunks": chunks.model_dump(mode="json"),
                "embeddings": [records[key].model_dump(mode="json") for key in sorted(records)],
            }
        )
    )
    if len(candidate.model_dump_json().encode()) > MAX_INDEX_BYTES:
        raise CorpusError("index_candidate_too_large")
    return candidate
