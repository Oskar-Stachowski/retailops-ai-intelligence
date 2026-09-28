"""Offline provider interface; fake vectors test mechanics, not semantic quality."""

import hashlib
import math
from typing import Protocol

from retailops_ai.knowledge.indexes import EmbeddingConfig, float32


class EmbeddingProvider(Protocol):
    @property
    def config(self) -> EmbeddingConfig: ...

    def embed(self, text: str) -> tuple[float, ...]: ...


class FakeEmbeddingProvider:
    def __init__(self, config: EmbeddingConfig) -> None:
        if config.provider != "fake":
            raise ValueError("explicit_real_embedding_provider_required")
        self.config = config

    def embed(self, text: str) -> tuple[float, ...]:
        checksum = hashlib.sha256(text.encode()).hexdigest()
        values = []
        for ordinal in range(self.config.dimension):
            digest = hashlib.sha256(
                f"{self.config.space_id()}:{checksum}:{ordinal}".encode()
            ).digest()
            values.append((int.from_bytes(digest[:4], "big") + 0.5) / 2**31 - 1)
        norm = math.sqrt(math.fsum(value * value for value in values))
        return tuple(float32(value / norm) for value in values)
