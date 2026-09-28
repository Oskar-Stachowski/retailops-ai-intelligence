"""Transactional, immutable candidate storage. No active pointer or retrieval API."""

import json
import struct

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.config import Settings
from retailops_ai.knowledge.indexes import IndexCandidate

STORE_LOCK_ID = 384790012


def index_engine(settings: Settings) -> Engine:
    if settings.database_url is None:
        raise CorpusError("index_database_required")
    url = make_url(settings.database_url.get_secret_value())
    if url.username != "ai_app" or url.database != "retailops_ai":
        raise CorpusError("isolated_ai_database_required")
    return create_engine(
        url, connect_args={"connect_timeout": 3}, pool_size=1, max_overflow=0, pool_timeout=3
    )


def _boundary(connection: Connection) -> None:
    identity = connection.execute(text("SELECT current_database(), current_user")).one()
    if tuple(identity) != ("retailops_ai", "ai_app"):
        raise CorpusError("isolated_ai_database_required")
    if connection.scalar(text("SELECT version_num FROM ai.alembic_version")) != EXPECTED_REVISION:
        raise CorpusError("index_schema_mismatch")
    if (
        connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        != "0.8.6"
    ):
        raise CorpusError("index_pgvector_version_mismatch")


def read_candidate(connection: Connection, index_id: str) -> IndexCandidate | None:
    row = connection.execute(
        text("SELECT manifest, chunk_manifest FROM ai.rag_indexes WHERE index_id=:id"),
        {"id": index_id},
    ).first()
    if row is None:
        return None
    records = (
        connection.execute(
            text("""SELECT DISTINCT e.embedding_id, e.space_id, e.content_checksum, e.dimension,
                    substring(vector_send(e.embedding) FROM 5) AS vector_bytes, e.vector_checksum
                FROM ai.rag_embeddings e JOIN ai.rag_index_chunks c
                  ON c.environment=e.environment AND c.embedding_id=e.embedding_id
                WHERE c.index_id=:id ORDER BY e.embedding_id"""),
            {"id": index_id},
        )
        .mappings()
        .all()
    )
    candidate = IndexCandidate.model_validate_json(
        json.dumps(
            {
                "schema_version": "1.0",
                "manifest": row.manifest,
                "chunks": row.chunk_manifest,
                "embeddings": [
                    {
                        **{k: v for k, v in record.items() if k != "vector_bytes"},
                        "vector": struct.unpack(f">{record['dimension']}f", record["vector_bytes"]),
                    }
                    for record in records
                ],
            }
        )
    )
    stored_chunks: list[object] = list(
        connection.execute(
            text("SELECT metadata FROM ai.rag_index_chunks WHERE index_id=:id ORDER BY ordinal"),
            {"id": index_id},
        )
        .scalars()
        .all()
    )
    if stored_chunks != [c.model_dump(mode="json") for c in candidate.chunks.chunks]:
        raise CorpusError("stored_index_binding_mismatch")
    return candidate


def _insert_chunks(connection: Connection, candidate: IndexCandidate) -> None:
    manifest = candidate.manifest
    connection.execute(
        text("""INSERT INTO ai.rag_index_chunks
          (index_id, environment, space_id, dimension, chunk_id, embedding_id, ordinal, metadata)
          VALUES (:index, :environment, :space, :dimension, :chunk, :embedding, :ordinal, CAST(:metadata AS jsonb))"""),
        [
            {
                "index": manifest.index_id,
                "environment": manifest.environment,
                "space": manifest.space_id,
                "dimension": manifest.embedding_config.dimension,
                "chunk": chunk.chunk_id,
                "embedding": entry.embedding_id,
                "ordinal": ordinal,
                "metadata": chunk.model_dump_json(),
            }
            for ordinal, (entry, chunk) in enumerate(
                zip(manifest.entries, candidate.chunks.chunks, strict=True)
            )
        ],
    )


def store_candidate(engine: Engine, candidate: IndexCandidate) -> bool:
    """Return True for a new candidate, False for an identical replay; otherwise roll back."""
    candidate = IndexCandidate.model_validate_json(candidate.model_dump_json())
    manifest = candidate.manifest
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='15s'"))
        connection.execute(text("SET LOCAL lock_timeout='3s'"))
        _boundary(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": STORE_LOCK_ID})
        existing = read_candidate(connection, manifest.index_id)
        if existing is not None:
            if existing != candidate:
                raise CorpusError("index_replay_mismatch")
            return False
        config = manifest.embedding_config.model_dump(mode="json")
        connection.execute(
            text("""INSERT INTO ai.rag_embedding_spaces(space_id, dimension, config)
                VALUES (:space, :dimension, CAST(:config AS jsonb)) ON CONFLICT DO NOTHING"""),
            {
                "space": manifest.space_id,
                "dimension": manifest.embedding_config.dimension,
                "config": json.dumps(config),
            },
        )
        stored_config = connection.scalar(
            text("SELECT config FROM ai.rag_embedding_spaces WHERE space_id=:space"),
            {"space": manifest.space_id},
        )
        if stored_config != config:
            raise CorpusError("embedding_space_collision")
        for record in candidate.embeddings:
            parameters = record.model_dump(mode="json", exclude={"vector"})
            parameters["environment"] = manifest.environment
            parameters["vector"] = "[" + ",".join(repr(v) for v in record.vector) + "]"
            connection.execute(
                text("""INSERT INTO ai.rag_embeddings
                    (environment, embedding_id, space_id, content_checksum, dimension, vector_checksum, embedding)
                    VALUES (:environment, :embedding_id, :space_id, :content_checksum, :dimension,
                            :vector_checksum, CAST(:vector AS vector)) ON CONFLICT DO NOTHING"""),
                parameters,
            )
            stored = connection.execute(
                text("""SELECT space_id, content_checksum, dimension, vector_checksum,
                        substring(vector_send(embedding) FROM 5)
                    FROM ai.rag_embeddings WHERE environment=:environment AND embedding_id=:embedding_id"""),
                parameters,
            ).one()
            if tuple(stored) != (
                record.space_id,
                record.content_checksum,
                record.dimension,
                record.vector_checksum,
                struct.pack(f">{record.dimension}f", *record.vector),
            ):
                raise CorpusError("embedding_cache_collision")
        connection.execute(
            text("""INSERT INTO ai.rag_indexes
                (index_id, environment, space_id, dimension, chunk_count, manifest, chunk_manifest)
                VALUES (:id, :environment, :space, :dimension, :count, CAST(:manifest AS jsonb), CAST(:chunks AS jsonb))"""),
            {
                "id": manifest.index_id,
                "environment": manifest.environment,
                "space": manifest.space_id,
                "dimension": manifest.embedding_config.dimension,
                "count": manifest.chunk_count,
                "manifest": manifest.model_dump_json(),
                "chunks": candidate.chunks.model_dump_json(),
            },
        )
        _insert_chunks(connection, candidate)
        if read_candidate(connection, manifest.index_id) != candidate:
            raise CorpusError("stored_index_round_trip_mismatch")
        # Deferred completeness constraint runs at COMMIT, inside this transaction context.
    return True
