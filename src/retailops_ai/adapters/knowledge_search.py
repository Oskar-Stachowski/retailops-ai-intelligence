"""Exact pgvector search with authorization and live denials before row output."""

import json
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.adapters.embeddings import FakeEmbeddingProvider
from retailops_ai.adapters.index_lifecycle import current_index
from retailops_ai.adapters.vector_store import STORE_LOCK_ID, _boundary
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.chunks import MarkdownChunk
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
        self, engine: Engine, environment: Literal["local", "test"], config: RetrievalConfig
    ) -> None:
        self.engine = engine
        self.environment = environment
        self.config = config

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
        vector = checked_vector(
            manifest.embedding_config,
            FakeEmbeddingProvider(manifest.embedding_config).embed(request.question),
        )
        parameters = {
            "id": manifest.index_id,
            "env": self.environment,
            "space": manifest.space_id,
            "dimension": manifest.embedding_config.dimension,
            "vector": "[" + ",".join(repr(v) for v in vector) + "]",
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
        }
        with self.engine.begin() as connection:
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            _boundary(connection)
            # Do not resolve current again: a pinned run may legitimately use an older version.
            bound = connection.scalar(
                text("""SELECT 1 FROM ai.rag_index_changes e
                JOIN ai.rag_qualifications q USING(index_id,environment,lane)
                WHERE e.request_id=:request AND e.generation=:generation AND e.index_id=:id
                AND e.environment=:env AND e.lane='offline_test'
                AND q.review_id=:review AND q.validation_id=:validation"""),
                parameters,
            )
            if bound != 1:
                raise ValueError("knowledge_pin_not_qualified")
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
        return result_from_ranked(manifest, request, self.config, ranked)


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
