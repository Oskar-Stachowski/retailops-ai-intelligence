"""Exact pgvector search with authorization and live denials before row output."""

import json
from collections.abc import Callable
from threading import Lock
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.adapters.embeddings import EmbeddingProvider, FakeEmbeddingProvider
from retailops_ai.adapters.index_lifecycle import current_index
from retailops_ai.adapters.vector_store import STORE_LOCK_ID, _boundary
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.chunks import MarkdownChunk
from retailops_ai.knowledge.indexes import EmbeddingConfig
from retailops_ai.knowledge.releases import IndexPin, Lane
from retailops_ai.knowledge.retrieval import (
    DocumentDenial,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalResult,
)
from retailops_ai.pipelines.retrieval import (
    allowed,
    checked_vector,
    resolve_scope,
    result_from_ranked,
)


class KnowledgeBackend(Protocol):
    def search(self, request: RetrievalRequest, principal: Principal) -> RetrievalResult: ...


class PostgresKnowledge:
    def __init__(
        self,
        engine: Engine,
        environment: Literal["local", "test"],
        config: RetrievalConfig,
        *,
        allow_bedrock: bool = False,
        provider_factory: Callable[[EmbeddingConfig], EmbeddingProvider] | None = None,
    ) -> None:
        self.engine = engine
        self.environment = environment
        self.config = config
        self.allow_bedrock = allow_bedrock
        self.provider_factory = provider_factory
        self.providers: dict[str, EmbeddingProvider] = {}
        self.provider_lock = Lock()

    def _provider(self, config: EmbeddingConfig) -> EmbeddingProvider:
        if config.provider == "fake":
            return FakeEmbeddingProvider(config)
        with self.provider_lock:
            provider = self.providers.get(config.space_id())
            if provider is None:
                if self.provider_factory is not None:
                    provider = self.provider_factory(config)
                elif self.allow_bedrock:
                    from retailops_ai.adapters.bedrock_embeddings import BedrockEmbeddingProvider

                    provider = BedrockEmbeddingProvider(
                        config, max_requests=1000, max_input_bytes=5_000_000
                    )
                else:
                    raise ValueError("bedrock_queries_disabled")
                if provider.config != config:
                    raise ValueError("embedding_provider_config_mismatch")
                self.providers[config.space_id()] = provider
        return provider

    def search(self, request: RetrievalRequest, principal: Principal) -> RetrievalResult:
        resolve_scope(principal, request, self.environment)
        lane: Lane = "offline_test" if self.environment == "test" else "retrieval"
        pin = current_index(self.engine, self.environment, lane)
        if pin is None:
            raise ValueError("knowledge_index_not_configured")
        return self.search_pinned(pin, request, principal)

    def search_pinned(
        self, pin: IndexPin, request: RetrievalRequest, principal: Principal
    ) -> RetrievalResult:
        request = RetrievalRequest.model_validate_json(request.model_dump_json())
        pin = IndexPin.model_validate_json(pin.model_dump_json())
        if pin.environment != self.environment:
            raise ValueError("knowledge_environment_mismatch")
        scope = resolve_scope(principal, request, self.environment)
        manifest = pin.manifest
        config = self.config
        parameters: dict[str, object] = {
            "id": manifest.index_id,
            "env": self.environment,
            "space": manifest.space_id,
            "dimension": manifest.embedding_config.dimension,
            "repos": json.dumps(sorted(scope.repositories)),
            "types": json.dumps(sorted(scope.document_types)),
            "statuses": json.dumps(sorted(scope.document_statuses)),
            "classes": json.dumps(sorted(scope.access_classes)),
            "per_document": self.config.max_chunks_per_document,
            "pool": self.config.candidate_limit,
            "request": pin.request_id,
            "generation": pin.generation,
            "review": pin.review_id,
            "validation": pin.validation_id,
            "lane": pin.lane,
            "manifest": manifest.model_dump_json(),
        }
        with self.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            _boundary(connection)
            # Do not resolve current again: a pinned run may legitimately use an older version.
            bound = connection.execute(
                text("""SELECT r.release->'retrieval_config' AS config FROM ai.rag_index_changes e
                JOIN ai.rag_qualifications q USING(index_id,environment,lane)
                JOIN ai.rag_indexes i ON i.index_id=e.index_id
                LEFT JOIN ai.rag_semantic_releases r ON r.release_id=q.release_id
                WHERE e.request_id=:request AND e.generation=:generation AND e.index_id=:id
                AND e.environment=:env AND e.lane=:lane AND i.manifest=CAST(:manifest AS jsonb)
                AND q.review_id=:review AND q.validation_id=:validation"""),
                parameters,
            ).first()
            if bound is None:
                raise ValueError("knowledge_pin_not_qualified")
            if pin.lane == "retrieval":
                config = RetrievalConfig.model_validate_json(json.dumps(bound.config))
        # Qualification precedes network use; SQL below checks current document denials.
        vector = checked_vector(
            manifest.embedding_config,
            self._provider(manifest.embedding_config).embed(request.question),
        )
        parameters.update(
            vector="[" + ",".join(repr(v) for v in vector) + "]",
            per_document=config.max_chunks_per_document,
            pool=config.candidate_limit,
        )
        with self.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            _boundary(connection)
            rows = connection.execute(
                text("""WITH eligible AS (
                SELECT c.metadata,c.chunk_id,
                    (e.embedding <=> CAST(:vector AS vector)) AS distance,
                    row_number() OVER (PARTITION BY c.metadata->>'document_id'
                        ORDER BY e.embedding <=> CAST(:vector AS vector),c.chunk_id) AS document_rank
                FROM ai.rag_index_chunks c JOIN ai.rag_embeddings e
                    ON c.environment=e.environment AND c.embedding_id=e.embedding_id
                WHERE c.index_id=:id AND c.environment=:env AND c.space_id=:space
                    AND c.dimension=:dimension AND e.dimension=:dimension AND e.space_id=:space
                    AND CAST(:repos AS jsonb) ? (c.metadata->>'repository')
                    AND CAST(:types AS jsonb) ? (c.metadata->>'document_type')
                    AND CAST(:statuses AS jsonb) ? (c.metadata->>'document_status')
                    AND CAST(:classes AS jsonb) ? (c.metadata->>'access_class')
                    AND NOT EXISTS (SELECT 1 FROM ai.rag_document_denials d
                        WHERE d.environment=c.environment AND d.document_id=c.metadata->>'document_id')
            ) SELECT metadata,1-distance AS score FROM eligible
                WHERE document_rank<=:per_document
                ORDER BY distance,chunk_id LIMIT :pool"""),
                parameters,
            ).all()
        ranked = []
        entries = {e.chunk_id: e for e in manifest.entries}
        for row in rows:
            chunk = MarkdownChunk.model_validate_json(json.dumps(row.metadata))
            if chunk.chunk_id not in entries or not allowed(chunk, scope, frozenset()):
                raise ValueError("knowledge_row_binding_mismatch")
            ranked.append((chunk, float(row.score)))
        return result_from_ranked(manifest, request, config, ranked)


def deny_document(engine: Engine, denial: DocumentDenial) -> bool:
    denial = DocumentDenial.model_validate_json(denial.model_dump_json())
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='5s'"))
        connection.execute(text("SET LOCAL lock_timeout='3s'"))
        _boundary(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": STORE_LOCK_ID})
        parameters = {
            "env": denial.environment,
            "document": denial.document_id,
            "payload": denial.model_dump_json(),
        }
        existing = connection.scalar(
            text(
                "SELECT denial FROM ai.rag_document_denials WHERE environment=:env AND document_id=:document"
            ),
            parameters,
        )
        if existing is not None:
            if existing != denial.model_dump(mode="json"):
                raise ValueError("document_denial_replay_mismatch")
            return False
        connection.execute(
            text(
                "INSERT INTO ai.rag_document_denials(environment,document_id,denial) VALUES (:env,:document,CAST(:payload AS jsonb))"
            ),
            parameters,
        )
    return True
