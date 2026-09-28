"""Approved local/test golden profiles, retained failed-gate reports and frozen thresholds."""

from collections.abc import Sequence
from importlib import import_module

from alembic import op
from sqlalchemy import text

revision: str = "0007_rag_golden_jobs"
down_revision: str | None = "0006_rag_job_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""ALTER TABLE ai.rag_build_profiles DROP CONSTRAINT rag_build_profiles_environment_check;
ALTER TABLE ai.rag_build_profiles DROP CONSTRAINT rag_build_profiles_check;
ALTER TABLE ai.rag_build_profiles ADD CONSTRAINT rag_build_profiles_environment_check CHECK(environment IN ('local','test'));
ALTER TABLE ai.rag_build_profiles ADD CONSTRAINT rag_build_profiles_approved_shape CHECK ((
    profile->>'profile_id'=profile_id AND profile->>'environment'=environment
    AND profile->'approval'->>'decision'='approved'
    AND profile->'approval'->>'environment'=environment
    AND profile->'chunks'->'corpus'->>'environment'=environment
    AND profile->'approval'->>'corpus_id'=profile->'chunks'->>'corpus_id'
    AND profile->'approval'->>'corpus_config_id'=profile->'chunks'->'corpus'->>'corpus_config_id'
    AND profile->'approval'->>'review_owner'=profile->'chunks'->'corpus'->>'review_owner'
    AND ((environment='test' AND profile->>'purpose'='offline_build_mechanics_only'
            AND profile->>'evaluation_set_id'='offline-index-mechanics-v1')
        OR (profile->>'purpose'='approved_corpus_fake_golden_validation'
            AND profile->>'evaluation_set_id'=profile->'golden_set'->>'golden_set_id'
            AND profile->'golden_approval'->>'decision'='approved'
            AND profile->'golden_approval'->>'environment'=environment
            AND profile->'golden_approval'->>'golden_set_id'=profile->'golden_set'->>'golden_set_id'
            AND profile->'golden_approval'->>'index_id'=profile->'golden_set'->>'index_id'
            AND profile->'golden_approval'->>'review_owner'=profile->'golden_set'->>'review_owner'
            AND profile->'golden_approval'->>'retrieval_config_id'=profile->'golden_set'->>'retrieval_config_id'
            AND profile->'embedding_config'->>'provider'='fake'))
) IS TRUE);
ALTER TABLE ai.rag_index_reports DROP CONSTRAINT rag_index_reports_check;
ALTER TABLE ai.rag_index_reports ADD CONSTRAINT rag_index_reports_bound_shape CHECK ((
    report->>'report_id'=report_id AND report->>'profile_id'=profile_id
    AND report->'validation'->>'index_id'=index_id
    AND report->'validation'->>'environment' IN ('local','test')
    AND report->>'purpose' IN ('offline_build_mechanics_only','approved_corpus_fake_golden_validation')
    AND report->'activation_allowed'='false'::jsonb
) IS TRUE);
ALTER TABLE ai.rag_index_reports ADD CONSTRAINT rag_report_profile_unique UNIQUE(report_id,profile_id);
ALTER TABLE ai.knowledge_index_runs DROP CONSTRAINT knowledge_index_runs_environment_check;
ALTER TABLE ai.knowledge_index_runs ADD CONSTRAINT knowledge_index_runs_environment_check CHECK(environment IN ('local','test'));
DO $$
DECLARE c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
        WHERE conrelid='ai.knowledge_index_runs'::regclass AND contype='c'
        AND ((pg_get_constraintdef(oid) LIKE '%output_index_id IS NULL%' AND pg_get_constraintdef(oid) LIKE '%report_id IS NULL%')
            OR (pg_get_constraintdef(oid) LIKE '%output_index_id IS NOT NULL%' AND pg_get_constraintdef(oid) LIKE '%report_id IS NOT NULL%'))
    LOOP
        EXECUTE format('ALTER TABLE ai.knowledge_index_runs DROP CONSTRAINT %I',c.conname);
    END LOOP;
END;
$$;
ALTER TABLE ai.knowledge_index_runs ADD CONSTRAINT knowledge_run_output_report_state CHECK (
    (status='succeeded' AND output_index_id IS NOT NULL AND report_id IS NOT NULL)
    OR (status='failed' AND output_index_id IS NULL AND (report_id IS NULL OR record->'error'->>'code'='gate_failed'))
    OR (status IN ('queued','running','cancelled') AND output_index_id IS NULL AND report_id IS NULL)
);
ALTER TABLE ai.knowledge_index_runs ADD CONSTRAINT knowledge_run_report_profile_fk
    FOREIGN KEY(report_id,profile_id) REFERENCES ai.rag_index_reports(report_id,profile_id);
CREATE OR REPLACE FUNCTION ai.knowledge_report_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; m jsonb;
BEGIN
    SELECT profile INTO p FROM ai.rag_build_profiles WHERE profile_id=NEW.profile_id;
    SELECT manifest INTO m FROM ai.rag_indexes WHERE index_id=NEW.index_id;
    IF (m->>'environment'=p->>'environment' AND m->>'chunk_manifest_id'=p->'chunks'->>'chunk_manifest_id'
        AND m->'embedding_config'=p->'embedding_config'
        AND NEW.report->>'purpose'=p->>'purpose'
        AND NEW.report->'validation'->>'environment'=p->>'environment'
        AND NEW.report->'validation'->>'corpus_id'=m->>'corpus_id'
        AND NEW.report->'validation'->>'space_id'=m->>'space_id'
        AND NEW.report->'validation'->>'provider'='fake'
        AND NEW.report->'validation'->>'golden_evaluation'='not_evaluated_fake_vectors') IS NOT TRUE THEN
        RAISE EXCEPTION 'knowledge_report_binding_mismatch' USING ERRCODE='23514';
    END IF;
    IF p->>'purpose'='approved_corpus_fake_golden_validation' THEN
        IF (NEW.report->'approval'=p->'approval' AND NEW.report->'golden_approval'=p->'golden_approval'
            AND NEW.report->'golden'->>'index_id'=NEW.index_id
            AND NEW.index_id=p->'golden_set'->>'index_id'
            AND NEW.report->'golden'->>'golden_set_id'=p->'golden_set'->>'golden_set_id'
            AND NEW.report->'golden'->>'retrieval_config_id'=p->'golden_set'->>'retrieval_config_id'
            AND NEW.report->'golden'->'thresholds'=p->'golden_set'->'thresholds'
            AND NEW.report->>'similarity_report_id'=p->'similarity_review'->>'report_id'
            AND NEW.report->>'similarity_review_id'=p->'similarity_review'->>'review_id'
            AND jsonb_typeof(NEW.report->'quality_gate_passed')='boolean'
            AND NEW.report->'golden'->>'provider'='fake'
            AND NEW.report->'golden'->'activation_allowed'='false'::jsonb) IS NOT TRUE THEN
            RAISE EXCEPTION 'knowledge_golden_report_binding_mismatch' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION ai.knowledge_success_gate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; p jsonb; g jsonb; t jsonb;
BEGIN
    IF NEW.record->>'status'='succeeded' THEN
        SELECT report INTO r FROM ai.rag_index_reports WHERE report_id=NEW.report_id;
        IF r->'validation'->'checks' IS DISTINCT FROM '{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": true}'::jsonb THEN
            RAISE EXCEPTION 'knowledge_run_failed_checks' USING ERRCODE='23514';
        END IF;
        SELECT profile INTO p FROM ai.rag_build_profiles WHERE profile_id=NEW.profile_id;
        IF p->>'purpose'='approved_corpus_fake_golden_validation' THEN
            g := r->'golden'; t := p->'golden_set'->'thresholds';
            IF (r->'quality_gate_passed'='true'::jsonb AND g->'measured_thresholds_passed'='true'::jsonb
                AND (g->>'recall_at_5')::numeric >= (t->>'recall_at_5_min')::numeric
                AND (g->>'mrr')::numeric >= (t->>'mrr_min')::numeric
                AND (g->>'citation_correctness')::numeric >= (t->>'citation_correctness_min')::numeric
                AND (g->>'critical_pass_rate')::numeric >= (t->>'critical_pass_rate_min')::numeric
                AND (g->>'latency_p95_ms')::numeric <= (t->>'latency_p95_ms_max')::numeric
                AND (g->>'model_cost_usd')::numeric = 0
                AND jsonb_array_length(g->'cases')=jsonb_array_length(p->'golden_set'->'cases')) IS NOT TRUE THEN
                RAISE EXCEPTION 'knowledge_run_failed_golden_gate' USING ERRCODE='23514';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;""")


def downgrade() -> None:
    populated = op.get_bind().scalar(
        text("""SELECT EXISTS(SELECT 1 FROM ai.knowledge_index_runs)
        OR EXISTS(SELECT 1 FROM ai.rag_index_reports) OR EXISTS(SELECT 1 FROM ai.rag_build_profiles)""")
    )
    if populated:
        raise RuntimeError("golden_jobs_downgrade_requires_empty_history")
    # Only empty job tables can be rebuilt; candidates, qualifications and pointers are retained.
    gate = import_module("retailops_ai.migrations.versions.0006_rag_job_gate")
    jobs = import_module("retailops_ai.migrations.versions.0005_rag_jobs")
    gate.downgrade()
    jobs.downgrade()
    jobs.upgrade()
    gate.upgrade()
