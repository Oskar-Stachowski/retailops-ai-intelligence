"""Private, content-bound cache shared by bounded preparation and offline replay."""

import hashlib
from pathlib import Path

from retailops_ai.adapters.embedding_snapshot import embedding_record
from retailops_ai.adapters.embeddings import EmbeddingProvider
from retailops_ai.knowledge.indexes import EmbeddingConfig, EmbeddingRecord, embedding_id
from retailops_ai.pipelines.corpus import write_candidate


class CachedEmbeddingProvider:
    def __init__(
        self, config: EmbeddingConfig, directory: Path, upstream: EmbeddingProvider | None
    ) -> None:
        if upstream is not None and upstream.config != config:
            raise ValueError("embedding_provider_config_mismatch")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_mode & 0o077:
            raise ValueError("embedding_cache_permissions_invalid")
        self.config, self.directory, self.upstream = config, directory, upstream

    def embed(self, text: str) -> tuple[float, ...]:
        checksum = hashlib.sha256(text.encode()).hexdigest()
        key = embedding_id(checksum, self.config.space_id())
        path = self.directory / (key + ".json")
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise ValueError("embedding_cache_permissions_invalid")
            with path.open("rb") as source:
                raw = source.read(100_001)
            if len(raw) > 100_000:
                raise ValueError("embedding_cache_size_invalid")
            record = EmbeddingRecord.model_validate_json(raw)
            if (
                record.embedding_id != key
                or record.space_id != self.config.space_id()
                or record.dimension != self.config.dimension
            ):
                raise ValueError("embedding_cache_binding_mismatch")
            return record.vector
        if self.upstream is None:
            raise ValueError("embedding_cache_miss_offline")
        record = embedding_record(self.config, text, self.upstream.embed(text))
        write_candidate(record, path)
        return record.vector
