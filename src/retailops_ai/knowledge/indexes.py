"""Pinned fake embedding space and complete candidate index contracts."""

import hashlib
import math
import struct
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import MAX_TOTAL_CHUNKS, ChunkID, ChunkManifest, ChunkManifestID
from retailops_ai.knowledge.contracts import CorpusID

SpaceID = Annotated[str, Field(pattern=r"^embedding-space-sha256-[0-9a-f]{64}$")]
EmbeddingID = Annotated[str, Field(pattern=r"^embedding-sha256-[0-9a-f]{64}$")]
IndexID = Annotated[str, Field(pattern=r"^index-sha256-[0-9a-f]{64}$")]
Dimension = Literal[8, 16, 32, 64]
MAX_INDEX_BYTES = 64_000_000


class EmbeddingConfig(Contract):
    schema_version: Literal["1.0"]
    provider: Literal["fake"]
    model_id: Literal["sha256-unit-f32-v1"]
    inference_profile: None
    region: Literal["offline"]
    dimension: Dimension
    normalization: Literal["l2-unit"]
    distance: Literal["cosine"]
    transformation_version: Literal["utf8-chunk-body-v1"]
    vector_format: Literal["float32-big-endian-v1"]

    def space_id(self) -> str:
        return "embedding-space-sha256-" + canonical_sha256(self.model_dump(mode="json"))


def float32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]  # type: ignore[no-any-return]


def vector_checksum(vector: tuple[float, ...]) -> str:
    return hashlib.sha256(struct.pack(f">{len(vector)}f", *vector)).hexdigest()


def embedding_id(checksum: str, space: str) -> str:
    return "embedding-sha256-" + canonical_sha256({"content_checksum": checksum, "space_id": space})


class EmbeddingRecord(Contract):
    embedding_id: EmbeddingID
    space_id: SpaceID
    content_checksum: Sha256
    dimension: Dimension
    vector: Annotated[tuple[float, ...], Field(min_length=8, max_length=64)]
    vector_checksum: Sha256

    @model_validator(mode="after")
    def vector_binding(self) -> Self:
        if len(self.vector) != self.dimension:
            raise ValueError("embedding_dimension_mismatch")
        if any(not math.isfinite(v) or abs(v) > 1 or float32(v) != v for v in self.vector):
            raise ValueError("finite_float32_unit_vector_required")
        if not math.isclose(math.sqrt(math.fsum(v * v for v in self.vector)), 1, abs_tol=1e-6):
            raise ValueError("embedding_normalization_mismatch")
        if self.vector_checksum != vector_checksum(self.vector):
            raise ValueError("embedding_checksum_mismatch")
        if self.embedding_id != embedding_id(self.content_checksum, self.space_id):
            raise ValueError("embedding_identity_mismatch")
        return self


class IndexEntry(Contract):
    chunk_id: ChunkID
    embedding_id: EmbeddingID
    vector_checksum: Sha256


class IndexManifest(Contract):
    schema_version: Literal["1.0"]
    lifecycle: Literal["candidate"]
    environment: Literal["local", "test"]
    semantic_quality: Literal["not_evaluated_fake_vectors"]
    index_id: IndexID
    corpus_id: CorpusID
    chunk_manifest_id: ChunkManifestID
    embedding_config: EmbeddingConfig
    space_id: SpaceID
    storage_version: Literal["pgvector-checked-dimension-v1"]
    pgvector_version: Literal["0.8.6"]
    migration_revision: Literal["0002_rag_candidates"]
    retrieval_version: Literal["not_implemented"]
    chunk_count: Annotated[int, Field(ge=1, le=MAX_TOTAL_CHUNKS)]
    embedding_count: Annotated[int, Field(ge=1, le=MAX_TOTAL_CHUNKS)]
    entries: Annotated[tuple[IndexEntry, ...], Field(min_length=1, max_length=MAX_TOTAL_CHUNKS)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.index_id != "index-sha256-" + canonical_sha256(self.identity_data()):
            raise ValueError("index_identity_mismatch")
        if self.space_id != self.embedding_config.space_id():
            raise ValueError("index_space_identity_mismatch")
        if self.chunk_count != len(self.entries) or len({e.chunk_id for e in self.entries}) != len(
            self.entries
        ):
            raise ValueError("index_chunk_count_mismatch")
        if self.embedding_count != len({e.embedding_id for e in self.entries}):
            raise ValueError("index_embedding_count_mismatch")
        return self

    def identity_data(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"index_id"})


class IndexCandidate(Contract):
    schema_version: Literal["1.0"]
    manifest: IndexManifest
    chunks: ChunkManifest
    embeddings: Annotated[
        tuple[EmbeddingRecord, ...], Field(min_length=1, max_length=MAX_TOTAL_CHUNKS)
    ]

    @model_validator(mode="after")
    def closed_graph(self) -> Self:
        manifest = self.manifest
        if (
            manifest.chunk_manifest_id != self.chunks.chunk_manifest_id
            or manifest.corpus_id != self.chunks.corpus_id
            or manifest.environment != self.chunks.corpus.environment
            or [e.chunk_id for e in manifest.entries] != [c.chunk_id for c in self.chunks.chunks]
        ):
            raise ValueError("index_source_binding_mismatch")
        ids = [e.embedding_id for e in self.embeddings]
        if ids != sorted(set(ids)) or set(ids) != {e.embedding_id for e in manifest.entries}:
            raise ValueError("index_embedding_coverage_mismatch")
        records = {e.embedding_id: e for e in self.embeddings}
        for entry, chunk in zip(manifest.entries, self.chunks.chunks, strict=True):
            record = records[entry.embedding_id]
            if (
                record.space_id != manifest.space_id
                or record.dimension != manifest.embedding_config.dimension
                or record.content_checksum != chunk.content_checksum
                or record.vector_checksum != entry.vector_checksum
            ):
                raise ValueError("index_embedding_binding_mismatch")
        return self
