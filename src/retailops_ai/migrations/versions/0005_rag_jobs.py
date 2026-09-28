"""Immutable approved test build snapshots, reports and durable administrative runs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_rag_jobs"
down_revision: str | None = "0004_rag_denials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""CREATE TABLE ai.rag_build_profiles (
    profile_id text PRIMARY KEY CHECK(profile_id ~ '^index-build-profile-sha256-[0-9a-f]{64}$'),
    environment text NOT NULL CHECK(environment='test'),
    request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
    profile jsonb NOT NULL,
    UNIQUE(environment, request_hash),
    UNIQUE(profile_id, environment, request_hash),
    CHECK ((profile->>'profile_id'=profile_id AND profile->>'environment'=environment
        AND profile->>'purpose'='offline_build_mechanics_only'
        AND profile->>'evaluation_set_id'='offline-index-mechanics-v1'
        AND profile->'approval'->>'decision'='approved') IS TRUE)
);
CREATE TABLE ai.rag_index_reports (
    report_id text PRIMARY KEY CHECK(report_id ~ '^index-run-report-sha256-[0-9a-f]{64}$'),
    profile_id text NOT NULL REFERENCES ai.rag_build_profiles(profile_id),
    index_id text NOT NULL REFERENCES ai.rag_indexes(index_id),
    report jsonb NOT NULL,
    UNIQUE(report_id, profile_id, index_id),
    CHECK ((report->>'report_id'=report_id AND report->>'profile_id'=profile_id
        AND report->'validation'->>'index_id'=index_id
        AND report->'validation'->>'environment'='test'
        AND report->>'purpose'='offline_build_mechanics_only'
        AND report->'activation_allowed'='false'::jsonb) IS TRUE)
);
CREATE TABLE ai.knowledge_index_runs (
    run_id text PRIMARY KEY CHECK(run_id ~ '^run-[0-9a-f]{32}$'),
    environment text NOT NULL CHECK(environment='test'),
    principal_id text NOT NULL,
    key_hash text NOT NULL CHECK(key_hash ~ '^[0-9a-f]{64}$'),
    request_hash text NOT NULL,
    profile_id text NOT NULL,
    record jsonb NOT NULL,
    status text GENERATED ALWAYS AS (record->>'status') STORED NOT NULL,
    claim_token uuid,
    output_index_id text,
    report_id text,
    UNIQUE(environment, principal_id, key_hash),
    FOREIGN KEY(profile_id, environment, request_hash)
        REFERENCES ai.rag_build_profiles(profile_id, environment, request_hash),
    FOREIGN KEY(report_id, profile_id, output_index_id)
        REFERENCES ai.rag_index_reports(report_id, profile_id, index_id),
    CHECK ((record->>'schema_version'='1.0' AND record->>'contract_type'='run'
        AND record->>'run_type'='knowledge_index' AND record->>'run_id'=run_id
        AND record->>'requested_by'=principal_id AND record->'input_ref'->>'environment'=environment
        AND record->'input_ref'->>'profile_id'=profile_id
        AND record->'input_ref'->>'request_hash'=request_hash
        AND record->'attempt'='1'::jsonb AND record->'resolved_model'='null'::jsonb) IS TRUE),
    CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
    CHECK (((status='queued' AND record->'started_at'='null'::jsonb
                AND record->'completed_at'='null'::jsonb AND record->'error'='null'::jsonb)
        OR (status='running' AND jsonb_typeof(record->'started_at')='string'
                AND record->'completed_at'='null'::jsonb AND record->'error'='null'::jsonb)
        OR (status='succeeded' AND jsonb_typeof(record->'started_at')='string'
                AND jsonb_typeof(record->'completed_at')='string')
        OR (status IN ('failed','cancelled') AND jsonb_typeof(record->'completed_at')='string'
                AND record->'error'->>'code' IN ('invalid_input','dependency_unavailable','execution_failed','gate_failed','cancelled')
                AND jsonb_typeof(record->'error'->'retryable')='boolean'
                AND ((status='cancelled')=(record->'error'->>'code'='cancelled'))
                AND (status='cancelled' OR jsonb_typeof(record->'started_at')='string'))) IS TRUE),
    CHECK (record->'started_at'='null'::jsonb OR (record->>'started_at')::timestamptz >= (record->>'requested_at')::timestamptz),
    CHECK (record->'completed_at'='null'::jsonb OR (record->>'completed_at')::timestamptz >= COALESCE((record->>'started_at')::timestamptz,(record->>'requested_at')::timestamptz)),
    CHECK ((status='running') = (claim_token IS NOT NULL)),
    CHECK ((status='succeeded') = (output_index_id IS NOT NULL AND report_id IS NOT NULL)),
    CHECK (status='succeeded' OR (output_index_id IS NULL AND report_id IS NULL)),
    CHECK ((status='succeeded' AND
        (record->'output_ref'->>'index_id'=output_index_id
        AND record->'output_ref'->>'manifest_ref'='db:ai.rag_indexes:'||output_index_id
        AND record->'output_ref'->>'evaluation_report_ref'='db:ai.rag_index_reports:'||report_id
        AND record->'output_ref'->>'activation_status'='candidate'
        AND record->'output_ref'->>'kind'='knowledge_index'
        AND record->'output_ref'->'complete'='true'::jsonb
        AND record->'error'='null'::jsonb) IS TRUE)
        OR (status<>'succeeded' AND record->'output_ref'='null'::jsonb))
);
CREATE INDEX knowledge_runs_pending ON ai.knowledge_index_runs(environment, status)
    WHERE status IN ('queued','running');
CREATE FUNCTION ai.knowledge_report_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; m jsonb;
BEGIN
    SELECT profile INTO p FROM ai.rag_build_profiles WHERE profile_id=NEW.profile_id;
    SELECT manifest INTO m FROM ai.rag_indexes WHERE index_id=NEW.index_id;
    IF (m->>'environment'='test' AND m->>'chunk_manifest_id'=p->'chunks'->>'chunk_manifest_id'
        AND m->'embedding_config'=p->'embedding_config'
        AND NEW.report->'validation'->>'corpus_id'=m->>'corpus_id'
        AND NEW.report->'validation'->>'space_id'=m->>'space_id'
        AND NEW.report->'validation'->>'provider'='fake'
        AND NEW.report->'validation'->>'golden_evaluation'='not_evaluated_fake_vectors') IS NOT TRUE THEN
        RAISE EXCEPTION 'knowledge_report_binding_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER knowledge_report_binding BEFORE INSERT ON ai.rag_index_reports
    FOR EACH ROW EXECUTE FUNCTION ai.knowledge_report_binding();
CREATE FUNCTION ai.knowledge_run_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; r jsonb;
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.record->>'status'<>'queued' THEN
            RAISE EXCEPTION 'knowledge_run_requires_queue' USING ERRCODE='23514';
        END IF;
    ELSE
        IF (NEW.run_id,NEW.environment,NEW.principal_id,NEW.key_hash,NEW.request_hash,NEW.profile_id)
            IS DISTINCT FROM (OLD.run_id,OLD.environment,OLD.principal_id,OLD.key_hash,OLD.request_hash,OLD.profile_id) THEN
            RAISE EXCEPTION 'knowledge_run_changed_identity' USING ERRCODE='23514';
        END IF;
        FOREACH k IN ARRAY ARRAY['schema_version','contract_type','run_id','run_type','attempt',
            'requested_at','requested_by','input_ref','resolved_model'] LOOP
            IF NEW.record->k IS DISTINCT FROM OLD.record->k THEN
                RAISE EXCEPTION 'knowledge_run_changed_input' USING ERRCODE='23514';
            END IF;
        END LOOP;
        IF OLD.record->>'status'='running' AND NEW.record=OLD.record THEN
            RETURN NEW;
        END IF;
        IF NOT ((OLD.record->>'status'='queued' AND NEW.record->>'status' IN ('running','cancelled'))
            OR (OLD.record->>'status'='running' AND NEW.record->>'status' IN ('succeeded','failed','cancelled'))) THEN
            RAISE EXCEPTION 'knowledge_run_illegal_transition' USING ERRCODE='23514';
        END IF;
        IF OLD.record->'started_at'<>'null'::jsonb AND NEW.record->'started_at' IS DISTINCT FROM OLD.record->'started_at' THEN
            RAISE EXCEPTION 'knowledge_run_changed_start' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.record->>'status'='succeeded' THEN
        SELECT report INTO r FROM ai.rag_index_reports WHERE report_id=NEW.report_id;
        IF r->'validation'->>'result' IS DISTINCT FROM 'passed' THEN
            RAISE EXCEPTION 'knowledge_run_failed_gate' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER knowledge_run_transition BEFORE INSERT OR UPDATE ON ai.knowledge_index_runs
    FOR EACH ROW EXECUTE FUNCTION ai.knowledge_run_transition();
CREATE TRIGGER knowledge_run_no_delete BEFORE DELETE ON ai.knowledge_index_runs
    FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();
""")
    for table in ("rag_build_profiles", "rag_index_reports"):
        op.execute(
            f"CREATE TRIGGER rag_immutable BEFORE UPDATE OR DELETE ON ai.{table} FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable()"
        )


def downgrade() -> None:
    op.execute("DROP TABLE ai.knowledge_index_runs")
    op.execute("DROP FUNCTION ai.knowledge_run_transition()")
    op.execute("DROP TABLE ai.rag_index_reports")
    op.execute("DROP FUNCTION ai.knowledge_report_binding()")
    op.execute("DROP TABLE ai.rag_build_profiles")
