"""Require every recorded mechanical check before a persisted run can succeed."""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_rag_job_gate"
down_revision: str | None = "0005_rag_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE FUNCTION ai.knowledge_success_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
    IF NEW.record->>'status'='succeeded' THEN
        SELECT report INTO r FROM ai.rag_index_reports WHERE report_id=NEW.report_id;
        IF r->'validation'->'checks' IS DISTINCT FROM '{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": true}'::jsonb THEN
            RAISE EXCEPTION 'knowledge_run_failed_checks' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER knowledge_success_gate BEFORE UPDATE ON ai.knowledge_index_runs
    FOR EACH ROW EXECUTE FUNCTION ai.knowledge_success_gate();""")


def downgrade() -> None:
    op.execute("DROP TRIGGER knowledge_success_gate ON ai.knowledge_index_runs")
    op.execute("DROP FUNCTION ai.knowledge_success_gate()")
