"""Explicit, bounded Titan v2 calls. Credentials use the AWS default chain."""

import json
import math
from collections.abc import Callable
from threading import Lock
from typing import Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from retailops_ai.knowledge.indexes import EmbeddingConfig, float32


class BedrockClient(Protocol):
    def invoke_model(self, **kwargs: object) -> dict[str, object]: ...


class ResponseBody(Protocol):
    def read(self, amt: int) -> bytes: ...

    def close(self) -> None: ...


def bedrock_client(config: EmbeddingConfig, profile: str | None = None) -> BedrockClient:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    return cast(
        BedrockClient,
        boto3.Session(profile_name=profile).client(
            "bedrock-runtime",
            region_name=config.region,
            config=Config(
                connect_timeout=5,
                read_timeout=20,
                retries={"total_max_attempts": 1, "mode": "standard"},
            ),
        ),
    )


class BedrockEmbeddingProvider:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        max_requests: int,
        max_input_bytes: int,
        client: BedrockClient | None = None,
        profile: str | None = None,
        on_embedding: Callable[[str, tuple[float, ...]], None] | None = None,
    ) -> None:
        self.config = EmbeddingConfig.model_validate_json(config.model_dump_json())
        if self.config.provider != "bedrock":
            raise ValueError("bedrock_configuration_required")
        if not 1 <= max_requests <= 1000 or not 1 <= max_input_bytes <= 5_000_000:
            raise ValueError("embedding_budget_invalid")
        self.max_requests = max_requests
        self.max_input_bytes = max_input_bytes
        self.requests = 0
        self.input_bytes = 0
        self.input_tokens = 0
        self.client = client or bedrock_client(config, profile)
        self.on_embedding = on_embedding
        self.budget_lock = Lock()

    def embed(self, text: str) -> tuple[float, ...]:
        size = len(text.encode("utf-8"))
        # Conservative UTF-8 byte cap also bounds Titan's 8192-token input limit.
        if not text.strip() or size > 8000:
            raise ValueError("embedding_input_size_invalid")
        with self.budget_lock:
            if self.requests >= self.max_requests or self.input_bytes + size > self.max_input_bytes:
                raise ValueError("embedding_budget_exhausted")
            # Failed requests consume budget too; SDK retries are disabled.
            self.requests += 1
            self.input_bytes += size
        try:
            response = self.client.invoke_model(
                modelId=self.config.model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps(
                    {"inputText": text, "dimensions": self.config.dimension, "normalize": True}
                ),
            )
        except (BotoCoreError, ClientError):
            raise ValueError("embedding_provider_unavailable") from None
        body = cast(ResponseBody, response["body"])
        try:
            raw = body.read(100_001)
        finally:
            body.close()
        if len(raw) > 100_000:
            raise ValueError("embedding_response_too_large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("embedding_response_invalid")
        values, tokens = payload.get("embedding"), payload.get("inputTextTokenCount")
        if (
            not isinstance(values, list)
            or len(values) != self.config.dimension
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in values)
            or type(tokens) is not int
            or not 0 < tokens <= 8192
        ):
            raise ValueError("embedding_response_invalid")
        norm = math.sqrt(math.fsum(v * v for v in values))
        if not math.isfinite(norm) or not math.isclose(norm, 1.0, abs_tol=1e-4):
            raise ValueError("embedding_normalization_mismatch")
        vector = tuple(float32(v / norm) for v in values)
        with self.budget_lock:
            self.input_tokens += tokens
        if self.on_embedding is not None:
            self.on_embedding(text, vector)
        return vector
