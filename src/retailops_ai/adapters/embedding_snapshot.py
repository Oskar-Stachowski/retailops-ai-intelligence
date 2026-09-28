"""Validated local embedding cache; missing values never cause network calls."""

import hashlib
from collections.abc import Iterable

from retailops_ai.knowledge.indexes import (
    EmbeddingConfig,
    EmbeddingRecord,
    embedding_id,
    vector_checksum,
)


def embedding_record(
    config: EmbeddingConfig, text: str, vector: tuple[float, ...]
) -> EmbeddingRecord:
    checksum = hashlib.sha256(text.encode()).hexdigest()
    return EmbeddingRecord(
        embedding_id=embedding_id(checksum, config.space_id()),
        space_id=config.space_id(),
        content_checksum=checksum,
        dimension=config.dimension,
        vector=vector,
        vector_checksum=vector_checksum(vector),
    )


class SnapshotEmbeddingProvider:
    def __init__(self, config: EmbeddingConfig, records: Iterable[EmbeddingRecord]) -> None:
        self.config = EmbeddingConfig.model_validate_json(config.model_dump_json())
        self.records: dict[str, EmbeddingRecord] = {}
        for value in records:
            record = EmbeddingRecord.model_validate_json(value.model_dump_json())
            if record.space_id != config.space_id() or record.dimension != config.dimension:
                raise ValueError("embedding_snapshot_space_mismatch")
            if record.content_checksum in self.records:
                raise ValueError("embedding_snapshot_duplicate")
            self.records[record.content_checksum] = record

    def embed(self, text: str) -> tuple[float, ...]:
        checksum = hashlib.sha256(text.encode()).hexdigest()
        record = self.records.get(checksum)
        if record is None:
            raise ValueError("embedding_snapshot_missing")
        return record.vector
