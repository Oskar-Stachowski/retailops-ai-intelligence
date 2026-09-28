"""Immutable, complete candidate indexes in the isolated AI database."""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_rag_candidates"
down_revision: str | None = "0001_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.rag_embedding_spaces (
    space_id text PRIMARY KEY,
    dimension integer NOT NULL CHECK (dimension IN (8,16,32,64)),
    config jsonb NOT NULL,
    UNIQUE (space_id, dimension),
    CHECK (((config->>'dimension')::integer = dimension) IS TRUE),
    CHECK ((config->>'provider' = 'fake' AND config->>'region' = 'offline') IS TRUE)
);
CREATE TABLE ai.rag_embeddings (
    environment text NOT NULL CHECK (environment IN ('local','test')),
    embedding_id text NOT NULL,
    space_id text NOT NULL,
    content_checksum text NOT NULL CHECK (content_checksum ~ '^[0-9a-f]{64}$'),
    vector_checksum text NOT NULL CHECK (vector_checksum ~ '^[0-9a-f]{64}$'),
    dimension integer NOT NULL,
    embedding vector NOT NULL,
    PRIMARY KEY (environment, embedding_id),
    UNIQUE (environment, embedding_id, space_id, dimension),
    FOREIGN KEY (space_id, dimension) REFERENCES ai.rag_embedding_spaces(space_id, dimension),
    CHECK (vector_dims(embedding) = dimension),
    CHECK (abs(vector_norm(embedding) - 1) < 0.000001),
    CHECK (encode(sha256(substring(vector_send(embedding) FROM 5)), 'hex') = vector_checksum)
);
CREATE TABLE ai.rag_indexes (
    index_id text PRIMARY KEY,
    environment text NOT NULL CHECK (environment IN ('local','test')),
    space_id text NOT NULL,
    dimension integer NOT NULL,
    chunk_count integer NOT NULL CHECK (chunk_count BETWEEN 1 AND 32768),
    manifest jsonb NOT NULL,
    chunk_manifest jsonb NOT NULL,
    UNIQUE (index_id, environment, space_id, dimension),
    FOREIGN KEY (space_id, dimension) REFERENCES ai.rag_embedding_spaces(space_id, dimension),
    CHECK ((manifest->>'index_id' = index_id AND manifest->>'environment' = environment
        AND manifest->>'lifecycle' = 'candidate' AND manifest->>'space_id' = space_id
        AND (manifest->'embedding_config'->>'dimension')::integer = dimension
        AND (manifest->>'chunk_count')::integer = chunk_count
        AND manifest->>'chunk_manifest_id' = chunk_manifest->>'chunk_manifest_id'
        AND manifest->>'corpus_id' = chunk_manifest->>'corpus_id'
        AND jsonb_array_length(manifest->'entries') = chunk_count
        AND jsonb_array_length(chunk_manifest->'chunks') = chunk_count) IS TRUE)
);
CREATE TABLE ai.rag_index_chunks (
    index_id text NOT NULL,
    environment text NOT NULL,
    space_id text NOT NULL,
    dimension integer NOT NULL,
    chunk_id text NOT NULL,
    embedding_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    metadata jsonb NOT NULL,
    PRIMARY KEY (index_id, chunk_id),
    UNIQUE (index_id, ordinal),
    FOREIGN KEY (index_id, environment, space_id, dimension)
        REFERENCES ai.rag_indexes(index_id, environment, space_id, dimension),
    FOREIGN KEY (environment, embedding_id, space_id, dimension)
        REFERENCES ai.rag_embeddings(environment, embedding_id, space_id, dimension)
);
CREATE FUNCTION ai.rag_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'rag_candidate_is_immutable' USING ERRCODE = '23514';
END;
$$;
CREATE FUNCTION ai.rag_chunk_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_entry jsonb; expected_chunk jsonb; expected_checksum text; actual_checksum text;
BEGIN
    SELECT manifest->'entries'->NEW.ordinal, chunk_manifest->'chunks'->NEW.ordinal
      INTO expected_entry, expected_chunk FROM ai.rag_indexes WHERE index_id = NEW.index_id;
    SELECT vector_checksum, content_checksum INTO expected_checksum, actual_checksum
      FROM ai.rag_embeddings WHERE environment = NEW.environment AND embedding_id = NEW.embedding_id;
    IF (expected_entry->>'chunk_id' = NEW.chunk_id
        AND expected_entry->>'embedding_id' = NEW.embedding_id
        AND expected_entry->>'vector_checksum' = expected_checksum
        AND expected_chunk = NEW.metadata AND expected_chunk->>'chunk_id' = NEW.chunk_id
        AND expected_chunk->>'content_checksum' = actual_checksum) IS NOT TRUE THEN
        RAISE EXCEPTION 'rag_chunk_binding_mismatch' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER rag_chunk_binding BEFORE INSERT ON ai.rag_index_chunks
    FOR EACH ROW EXECUTE FUNCTION ai.rag_chunk_binding();
CREATE FUNCTION ai.rag_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT count(*) FROM ai.rag_index_chunks WHERE index_id = NEW.index_id) <> NEW.chunk_count THEN
        RAISE EXCEPTION 'rag_candidate_incomplete' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER rag_complete AFTER INSERT ON ai.rag_indexes
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.rag_complete();
""")
    for table in ("rag_embedding_spaces", "rag_embeddings", "rag_indexes", "rag_index_chunks"):
        op.execute(
            f"CREATE TRIGGER rag_immutable BEFORE UPDATE OR DELETE ON ai.{table} "
            "FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable()"
        )


def downgrade() -> None:
    # Explicit Alembic downgrade is destructive; ordinary candidate writes cannot delete.
    for table in ("rag_index_chunks", "rag_indexes", "rag_embeddings", "rag_embedding_spaces"):
        op.execute(f"DROP TABLE ai.{table}")
    for function in ("rag_complete", "rag_chunk_binding", "rag_immutable"):
        op.execute(f"DROP FUNCTION ai.{function}()")
