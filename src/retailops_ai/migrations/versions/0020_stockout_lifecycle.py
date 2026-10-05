"""Stockout-only append-only lifecycle with guarded and atomic approved head."""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_stockout_lifecycle"
down_revision: str | None = "0019_v12_development"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE FUNCTION ai.stockout_approved_capsule_valid(c jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
 SELECT (c->>'version'='stockout-inference-approval-1.0.0'
 AND c->'serving_eligible'='true'::jsonb
 AND c->'registered_in_mlflow'='false'::jsonb AND c->'activated_as_champion'='false'::jsonb
 AND c->'qualification'->>'version'='stockout-serving-qualification-1.0.0'
 AND c->'qualification'->'serving_eligible'='false'::jsonb
 AND c->'qualification'->'source_packages_verified'='true'::jsonb
 AND c->'qualification'->'complete_pipeline_verified'='true'::jsonb
 AND c->'qualification'->'repeatability_verified'='true'::jsonb
 AND c->'qualification'->'recipe'->'pin'=c->'qualification'->'policy'->'pin'
 AND ((c->'qualification'->>'purpose'='qualified_stockout'
       AND c->'qualification'->>'quality_status'='passed_independent_final_campaign'
       AND jsonb_typeof(c->'qualification'->'final_quality')='object'
       AND c->'qualification'->>'final_campaign_id' ~ '^stockout-final-campaign-sha256-[0-9a-f]{64}$')
   OR (c->'qualification'->>'purpose'='stockout_mechanics_only'
       AND c->'qualification'->>'quality_status'='not_evaluated_mechanics_only'
       AND c->'qualification'->'final_quality'='null'::jsonb
       AND c->'qualification'->'final_campaign_id'='null'::jsonb))
 AND c->'approval'->>'qualification_id'=c->'qualification'->>'qualification_id'
 AND (SELECT count(*) FROM jsonb_object_keys(c->'approval'->'gates'))=12
 AND c->'approval'->'gates' ?& ARRAY['source','features','pit','protocol','segments','signature',
     'resources','security_license','model_card','freshness_drift_compatibility','calibration','threshold_capacity']
 AND NOT EXISTS (SELECT 1 FROM jsonb_each(c->'approval'->'gates') g WHERE g.value->>'status' IS DISTINCT FROM 'passed')
 ) IS TRUE;
$$;
CREATE TABLE ai.stockout_model_decisions (
 decision_id text PRIMARY KEY CHECK(decision_id ~ '^decision-[a-z0-9-]{8,64}$'),
 model_name text NOT NULL CHECK(model_name IN ('retailops-stockout-risk','retailops-stockout-risk-test-mechanics')),
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 record jsonb NOT NULL CHECK(octet_length(record::text)<=8388608),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((record->'request'->>'decision_id'=decision_id AND record->'request'->>'model_name'=model_name
   AND record->>'request_sha256'=request_sha256
   AND record->'request'->>'action' IN ('register','reject','promote','rollback')) IS TRUE)
);
CREATE TABLE ai.stockout_model_steps (
 decision_id text NOT NULL REFERENCES ai.stockout_model_decisions(decision_id),
 phase text NOT NULL CHECK(phase IN ('create_attempted','version_bound','aliases_verified','completed')),
 record jsonb NOT NULL CHECK(octet_length(record::text)<=8388608),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(decision_id,phase)
);
CREATE TABLE ai.stockout_model_versions (
 model_name text NOT NULL, model_version text NOT NULL CHECK(model_version ~ '^[1-9][0-9]{0,8}$'),
 binding jsonb NOT NULL CHECK(octet_length(binding::text)<=8388608),
 decision_id text NOT NULL REFERENCES ai.stockout_model_decisions(decision_id),
 PRIMARY KEY(model_name,model_version),
 CHECK ((binding->>'model_name'=model_name AND binding->>'model_version'=model_version
   AND binding->>'approval_sha256' ~ '^[0-9a-f]{64}$'
   AND ai.stockout_approved_capsule_valid(binding->'approval')
   AND ((binding->'approval'->'qualification'->>'purpose'='stockout_mechanics_only')
        = (model_name='retailops-stockout-risk-test-mechanics'))) IS TRUE)
);
CREATE TABLE ai.stockout_model_releases (
 release_id text PRIMARY KEY CHECK(release_id ~ '^stockout-release-sha256-[0-9a-f]{64}$'),
 model_name text NOT NULL, model_version text NOT NULL, release jsonb NOT NULL,
 decision_id text NOT NULL REFERENCES ai.stockout_model_decisions(decision_id),
 FOREIGN KEY(model_name,model_version) REFERENCES ai.stockout_model_versions(model_name,model_version),
 UNIQUE(model_name,release_id),
 CHECK ((release->>'release_id'=release_id AND release->>'version'='stockout-database-release-1.0.0'
   AND release->'binding'->>'model_name'=model_name AND release->'binding'->>'model_version'=model_version
   AND release->>'decision_id'=decision_id
   AND release->>'image_digest'=release->'binding'->'approval'->'approval'->>'image_digest') IS TRUE)
);
CREATE TABLE ai.stockout_model_heads (
 model_name text PRIMARY KEY, release_id text NOT NULL,
 FOREIGN KEY(model_name,release_id) REFERENCES ai.stockout_model_releases(model_name,release_id)
);
CREATE TRIGGER stockout_model_immutable BEFORE UPDATE OR DELETE ON ai.stockout_model_decisions FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_model_immutable BEFORE UPDATE OR DELETE ON ai.stockout_model_steps FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_model_immutable BEFORE UPDATE OR DELETE ON ai.stockout_model_versions FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_model_immutable BEFORE UPDATE OR DELETE ON ai.stockout_model_releases FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_model_head_no_delete BEFORE DELETE ON ai.stockout_model_heads FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE FUNCTION ai.stockout_model_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d jsonb;
BEGIN
 SELECT record INTO d FROM ai.stockout_model_decisions WHERE decision_id=NEW.decision_id;
 IF NOT ((d->'request'->>'action'='register' AND d->'request'->>'model_name'=NEW.model_name
   AND d->'request'->>'approval_id'=NEW.binding->'approval'->>'release_id'
   AND d->'request'->>'approval_sha256'=NEW.binding->>'approval_sha256'
   AND d->'request'->>'mlflow_run_id'=NEW.binding->>'mlflow_run_id') IS TRUE) THEN
   RAISE EXCEPTION 'stockout_model_binding_decision_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER stockout_model_binding_guard BEFORE INSERT ON ai.stockout_model_versions FOR EACH ROW EXECUTE FUNCTION ai.stockout_model_binding_guard();
CREATE FUNCTION ai.stockout_model_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; d jsonb;
BEGIN
 SELECT binding INTO b FROM ai.stockout_model_versions WHERE model_name=NEW.model_name AND model_version=NEW.model_version;
 SELECT record INTO d FROM ai.stockout_model_decisions WHERE decision_id=NEW.decision_id;
 IF NOT ((NEW.release->'binding'=b AND d->'release'=NEW.release
   AND d->'request'->>'action' IN ('promote','rollback') AND d->'request'->>'model_name'=NEW.model_name) IS TRUE) THEN
   RAISE EXCEPTION 'stockout_model_release_binding_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER stockout_model_release_guard BEFORE INSERT ON ai.stockout_model_releases FOR EACH ROW EXECUTE FUNCTION ai.stockout_model_release_guard();
CREATE FUNCTION ai.stockout_model_head_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; previous text;
BEGIN
 IF TG_OP='UPDATE' AND OLD.release_id=NEW.release_id THEN RETURN NEW; END IF;
 SELECT release INTO r FROM ai.stockout_model_releases WHERE release_id=NEW.release_id AND model_name=NEW.model_name;
 IF TG_OP='UPDATE' THEN previous=OLD.release_id; ELSE previous=NULL; END IF;
 IF r IS NULL OR (r->>'previous_release_id') IS DISTINCT FROM previous THEN
   RAISE EXCEPTION 'stockout_model_head_previous_release_mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER stockout_model_head_guard BEFORE INSERT OR UPDATE ON ai.stockout_model_heads FOR EACH ROW EXECUTE FUNCTION ai.stockout_model_head_guard();
CREATE FUNCTION ai.stockout_model_head_completion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM ai.stockout_model_releases r JOIN ai.stockout_model_steps s USING(decision_id)
   WHERE r.release_id=NEW.release_id AND s.phase='completed' AND s.record->>'release_id'=r.release_id) THEN
   RAISE EXCEPTION 'stockout_model_head_requires_completed_decision' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER stockout_model_head_completion AFTER INSERT OR UPDATE ON ai.stockout_model_heads
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.stockout_model_head_completion();
""")


def downgrade() -> None:
    raise RuntimeError("stockout_model_history_downgrade_requires_backup_restore")
