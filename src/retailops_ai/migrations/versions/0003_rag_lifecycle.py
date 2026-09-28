"""Offline test qualification, immutable changes and atomic active pointers."""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_rag_lifecycle"
down_revision: str | None = "0002_rag_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ai.rag_indexes ADD CONSTRAINT rag_indexes_environment_unique UNIQUE(index_id, environment);
CREATE TABLE ai.rag_corpus_reviews (
    review_id text PRIMARY KEY,
    environment text NOT NULL CHECK(environment IN ('local','test')),
    corpus_id text NOT NULL,
    approval jsonb NOT NULL,
    UNIQUE(review_id, environment),
    CHECK ((approval->>'review_id'=review_id AND approval->>'environment'=environment
        AND approval->>'corpus_id'=corpus_id AND approval->>'decision'='approved') IS TRUE)
);
CREATE TABLE ai.rag_qualifications (
    index_id text NOT NULL,
    environment text NOT NULL CHECK(environment='test'),
    lane text NOT NULL CHECK(lane='offline_test'),
    review_id text NOT NULL,
    validation_id text NOT NULL,
    validation jsonb NOT NULL,
    PRIMARY KEY(index_id, environment, lane),
    FOREIGN KEY(index_id, environment) REFERENCES ai.rag_indexes(index_id, environment),
    FOREIGN KEY(review_id, environment) REFERENCES ai.rag_corpus_reviews(review_id, environment),
    CHECK ((validation->>'validation_id'=validation_id AND validation->>'index_id'=index_id
        AND validation->>'environment'=environment AND validation->>'result'='passed'
        AND validation->>'provider'='fake'
        AND validation->>'golden_evaluation'='not_evaluated_fake_vectors'
        AND validation->'checks'='{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": true}'::jsonb) IS TRUE)
);
CREATE FUNCTION ai.rag_qualification_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m jsonb; c jsonb; a jsonb;
BEGIN
    SELECT manifest, chunk_manifest->'corpus' INTO m,c FROM ai.rag_indexes WHERE index_id=NEW.index_id;
    SELECT approval INTO a FROM ai.rag_corpus_reviews WHERE review_id=NEW.review_id;
    IF (m->>'corpus_id'=a->>'corpus_id' AND c->>'corpus_config_id'=a->>'corpus_config_id'
        AND c->>'review_owner'=a->>'review_owner' AND m->>'corpus_id'=NEW.validation->>'corpus_id'
        AND m->>'space_id'=NEW.validation->>'space_id') IS NOT TRUE THEN
        RAISE EXCEPTION 'rag_qualification_binding_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER rag_qualification_binding BEFORE INSERT ON ai.rag_qualifications
    FOR EACH ROW EXECUTE FUNCTION ai.rag_qualification_binding();
CREATE TABLE ai.rag_index_changes (
    request_id text PRIMARY KEY,
    environment text NOT NULL,
    lane text NOT NULL,
    generation bigint NOT NULL CHECK(generation>0),
    previous_index_id text,
    index_id text NOT NULL,
    request jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(environment, lane, generation),
    UNIQUE(request_id, environment, lane, generation, index_id),
    FOREIGN KEY(index_id, environment, lane) REFERENCES ai.rag_qualifications(index_id, environment, lane),
    FOREIGN KEY(previous_index_id, environment, lane) REFERENCES ai.rag_qualifications(index_id, environment, lane),
    CHECK ((request->>'request_id'=request_id AND request->>'environment'=environment
        AND request->>'lane'=lane AND request->>'target_index_id'=index_id
        AND (request->>'expected_generation')::bigint=generation-1
        AND request->>'operation' IN ('activate','rollback')) IS TRUE)
);
CREATE TABLE ai.rag_active_indexes (
    environment text NOT NULL,
    lane text NOT NULL,
    generation bigint NOT NULL,
    request_id text NOT NULL,
    index_id text NOT NULL,
    PRIMARY KEY(environment, lane),
    FOREIGN KEY(request_id, environment, lane, generation, index_id)
        REFERENCES ai.rag_index_changes(request_id, environment, lane, generation, index_id)
);
CREATE FUNCTION ai.rag_pointer_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE event ai.rag_index_changes;
BEGIN
    SELECT * INTO event FROM ai.rag_index_changes WHERE request_id=NEW.request_id;
    IF TG_OP='INSERT' THEN
        IF NEW.generation<>1 OR event.previous_index_id IS NOT NULL OR event.request->>'operation'<>'activate' THEN
            RAISE EXCEPTION 'rag_initial_pointer_mismatch' USING ERRCODE='23514';
        END IF;
    ELSE
        IF NEW.environment<>OLD.environment OR NEW.lane<>OLD.lane
            OR NEW.generation<>OLD.generation+1 OR event.previous_index_id IS DISTINCT FROM OLD.index_id
            OR NEW.index_id=OLD.index_id THEN
            RAISE EXCEPTION 'rag_pointer_generation_mismatch' USING ERRCODE='23514';
        END IF;
    END IF;
    IF event.request->>'operation'='rollback' AND NOT EXISTS(
        SELECT 1 FROM ai.rag_index_changes WHERE environment=NEW.environment AND lane=NEW.lane
            AND index_id=NEW.index_id AND generation<NEW.generation) THEN
        RAISE EXCEPTION 'rag_rollback_history_missing' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER rag_pointer_transition BEFORE INSERT OR UPDATE ON ai.rag_active_indexes
    FOR EACH ROW EXECUTE FUNCTION ai.rag_pointer_transition();
CREATE TRIGGER rag_pointer_no_delete BEFORE DELETE ON ai.rag_active_indexes
    FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();
CREATE FUNCTION ai.rag_change_complete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM ai.rag_active_indexes WHERE request_id=NEW.request_id
        AND environment=NEW.environment AND lane=NEW.lane AND generation=NEW.generation AND index_id=NEW.index_id) THEN
        RAISE EXCEPTION 'rag_change_incomplete' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER rag_change_complete AFTER INSERT ON ai.rag_index_changes
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.rag_change_complete();
""")
    for table in ("rag_corpus_reviews", "rag_qualifications", "rag_index_changes"):
        op.execute(
            f"CREATE TRIGGER rag_immutable BEFORE UPDATE OR DELETE ON ai.{table} "
            "FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable()"
        )


def downgrade() -> None:
    for table in (
        "rag_active_indexes",
        "rag_index_changes",
        "rag_qualifications",
        "rag_corpus_reviews",
    ):
        op.execute(f"DROP TABLE ai.{table}")
    for function in ("rag_change_complete", "rag_pointer_transition", "rag_qualification_binding"):
        op.execute(f"DROP FUNCTION ai.{function}()")
    op.execute("ALTER TABLE ai.rag_indexes DROP CONSTRAINT rag_indexes_environment_unique")
