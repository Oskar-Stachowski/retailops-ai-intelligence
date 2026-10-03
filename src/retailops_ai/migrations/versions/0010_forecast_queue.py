"""Durable forecast intake, fenced leases, immutable attempt history and test-only worker receipts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_forecast_queue"
down_revision: str | None = "0009_model_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.forecast_batch_profiles (
 profile_id text PRIMARY KEY CHECK(profile_id ~ '^batch-profile-sha256-[0-9a-f]{64}$'),
 profile jsonb NOT NULL,
 CHECK ((profile->>'profile_id'=profile_id AND profile->>'environment'='test'
         AND profile->>'purpose'='lifecycle_mechanics_only') IS TRUE)
);
CREATE TABLE ai.forecast_batch_runs (
 run_id text PRIMARY KEY CHECK(run_id ~ '^run-[0-9a-f]{32}$'),
 environment text NOT NULL CHECK(environment IN ('test','local')),
 principal_id text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[0-9a-f]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 profile_id text NOT NULL REFERENCES ai.forecast_batch_profiles(profile_id),
 release_id text NOT NULL REFERENCES ai.model_releases(release_id),
 record jsonb NOT NULL,
 status text GENERATED ALWAYS AS (record->>'status') STORED NOT NULL,
 available_at timestamptz NOT NULL,
 run_deadline timestamptz NOT NULL,
 lease_token uuid,
 lease_expires timestamptz,
 attempt_deadline timestamptz,
 UNIQUE(environment,principal_id,key_hash),
 CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
 CHECK ((record->>'run_id'=run_id AND record->>'run_type'='forecast_batch'
  AND record->>'schema_version'='1.0' AND record->>'contract_type'='run'
  AND record->>'environment'=environment AND record->>'requested_by'=principal_id
  AND record->>'release_id'=release_id AND record->'input_ref'->>'profile_id'=profile_id
  AND record->'input_ref'->>'request_hash'=request_hash
  AND jsonb_typeof(record->'attempt')='number'
  AND (record->>'attempt')::integer BETWEEN 1 AND 5) IS TRUE),
 CHECK ((status='running')=(lease_token IS NOT NULL)),
 CHECK ((status='running')=(lease_expires IS NOT NULL)),
 CHECK ((status='running')=(attempt_deadline IS NOT NULL)),
 CHECK (lease_expires<=attempt_deadline AND attempt_deadline<=run_deadline),
 CHECK (((status='queued' AND record->'started_at'='null'::jsonb AND record->'completed_at'='null'::jsonb
                           AND record->'output_ref'='null'::jsonb AND record->'error'='null'::jsonb)
 OR (status='running' AND jsonb_typeof(record->'started_at')='string' AND record->'completed_at'='null'::jsonb
                      AND record->'output_ref'='null'::jsonb AND record->'error'='null'::jsonb)
 OR (status='succeeded' AND jsonb_typeof(record->'started_at')='string' AND jsonb_typeof(record->'completed_at')='string'
   AND record->'output_ref'->>'kind'='predictions' AND record->'output_ref'->'complete'='true'::jsonb AND record->'error'='null'::jsonb)
 OR (status IN ('failed','cancelled') AND jsonb_typeof(record->'completed_at')='string'
   AND record->'output_ref'='null'::jsonb AND jsonb_typeof(record->'error'->'retryable')='boolean'
   AND ((status='cancelled')=(record->'error'->>'code'='cancelled'))
   AND (status='cancelled' OR jsonb_typeof(record->'started_at')='string'))) IS TRUE),
 CHECK ((record->>'requested_at')::timestamptz<run_deadline),
 CHECK (record->'started_at'='null'::jsonb OR (record->>'started_at')::timestamptz >= (record->>'requested_at')::timestamptz),
 CHECK (record->'completed_at'='null'::jsonb OR (record->>'completed_at')::timestamptz >= coalesce((record->>'started_at')::timestamptz,(record->>'requested_at')::timestamptz))
);
CREATE INDEX forecast_batch_pending ON ai.forecast_batch_runs(environment,available_at,run_id)
 WHERE status IN ('queued','running');
CREATE TABLE ai.forecast_batch_attempts (
 run_id text NOT NULL REFERENCES ai.forecast_batch_runs(run_id),
 attempt integer NOT NULL CHECK(attempt BETWEEN 1 AND 5),
 reason text NOT NULL CHECK(reason ~ '^[a-z][a-z0-9_]{0,63}$'),
 record jsonb NOT NULL,
 PRIMARY KEY(run_id,attempt),
 CHECK ((record->>'run_id'=run_id AND (record->>'attempt')::integer=attempt
  AND record->>'status' IN ('succeeded','failed','cancelled')) IS TRUE)
);
CREATE TABLE ai.forecast_mechanics_outputs (
 artifact_id text PRIMARY KEY CHECK(artifact_id ~ '^predictions-sha256-[0-9a-f]{64}$'),
 run_id text NOT NULL UNIQUE REFERENCES ai.forecast_batch_runs(run_id),
 output jsonb NOT NULL,
 CHECK ((output->>'artifact_id'=artifact_id AND output->>'run_id'=run_id
  AND output->>'purpose'='lifecycle_mechanics_only' AND output->'forecast_quality_approved'='false'::jsonb
  AND output->'complete'='true'::jsonb) IS TRUE)
);
CREATE FUNCTION ai.forecast_batch_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; release jsonb; profile jsonb;
BEGIN
 SELECT r.release INTO release FROM ai.model_releases r WHERE r.release_id=NEW.release_id;
 SELECT p.profile INTO profile FROM ai.forecast_batch_profiles p WHERE p.profile_id=NEW.profile_id;
 IF (NEW.record->'resolved_model'=release->'binding' AND NEW.record->'image_digest'=release->'image_digest'
  AND NEW.record->'purpose'=release->'binding'->'qualification'->'purpose'
  AND NEW.record->'environment'=profile->'environment'
  AND NEW.record->'purpose'=profile->'purpose') IS NOT TRUE THEN
  RAISE EXCEPTION 'forecast_batch_release_profile_binding' USING ERRCODE='23514';
 END IF;
 FOREACH k IN ARRAY ARRAY['source_dataset_id','curated_dataset_id','feature_set_id','as_of_time'] LOOP
  IF NEW.record->'input_ref'->k IS DISTINCT FROM profile->k THEN
   RAISE EXCEPTION 'forecast_batch_profile_data_changed' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF TG_OP='INSERT' THEN
  IF (NEW.record->>'status')<>'queued' OR NEW.record->'attempt'<>'1'::jsonb THEN
   RAISE EXCEPTION 'forecast_batch_requires_queue' USING ERRCODE='23514';
  END IF;
 ELSE
  IF (NEW.run_id,NEW.environment,NEW.principal_id,NEW.key_hash,NEW.request_hash,NEW.profile_id,NEW.release_id,NEW.run_deadline)
    IS DISTINCT FROM (OLD.run_id,OLD.environment,OLD.principal_id,OLD.key_hash,OLD.request_hash,OLD.profile_id,OLD.release_id,OLD.run_deadline) THEN
   RAISE EXCEPTION 'forecast_batch_changed_identity' USING ERRCODE='23514';
  END IF;
  FOREACH k IN ARRAY ARRAY['schema_version','contract_type','run_id','run_type','requested_at','requested_by',
                           'input_ref','resolved_model','release_id','image_digest','environment','purpose','policy'] LOOP
   IF NEW.record->k IS DISTINCT FROM OLD.record->k THEN
    RAISE EXCEPTION 'forecast_batch_changed_pin' USING ERRCODE='23514';
   END IF;
  END LOOP;
  IF NEW.record=OLD.record AND (OLD.record->>'status')='running' AND NEW.lease_token=OLD.lease_token THEN RETURN NEW; END IF;
  IF (OLD.record->>'status')='failed' AND (NEW.record->>'status')='queued' THEN
   IF (NEW.record->>'attempt')::integer<>(OLD.record->>'attempt')::integer+1
      OR (NEW.record->>'attempt')::integer>(OLD.record->'policy'->>'max_attempts')::integer
      OR OLD.record->'error'->'retryable'<>'true'::jsonb
      OR NOT EXISTS(SELECT 1 FROM ai.forecast_batch_attempts a
                     WHERE a.run_id=OLD.run_id AND a.attempt=(OLD.record->>'attempt')::integer AND a.record=OLD.record) THEN
    RAISE EXCEPTION 'forecast_batch_retry_requires_closed_attempt' USING ERRCODE='23514';
   END IF;
  ELSE
   IF NEW.record->'attempt' IS DISTINCT FROM OLD.record->'attempt'
      OR NOT (((OLD.record->>'status')='queued' AND (NEW.record->>'status') IN ('running','cancelled'))
              OR ((OLD.record->>'status')='running' AND (NEW.record->>'status') IN ('succeeded','failed','cancelled'))) THEN
    RAISE EXCEPTION 'forecast_batch_illegal_transition' USING ERRCODE='23514';
   END IF;
   IF OLD.record->'started_at'<>'null'::jsonb AND NEW.record->'started_at' IS DISTINCT FROM OLD.record->'started_at' THEN
    RAISE EXCEPTION 'forecast_batch_changed_start' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 IF (NEW.record->>'status')='succeeded' AND NOT EXISTS(SELECT 1 FROM ai.forecast_mechanics_outputs o
   WHERE o.run_id=NEW.run_id AND o.artifact_id=NEW.record->'output_ref'->>'artifact_id'
    AND o.output->'release_id'=NEW.record->'release_id' AND o.output->'profile_id'=NEW.record->'input_ref'->'profile_id') THEN
  RAISE EXCEPTION 'forecast_batch_requires_persisted_complete_output' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_batch_transition BEFORE INSERT OR UPDATE ON ai.forecast_batch_runs
 FOR EACH ROW EXECUTE FUNCTION ai.forecast_batch_transition();
CREATE TRIGGER forecast_batch_no_delete BEFORE DELETE ON ai.forecast_batch_runs FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
""")
    for table in (
        "forecast_batch_profiles",
        "forecast_batch_attempts",
        "forecast_mechanics_outputs",
    ):
        op.execute(
            f"CREATE TRIGGER model_immutable BEFORE UPDATE OR DELETE ON ai.{table} FOR EACH ROW EXECUTE FUNCTION ai.model_immutable()"
        )


def downgrade() -> None:
    raise RuntimeError("forecast_queue_downgrade_requires_backup_restore")
