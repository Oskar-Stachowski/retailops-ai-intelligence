"""Semantic spaces, approved cached jobs and quality-bound retrieval qualifications."""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_rag_semantic"
down_revision: str | None = "0007_rag_golden_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ai.rag_embedding_spaces DROP CONSTRAINT rag_embedding_spaces_dimension_check;
ALTER TABLE ai.rag_embedding_spaces DROP CONSTRAINT rag_embedding_spaces_config_check;
ALTER TABLE ai.rag_embedding_spaces ADD CONSTRAINT rag_embedding_provider CHECK ((
 (config->>'provider'='fake' AND config->>'region'='offline' AND config->>'model_id'='sha256-unit-f32-v1' AND dimension IN (8,16,32,64) AND config->>'transformation_version'='utf8-chunk-body-v1')
 OR (config->>'provider'='bedrock' AND config->>'region' ~ '^[a-z]{2}-[a-z]+-[0-9]$' AND config->>'model_id'='amazon.titan-embed-text-v2:0' AND dimension IN (256,512,1024) AND config->>'transformation_version' IN ('utf8-chunk-body-v1','utf8-heading-path-body-v1'))
) IS TRUE);
CREATE OR REPLACE FUNCTION ai.rag_chunk_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE expected_entry jsonb; expected_chunk jsonb; expected_checksum text; actual_checksum text; cfg jsonb; input_checksum text;
BEGIN
 SELECT manifest->'entries'->NEW.ordinal, chunk_manifest->'chunks'->NEW.ordinal,manifest->'embedding_config'
 INTO expected_entry,expected_chunk,cfg FROM ai.rag_indexes WHERE index_id=NEW.index_id;
 SELECT vector_checksum,content_checksum INTO expected_checksum,actual_checksum
 FROM ai.rag_embeddings WHERE environment=NEW.environment AND embedding_id=NEW.embedding_id;
 input_checksum := expected_chunk->>'content_checksum';
 IF cfg->>'transformation_version'='utf8-heading-path-body-v1' THEN
   SELECT encode(sha256(convert_to(string_agg(h->>'title', E'\n' ORDER BY ordinal) || E'\n\n' || (expected_chunk->>'text'),'UTF8')),'hex')
   INTO input_checksum FROM jsonb_array_elements(expected_chunk->'heading_path') WITH ORDINALITY AS headings(h,ordinal);
 END IF;
 IF (expected_entry->>'chunk_id'=NEW.chunk_id AND expected_entry->>'embedding_id'=NEW.embedding_id
 AND expected_entry->>'vector_checksum'=expected_checksum AND expected_chunk=NEW.metadata
 AND expected_chunk->>'chunk_id'=NEW.chunk_id AND input_checksum=actual_checksum) IS NOT TRUE THEN
   RAISE EXCEPTION 'rag_chunk_binding_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
ALTER TABLE ai.rag_build_profiles DROP CONSTRAINT rag_build_profiles_approved_shape;
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
        OR (profile->>'purpose' IN ('approved_corpus_fake_golden_validation','approved_corpus_semantic_validation')
            AND profile->>'evaluation_set_id'=profile->'golden_set'->>'golden_set_id'
            AND profile->'golden_approval'->>'decision'='approved'
            AND profile->'golden_approval'->>'environment'=environment
            AND profile->'golden_approval'->>'golden_set_id'=profile->'golden_set'->>'golden_set_id'
            AND profile->'golden_approval'->>'index_id'=profile->'golden_set'->>'index_id'
            AND profile->'golden_approval'->>'review_owner'=profile->'golden_set'->>'review_owner'
            AND profile->'golden_approval'->>'retrieval_config_id'=profile->'golden_set'->>'retrieval_config_id'
            AND ((profile->>'purpose'='approved_corpus_fake_golden_validation' AND profile->'embedding_config'->>'provider'='fake') OR (profile->>'purpose'='approved_corpus_semantic_validation' AND profile->'embedding_config'->>'provider'='bedrock' AND jsonb_array_length(profile->'document_embeddings') BETWEEN 1 AND 32768 AND jsonb_array_length(profile->'query_embeddings') BETWEEN 1 AND 50))))
) IS TRUE);

ALTER TABLE ai.rag_index_reports DROP CONSTRAINT rag_index_reports_bound_shape;
ALTER TABLE ai.rag_index_reports ADD CONSTRAINT rag_index_reports_bound_shape CHECK ((
 report->>'report_id'=report_id AND report->>'profile_id'=profile_id
 AND report->'validation'->>'index_id'=index_id AND report->'validation'->>'environment' IN ('local','test')
 AND report->>'purpose' IN ('offline_build_mechanics_only','approved_corpus_fake_golden_validation','approved_corpus_semantic_validation')
 AND report->'activation_allowed'='false'::jsonb
) IS TRUE);
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
        AND NEW.report->'validation'->>'provider'=p->'embedding_config'->>'provider'
        AND NEW.report->'validation'->>'golden_evaluation'=m->>'semantic_quality') IS NOT TRUE THEN
        RAISE EXCEPTION 'knowledge_report_binding_mismatch' USING ERRCODE='23514';
    END IF;
    IF p->>'purpose' IN ('approved_corpus_fake_golden_validation','approved_corpus_semantic_validation') THEN
        IF (NEW.report->'approval'=p->'approval' AND NEW.report->'golden_approval'=p->'golden_approval'
            AND NEW.report->'golden'->>'index_id'=NEW.index_id
            AND NEW.index_id=p->'golden_set'->>'index_id'
            AND NEW.report->'golden'->>'golden_set_id'=p->'golden_set'->>'golden_set_id'
            AND NEW.report->'golden'->>'retrieval_config_id'=p->'golden_set'->>'retrieval_config_id'
            AND NEW.report->'golden'->'thresholds'=p->'golden_set'->'thresholds'
            AND NEW.report->>'similarity_report_id'=p->'similarity_review'->>'report_id'
            AND NEW.report->>'similarity_review_id'=p->'similarity_review'->>'review_id'
            AND jsonb_typeof(NEW.report->'quality_gate_passed')='boolean'
            AND NEW.report->'golden'->>'provider'=p->'embedding_config'->>'provider'
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
        IF (r->'validation'->>'provider'='fake' AND r->'validation'->'checks' IS DISTINCT FROM '{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": true}'::jsonb) OR (r->'validation'->>'provider'='bedrock' AND r->'validation'->'checks' IS DISTINCT FROM '{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": null}'::jsonb) THEN
            RAISE EXCEPTION 'knowledge_run_failed_checks' USING ERRCODE='23514';
        END IF;
        SELECT profile INTO p FROM ai.rag_build_profiles WHERE profile_id=NEW.profile_id;
        IF p->>'purpose' IN ('approved_corpus_fake_golden_validation','approved_corpus_semantic_validation') THEN
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
$$;
CREATE TABLE ai.rag_semantic_releases (
 release_id text PRIMARY KEY,
 index_id text NOT NULL,
 environment text NOT NULL,
 report_id text NOT NULL REFERENCES ai.rag_index_reports(report_id),
 release jsonb NOT NULL,
 UNIQUE(release_id,index_id,environment),
 FOREIGN KEY(index_id,environment) REFERENCES ai.rag_indexes(index_id,environment),
 CHECK ((release->>'release_id'=release_id AND release->'index_manifest'->>'index_id'=index_id
 AND release->'index_manifest'->>'environment'=environment AND release->>'policy_version'='semantic-release-v1'
 AND release->>'status'='ready' AND release->'activation_allowed'='true'::jsonb
 AND release->'blockers'='[]'::jsonb AND release->'golden_report'->>'provider'='bedrock'
 AND release->'golden_report'->'measured_thresholds_passed'='true'::jsonb) IS TRUE)
);
CREATE TRIGGER rag_immutable BEFORE UPDATE OR DELETE ON ai.rag_semantic_releases FOR EACH ROW EXECUTE FUNCTION ai.rag_immutable();
CREATE FUNCTION ai.rag_semantic_release_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; p jsonb; m jsonb;
BEGIN
 SELECT report,profile INTO r,p FROM ai.rag_index_reports JOIN ai.rag_build_profiles USING(profile_id) WHERE report_id=NEW.report_id AND index_id=NEW.index_id;
 SELECT manifest INTO m FROM ai.rag_indexes WHERE index_id=NEW.index_id;
 IF (r->>'purpose'='approved_corpus_semantic_validation' AND r->'quality_gate_passed'='true'::jsonb
 AND NEW.release->'index_manifest'=m AND NEW.release->'corpus_manifest'=p->'chunks'->'corpus'
 AND NEW.release->'golden_report'=r->'golden' AND NEW.release->'mechanical_validation'=r->'validation'
 AND NEW.release->'corpus_approval'=p->'approval' AND NEW.release->'golden_approval'=p->'golden_approval'
 AND NEW.release->'golden_set'=p->'golden_set' AND NEW.release->'retrieval_config'=p->'retrieval_config'
 AND NEW.release->'similarity_review'=p->'similarity_review'
 AND NEW.release->>'similarity_report_id'=r->>'similarity_report_id'
 AND (NEW.release->>'unreviewed_similarity_findings')::integer=0
 AND EXISTS(SELECT 1 FROM ai.knowledge_index_runs WHERE report_id=NEW.report_id AND environment=NEW.environment AND status='succeeded' AND output_index_id=NEW.index_id)) IS NOT TRUE THEN
  RAISE EXCEPTION 'semantic_release_binding_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER rag_semantic_release_binding BEFORE INSERT ON ai.rag_semantic_releases FOR EACH ROW EXECUTE FUNCTION ai.rag_semantic_release_binding();
ALTER TABLE ai.rag_qualifications ADD COLUMN release_id text;
ALTER TABLE ai.rag_qualifications ADD FOREIGN KEY(release_id,index_id,environment) REFERENCES ai.rag_semantic_releases(release_id,index_id,environment);
ALTER TABLE ai.rag_qualifications DROP CONSTRAINT rag_qualifications_environment_check;
ALTER TABLE ai.rag_qualifications DROP CONSTRAINT rag_qualifications_lane_check;
ALTER TABLE ai.rag_qualifications DROP CONSTRAINT rag_qualifications_check;
ALTER TABLE ai.rag_qualifications ADD CONSTRAINT rag_qualification_gate CHECK ((
 validation->>'validation_id'=validation_id AND validation->>'index_id'=index_id AND validation->>'environment'=environment AND validation->>'result'='passed'
 AND ((environment='test' AND lane='offline_test' AND release_id IS NULL AND validation->>'provider'='fake'
 AND validation->>'golden_evaluation'='not_evaluated_fake_vectors'
 AND validation->'checks'='{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": true}'::jsonb)
 OR (environment IN ('local','test') AND lane='retrieval' AND release_id IS NOT NULL AND validation->>'provider'='bedrock'
 AND validation->>'golden_evaluation'='not_evaluated_real_vectors'
 AND validation->'checks'='{"complete_graph": true,"source_metadata": true,"citation_binding": true,"vector_binding": true,"deterministic_fake_vectors": null}'::jsonb))
) IS TRUE);
CREATE FUNCTION ai.rag_semantic_qualification_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb;
BEGIN
 IF NEW.lane='retrieval' THEN
   SELECT release INTO r FROM ai.rag_semantic_releases WHERE release_id=NEW.release_id;
   IF (r->'mechanical_validation'=NEW.validation AND r->'corpus_approval'->>'review_id'=NEW.review_id) IS NOT TRUE THEN
     RAISE EXCEPTION 'semantic_qualification_binding_mismatch' USING ERRCODE='23514';
   END IF;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER rag_semantic_qualification_binding BEFORE INSERT ON ai.rag_qualifications FOR EACH ROW EXECUTE FUNCTION ai.rag_semantic_qualification_binding();
""")


def downgrade() -> None:
    # Immutable accepted histories cannot safely be represented by the fake-only schema.
    raise RuntimeError("semantic_schema_downgrade_requires_database_backup_restore")
