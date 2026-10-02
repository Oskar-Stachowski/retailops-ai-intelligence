"""Separate v12 batch computation queue, leases, attempt history and atomic complete receipts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_v12_queue"
down_revision: str | None = "0015_v12_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.v12_batch_runs (
 run_id text PRIMARY KEY CHECK(run_id ~ '^run-[0-9a-f]{32}$'),
 environment text NOT NULL CHECK(environment IN ('local','test')),
 principal_id text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[0-9a-f]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 profile_id text NOT NULL,
 release_id text NOT NULL REFERENCES ai.v12_model_releases(release_id),
 FOREIGN KEY(environment,profile_id) REFERENCES ai.forecast_prepared_inputs(environment,profile_id),
 record jsonb NOT NULL CHECK(octet_length(record::text)<=8388608),
 status text GENERATED ALWAYS AS (record->>'status') STORED NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 available_at timestamptz NOT NULL, run_deadline timestamptz NOT NULL,
 lease_token uuid, lease_expires timestamptz, attempt_deadline timestamptz,
 UNIQUE(environment,principal_id,key_hash),
 CHECK((record->>'run_id'=run_id AND record->>'environment'=environment AND record->>'requested_by'=principal_id
  AND record->>'version'='forecast-v12-batch-run-1.0.0' AND record->>'purpose'='qualified_v12_computation'
  AND record->>'release_id'=release_id AND record->'input_ref'->>'profile_id'=profile_id
  AND record->'input_ref'->>'request_hash'=request_hash AND (record->>'attempt')::integer BETWEEN 1 AND 5
  AND (record->>'attempt')::integer <= (record->'policy'->>'max_attempts')::integer) IS TRUE),
 CHECK((status='running')=(lease_token IS NOT NULL)),
 CHECK((status='running')=(lease_expires IS NOT NULL)),
 CHECK((status='running')=(attempt_deadline IS NOT NULL)),
 CHECK(lease_expires<=attempt_deadline AND attempt_deadline<=run_deadline),
 CHECK((record->>'requested_at')::timestamptz<run_deadline),
 CHECK(record->'started_at'='null'::jsonb OR (record->>'started_at')::timestamptz >= (record->>'requested_at')::timestamptz),
 CHECK(record->'completed_at'='null'::jsonb OR (record->>'completed_at')::timestamptz >= coalesce((record->>'started_at')::timestamptz,(record->>'requested_at')::timestamptz)),
 CHECK(((status='queued' AND record->'started_at'='null'::jsonb AND record->'completed_at'='null'::jsonb AND record->'output_id'='null'::jsonb AND record->'error'='null'::jsonb)
 OR (status='running' AND jsonb_typeof(record->'started_at')='string' AND record->'completed_at'='null'::jsonb AND record->'output_id'='null'::jsonb AND record->'error'='null'::jsonb)
 OR (status='succeeded' AND jsonb_typeof(record->'started_at')='string' AND jsonb_typeof(record->'completed_at')='string' AND jsonb_typeof(record->'output_id')='string' AND record->'error'='null'::jsonb)
 OR (status IN ('failed','cancelled') AND jsonb_typeof(record->'completed_at')='string' AND record->'output_id'='null'::jsonb AND jsonb_typeof(record->'error'->'retryable')='boolean'
   AND ((status='cancelled')=(record->'error'->>'code'='cancelled')) AND (status='cancelled' OR jsonb_typeof(record->'started_at')='string'))) IS TRUE)
);
CREATE INDEX v12_batch_pending ON ai.v12_batch_runs(environment,available_at,run_id) WHERE status IN ('queued','running');
CREATE TABLE ai.v12_batch_attempts (
 run_id text NOT NULL REFERENCES ai.v12_batch_runs(run_id), attempt integer NOT NULL CHECK(attempt BETWEEN 1 AND 5),
 reason text NOT NULL CHECK(reason ~ '^[a-z][a-z0-9_]{0,63}$'), record jsonb NOT NULL,
 PRIMARY KEY(run_id,attempt),
 CHECK((record->>'run_id'=run_id AND (record->>'attempt')::integer=attempt AND record->>'status' IN ('succeeded','failed','cancelled')) IS TRUE)
);
CREATE TABLE ai.v12_batch_receipts (
 artifact_id text PRIMARY KEY CHECK(artifact_id ~ '^v12-computation-sha256-[0-9a-f]{64}$'),
 run_id text NOT NULL UNIQUE REFERENCES ai.v12_batch_runs(run_id),
 receipt jsonb NOT NULL CHECK(octet_length(receipt::text)<=12582912),
 CHECK((receipt->>'artifact_id'=artifact_id AND receipt->>'run_id'=run_id
   AND receipt->>'purpose'='qualified_v12_computation' AND receipt->'complete'='true'::jsonb
   AND receipt->'published_forecast_outputs'='0'::jsonb AND receipt->'model_refits'='0'::jsonb
   AND receipt->'source_generation'='false'::jsonb) IS TRUE)
);
CREATE FUNCTION ai.v12_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; p jsonb; k text;
BEGIN
 SELECT release INTO r FROM ai.v12_model_releases WHERE release_id=NEW.release_id;
 SELECT profile INTO p FROM ai.forecast_prepared_inputs WHERE environment=NEW.environment AND profile_id=NEW.profile_id;
 IF NOT ((NEW.record->'resolved_model'=r->'binding' AND NEW.record->'image_digest'=r->'image_digest'
  AND p->>'schema_version'='1.1' AND NEW.record->'input_ref'->'as_of_time'=p->'as_of_time'
  AND NEW.record->'input_ref'->'source_dataset_id'=p->'feature_manifest'->'descriptor'->'parent'->'source_dataset_id'
  AND NEW.record->'input_ref'->'curated_dataset_id'=p->'feature_manifest'->'descriptor'->'parent'->'curated_dataset_id'
  AND NEW.record->'input_ref'->'feature_set_id'=p->'feature_manifest'->'feature_set_id'
  AND (NEW.environment='test' OR NEW.record->'resolved_model'->>'model_name'='retailops-demand-forecast-v12')) IS TRUE) THEN
  RAISE EXCEPTION 'v12_batch_release_profile_mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF (NEW.record->>'status')<>'queued' OR NEW.record->'attempt'<>'1'::jsonb THEN
   RAISE EXCEPTION 'v12_batch_requires_queue' USING ERRCODE='23514';
  END IF;
 ELSE
  IF (NEW.run_id,NEW.environment,NEW.principal_id,NEW.key_hash,NEW.request_hash,NEW.profile_id,NEW.release_id,NEW.run_deadline)
   IS DISTINCT FROM (OLD.run_id,OLD.environment,OLD.principal_id,OLD.key_hash,OLD.request_hash,OLD.profile_id,OLD.release_id,OLD.run_deadline) THEN
   RAISE EXCEPTION 'v12_batch_changed_identity' USING ERRCODE='23514';
  END IF;
  FOREACH k IN ARRAY ARRAY['version','purpose','run_id','requested_at','requested_by','input_ref','resolved_model','release_id','image_digest','environment','policy'] LOOP
   IF NEW.record->k IS DISTINCT FROM OLD.record->k THEN
    RAISE EXCEPTION 'v12_batch_changed_pin' USING ERRCODE='23514';
   END IF;
  END LOOP;
  IF NEW.record=OLD.record AND (OLD.record->>'status')='running' AND NEW.lease_token=OLD.lease_token THEN RETURN NEW; END IF;
  IF (OLD.record->>'status')='failed' AND (NEW.record->>'status')='queued' THEN
   IF (NEW.record->>'attempt')::integer<>(OLD.record->>'attempt')::integer+1 OR OLD.record->'error'->'retryable'<>'true'::jsonb
    OR NOT EXISTS(SELECT 1 FROM ai.v12_batch_attempts a WHERE a.run_id=OLD.run_id AND a.attempt=(OLD.record->>'attempt')::integer AND a.record=OLD.record) THEN
    RAISE EXCEPTION 'v12_batch_retry_requires_history' USING ERRCODE='23514';
   END IF;
  ELSE
   IF NEW.record->'attempt' IS DISTINCT FROM OLD.record->'attempt' OR NOT (((OLD.record->>'status')='queued' AND (NEW.record->>'status') IN ('running','cancelled')) OR ((OLD.record->>'status')='running' AND (NEW.record->>'status') IN ('succeeded','failed','cancelled'))) THEN
    RAISE EXCEPTION 'v12_batch_illegal_transition' USING ERRCODE='23514';
   END IF;
   IF OLD.record->'started_at'<>'null'::jsonb AND NEW.record->'started_at' IS DISTINCT FROM OLD.record->'started_at' THEN
    RAISE EXCEPTION 'v12_batch_changed_start' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 IF (NEW.record->>'status')='succeeded' AND NOT EXISTS(SELECT 1 FROM ai.v12_batch_receipts o WHERE o.run_id=NEW.run_id AND o.artifact_id=NEW.record->>'output_id' AND o.receipt->'release'=r AND o.receipt->>'profile_id'=NEW.profile_id) THEN
  RAISE EXCEPTION 'v12_batch_requires_complete_receipt' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER v12_batch_guard BEFORE INSERT OR UPDATE ON ai.v12_batch_runs FOR EACH ROW EXECUTE FUNCTION ai.v12_batch_guard();
CREATE TRIGGER v12_batch_no_delete BEFORE DELETE ON ai.v12_batch_runs FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER v12_batch_immutable BEFORE UPDATE OR DELETE ON ai.v12_batch_attempts FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER v12_batch_immutable BEFORE UPDATE OR DELETE ON ai.v12_batch_receipts FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE FUNCTION ai.v12_batch_closed_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (NEW.record->>'status') IN ('failed','cancelled','succeeded') AND NOT EXISTS(
  SELECT 1 FROM ai.v12_batch_attempts a WHERE a.run_id=NEW.run_id
   AND a.attempt=(NEW.record->>'attempt')::integer AND a.record=NEW.record) THEN
  RAISE EXCEPTION 'v12_batch_completion_requires_attempt_history' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER v12_batch_closed_attempt AFTER UPDATE ON ai.v12_batch_runs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.v12_batch_closed_attempt();
""")


def downgrade() -> None:
    raise RuntimeError("v12_queue_history_downgrade_requires_backup_restore")
