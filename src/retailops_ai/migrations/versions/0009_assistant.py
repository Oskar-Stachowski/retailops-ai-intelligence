"""Assistant-owned outcomes, review candidates and bounded shared admission."""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_assistant"
down_revision: str | None = "0008_rag_semantic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE ai.assistant_admission_policy (
        environment text PRIMARY KEY CHECK(environment IN ('local','test')),
        policy jsonb NOT NULL
    );
    CREATE TABLE ai.assistant_runs (
        trace_id uuid PRIMARY KEY,
        correlation_id uuid NOT NULL,
        environment text NOT NULL REFERENCES ai.assistant_admission_policy(environment),
        owner_id text NOT NULL,
        scope jsonb NOT NULL,
        access_context jsonb NOT NULL,
        request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
        claim uuid,
        requested_at timestamptz NOT NULL,
        lease_until timestamptz NOT NULL,
        retain_until timestamptz NOT NULL,
        reserved_tokens integer NOT NULL CHECK(reserved_tokens BETWEEN 1 AND 13500),
        reserved_cost numeric(18,9) NOT NULL CHECK(reserved_cost > 0),
        status text NOT NULL CHECK(status IN ('running','succeeded','failed')),
        record jsonb NOT NULL,
        CHECK(lease_until > requested_at AND lease_until <= requested_at + interval '45 seconds'),
        CHECK(retain_until >= requested_at + interval '300 seconds' AND retain_until <= requested_at + interval '900 seconds'),
        CHECK((status='running')=(claim IS NOT NULL)),
        CHECK((record->>'trace_id'=trace_id::text AND record->>'status'=status) IS TRUE),
        CHECK(((status='running' AND record->'completed_at'='null'::jsonb AND record->'error_code'='null'::jsonb)
            OR (status='failed' AND record->'answer_id'='null'::jsonb AND record->'outcome'='null'::jsonb
                AND jsonb_typeof(record->'error_code')='string' AND jsonb_typeof(record->'completed_at')='string')
            OR (status='succeeded' AND jsonb_typeof(record->'answer_id')='string'
                AND record->>'outcome' IN ('answered','insufficient_evidence','refused')
                AND record->'error_code'='null'::jsonb AND jsonb_typeof(record->'completed_at')='string')) IS TRUE)
    );
    CREATE INDEX assistant_admission ON ai.assistant_runs(environment,owner_id,requested_at);
    CREATE TABLE ai.assistant_answers (
        answer_id uuid PRIMARY KEY,
        trace_id uuid NOT NULL UNIQUE REFERENCES ai.assistant_runs(trace_id) ON DELETE CASCADE,
        record jsonb NOT NULL,
        UNIQUE(answer_id,trace_id),
        CHECK((record->>'answer_id'=answer_id::text AND record->>'trace_id'=trace_id::text) IS TRUE)
    );
    CREATE TABLE ai.assistant_suggestions (
        recommendation_id uuid PRIMARY KEY,
        answer_id uuid NOT NULL,
        trace_id uuid NOT NULL,
        expires_at timestamptz NOT NULL,
        record jsonb NOT NULL,
        FOREIGN KEY(answer_id,trace_id) REFERENCES ai.assistant_answers(answer_id,trace_id) ON DELETE CASCADE,
        CHECK((record->>'recommendation_id'=recommendation_id::text AND record->>'trace_id'=trace_id::text
            AND record->>'answer_id'=answer_id::text AND record->>'origin'='retailops-ai'
            AND record->>'status'='proposed' AND record->'requires_human_review'='true'::jsonb) IS TRUE)
    );
    CREATE FUNCTION ai.assistant_transition() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP='INSERT' THEN
            IF NEW.status <> 'running' THEN
                RAISE EXCEPTION 'assistant_requires_admission' USING ERRCODE='23514';
            END IF;
        ELSE
            IF OLD.status <> 'running' OR NEW.status='running' OR
                (NEW.trace_id,NEW.correlation_id,NEW.environment,NEW.owner_id,NEW.scope,NEW.access_context,NEW.request_sha256,NEW.requested_at,
                    NEW.lease_until,NEW.retain_until,NEW.reserved_tokens,NEW.reserved_cost)
                IS DISTINCT FROM
                (OLD.trace_id,OLD.correlation_id,OLD.environment,OLD.owner_id,OLD.scope,OLD.access_context,OLD.request_sha256,OLD.requested_at,
                    OLD.lease_until,OLD.retain_until,OLD.reserved_tokens,OLD.reserved_cost) THEN
                RAISE EXCEPTION 'assistant_illegal_transition' USING ERRCODE='23514';
            END IF;
            IF NEW.status='succeeded' AND NOT EXISTS(SELECT 1 FROM ai.assistant_answers a
                WHERE a.trace_id=NEW.trace_id AND a.answer_id=(NEW.record->>'answer_id')::uuid) THEN
                RAISE EXCEPTION 'assistant_requires_answer' USING ERRCODE='23514';
            END IF;
        END IF;
        RETURN NEW;
    END;
    $$;
    CREATE TRIGGER assistant_transition BEFORE INSERT OR UPDATE ON ai.assistant_runs
        FOR EACH ROW EXECUTE FUNCTION ai.assistant_transition();
    CREATE TRIGGER assistant_answer_immutable BEFORE UPDATE ON ai.assistant_answers
        FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();
    CREATE TRIGGER assistant_suggestion_immutable BEFORE UPDATE ON ai.assistant_suggestions
        FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TABLE ai.assistant_suggestions")
    op.execute("DROP TABLE ai.assistant_answers")
    op.execute("DROP TABLE ai.assistant_runs")
    op.execute("DROP FUNCTION ai.assistant_transition()")
    op.execute("DROP TABLE ai.assistant_admission_policy")
