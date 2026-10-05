"""Public sealed physical inputs, durable fenced queue and atomic risk publication."""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_stockout_jobs"
down_revision: str | None = "0020_stockout_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.stockout_prepared_inputs (
 environment text NOT NULL CHECK(environment IN ('local','test')),
 inputs_id text NOT NULL CHECK(inputs_id ~ '^stockout-inputs-sha256-[0-9a-f]{64}$'),
 registered_by text NOT NULL, registered_at timestamptz NOT NULL DEFAULT now(),
 inputs jsonb NOT NULL CHECK(octet_length(inputs::text)<=25165824),
 PRIMARY KEY(environment,inputs_id),
 CHECK((inputs->>'inputs_id'=inputs_id AND inputs->>'version'='stockout-prepared-inputs-1.0.0'
  AND inputs->>'input_role'='inference_public_facts_only'
  AND inputs->>'parent_replay'='complete_public_features_and_upstream'
  AND jsonb_array_length(inputs->'points') BETWEEN 1 AND 100
  AND jsonb_array_length(inputs->'scope'->'product_ids') BETWEEN 1 AND 20
  AND jsonb_array_length(inputs->'scope'->'stock_location_ids') BETWEEN 1 AND 5
  AND jsonb_array_length(inputs->'points')=jsonb_array_length(inputs->'scope'->'product_ids')*jsonb_array_length(inputs->'scope'->'stock_location_ids')
  AND inputs->>'source_parent_files_sha256' ~ '^[0-9a-f]{64}$'
  AND inputs->>'preparation_code_sha256' ~ '^[0-9a-f]{64}$') IS TRUE)
);
CREATE TRIGGER stockout_inputs_immutable BEFORE UPDATE OR DELETE ON ai.stockout_prepared_inputs FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TABLE ai.stockout_batch_runs (
 run_id text PRIMARY KEY CHECK(run_id ~ '^run-[0-9a-f]{32}$'),
 environment text NOT NULL CHECK(environment IN ('local','test')),
 principal_id text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[0-9a-f]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
 profile_id text NOT NULL, release_id text NOT NULL REFERENCES ai.stockout_model_releases(release_id),
 FOREIGN KEY(environment,profile_id) REFERENCES ai.stockout_prepared_inputs(environment,inputs_id),
 record jsonb NOT NULL CHECK(octet_length(record::text)<=8388608),
 status text GENERATED ALWAYS AS (record->>'status') STORED NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 available_at timestamptz NOT NULL, run_deadline timestamptz NOT NULL,
 lease_token uuid, lease_expires timestamptz, attempt_deadline timestamptz,
 UNIQUE(environment,principal_id,key_hash),
 CHECK((record->>'run_id'=run_id AND record->>'environment'=environment AND record->>'requested_by'=principal_id
  AND record->>'version'='stockout-batch-run-1.0.0' AND record->>'run_type'='stockout_batch'
  AND record->'release'->>'release_id'=release_id AND record->'input_ref'->'request'->>'profile_id'=profile_id
  AND record->'input_ref'->>'request_sha256'=request_hash AND (record->>'attempt')::integer BETWEEN 1 AND 5
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
CREATE INDEX stockout_batch_pending ON ai.stockout_batch_runs(environment,available_at,run_id) WHERE status IN ('queued','running');
CREATE TABLE ai.stockout_batch_attempts (
 run_id text NOT NULL REFERENCES ai.stockout_batch_runs(run_id), attempt integer NOT NULL CHECK(attempt BETWEEN 1 AND 5),
 reason text NOT NULL CHECK(reason ~ '^[a-z][a-z0-9_]{0,63}$'), record jsonb NOT NULL,
 PRIMARY KEY(run_id,attempt),
 CHECK((record->>'run_id'=run_id AND (record->>'attempt')::integer=attempt AND record->>'status' IN ('succeeded','failed','cancelled')) IS TRUE)
);
CREATE TABLE ai.stockout_batch_outputs (
 output_id text PRIMARY KEY CHECK(output_id ~ '^stockout-output-sha256-[0-9a-f]{64}$'),
 run_id text NOT NULL UNIQUE REFERENCES ai.stockout_batch_runs(run_id),
 output jsonb NOT NULL CHECK(octet_length(output::text)<=6291456),
 CHECK((output->>'output_id'=output_id AND output->>'run_id'=run_id
  AND output->>'version'='stockout-batch-output-1.0.0' AND output->'complete'='true'::jsonb
  AND output->'model_refits'='0'::jsonb AND output->'source_generation'='false'::jsonb
  AND jsonb_array_length(output->'items') BETWEEN 1 AND 100) IS TRUE)
);
CREATE FUNCTION ai.stockout_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; p jsonb; k text;
BEGIN
 SELECT release INTO r FROM ai.stockout_model_releases WHERE release_id=NEW.release_id;
 SELECT inputs INTO p FROM ai.stockout_prepared_inputs WHERE environment=NEW.environment AND inputs_id=NEW.profile_id;
 IF NOT ((NEW.record->'release'=r AND NEW.record->'input_ref'->'request'->'as_of'=p->'as_of'
  AND NEW.record->'input_ref'->'lineage'=p->'lineage'
  AND (NEW.record->'input_ref'->'request'->'scope'->'product_ids') @> (p->'scope'->'product_ids')
  AND (NEW.record->'input_ref'->'request'->'scope'->'product_ids') <@ (p->'scope'->'product_ids')
  AND (NEW.record->'input_ref'->'request'->'scope'->'stock_location_ids') @> (p->'scope'->'stock_location_ids')
  AND (NEW.record->'input_ref'->'request'->'scope'->'stock_location_ids') <@ (p->'scope'->'stock_location_ids')
  AND (NEW.environment='test' OR r->'binding'->>'model_name'='retailops-stockout-risk')) IS TRUE) THEN
  RAISE EXCEPTION 'stockout_batch_release_profile_mismatch' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.record->>'status'<>'queued' OR NEW.record->'attempt'<>'1'::jsonb THEN
   RAISE EXCEPTION 'stockout_batch_requires_queue' USING ERRCODE='23514';
  END IF;
 ELSE
  IF (NEW.run_id,NEW.environment,NEW.principal_id,NEW.key_hash,NEW.request_hash,NEW.profile_id,NEW.release_id,NEW.run_deadline)
   IS DISTINCT FROM (OLD.run_id,OLD.environment,OLD.principal_id,OLD.key_hash,OLD.request_hash,OLD.profile_id,OLD.release_id,OLD.run_deadline) THEN
   RAISE EXCEPTION 'stockout_batch_changed_identity' USING ERRCODE='23514';
  END IF;
  FOREACH k IN ARRAY ARRAY['version','run_type','run_id','requested_at','requested_by','input_ref','release','environment','policy'] LOOP
   IF NEW.record->k IS DISTINCT FROM OLD.record->k THEN RAISE EXCEPTION 'stockout_batch_changed_pin' USING ERRCODE='23514'; END IF;
  END LOOP;
  IF NEW.record=OLD.record AND OLD.record->>'status'='running' AND NEW.lease_token=OLD.lease_token THEN RETURN NEW; END IF;
  IF OLD.record->>'status'='failed' AND NEW.record->>'status'='queued' THEN
   IF (NEW.record->>'attempt')::integer<>(OLD.record->>'attempt')::integer+1 OR OLD.record->'error'->'retryable'<>'true'::jsonb
    OR NOT EXISTS(SELECT 1 FROM ai.stockout_batch_attempts a WHERE a.run_id=OLD.run_id AND a.attempt=(OLD.record->>'attempt')::integer AND a.record=OLD.record) THEN
    RAISE EXCEPTION 'stockout_batch_retry_requires_history' USING ERRCODE='23514';
   END IF;
  ELSE
   IF NEW.record->'attempt' IS DISTINCT FROM OLD.record->'attempt' OR NOT ((OLD.record->>'status'='queued' AND NEW.record->>'status' IN ('running','cancelled')) OR (OLD.record->>'status'='running' AND NEW.record->>'status' IN ('succeeded','failed','cancelled'))) THEN
    RAISE EXCEPTION 'stockout_batch_illegal_transition' USING ERRCODE='23514';
   END IF;
   IF OLD.record->'started_at'<>'null'::jsonb AND NEW.record->'started_at' IS DISTINCT FROM OLD.record->'started_at' THEN
    RAISE EXCEPTION 'stockout_batch_changed_start' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER stockout_batch_guard BEFORE INSERT OR UPDATE ON ai.stockout_batch_runs FOR EACH ROW EXECUTE FUNCTION ai.stockout_batch_guard();
CREATE TRIGGER stockout_batch_no_delete BEFORE DELETE ON ai.stockout_batch_runs FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_attempts_immutable BEFORE UPDATE OR DELETE ON ai.stockout_batch_attempts FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE TRIGGER stockout_outputs_immutable BEFORE UPDATE OR DELETE ON ai.stockout_batch_outputs FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
CREATE FUNCTION ai.stockout_output_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; i jsonb; expected integer; unique_keys integer;
BEGIN
 SELECT record INTO r FROM ai.stockout_batch_runs WHERE run_id=NEW.run_id;
 expected=jsonb_array_length(r->'input_ref'->'request'->'scope'->'product_ids')*jsonb_array_length(r->'input_ref'->'request'->'scope'->'stock_location_ids');
 SELECT count(DISTINCT ((v->>'product_id')||':'||(v->>'stock_location_id'))) INTO unique_keys FROM jsonb_array_elements(NEW.output->'items') v;
 IF NOT ((r->>'status'='running' AND NEW.output->'release'=r->'release'
  AND NEW.output->'profile_id'=r->'input_ref'->'request'->'profile_id'
  AND jsonb_array_length(NEW.output->'items')=expected AND unique_keys=expected) IS TRUE) THEN
  RAISE EXCEPTION 'stockout_output_requires_running_complete_binding' USING ERRCODE='23514';
 END IF;
 FOR i IN SELECT value FROM jsonb_array_elements(NEW.output->'items') LOOP
  IF NOT (((i->'product_id') <@ (r->'input_ref'->'request'->'scope'->'product_ids')
   AND (i->'stock_location_id') <@ (r->'input_ref'->'request'->'scope'->'stock_location_ids')
   AND i->'as_of'=r->'input_ref'->'request'->'as_of'
   AND i->>'inference_run_id'=NEW.run_id AND i->'lineage'=r->'input_ref'->'lineage'
   AND i->'generated_at'=NEW.output->'generated_at'
   AND i->'release_id'=r->'release'->'release_id'
   AND i->'model_name'=r->'release'->'binding'->'model_name'
   AND i->'model_version'=r->'release'->'binding'->'model_version'
   AND i->'threshold_version'=r->'release'->'binding'->'approval'->'qualification'->'policy'->'policy_id'
   AND i->>'calibrator_version'='stockout-calibrator-sha256-'||(r->'release'->'binding'->'approval'->'qualification'->'recipe'->'pin'->>'calibrator_sha256')
   AND (i->>'status' IN ('scored','already_stockout','insufficient_data','stale_input'))
   AND ((i->>'status'='scored')=(jsonb_typeof(i->'probability')='number'))
   AND ((i->>'status'='scored')=(jsonb_typeof(i->'risk_band')='string'))) IS TRUE) THEN
   RAISE EXCEPTION 'stockout_output_row_scope_or_pin_mismatch' USING ERRCODE='23514';
  END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER stockout_output_guard BEFORE INSERT ON ai.stockout_batch_outputs FOR EACH ROW EXECUTE FUNCTION ai.stockout_output_guard();
CREATE FUNCTION ai.stockout_batch_completion_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE o jsonb;
BEGIN
 IF NEW.status IN ('succeeded','failed','cancelled') AND NOT EXISTS(SELECT 1 FROM ai.stockout_batch_attempts a WHERE a.run_id=NEW.run_id AND a.attempt=(NEW.record->>'attempt')::integer AND a.record=NEW.record) THEN
  RAISE EXCEPTION 'stockout_batch_completion_requires_history' USING ERRCODE='23514';
 END IF;
 IF NEW.status='succeeded' THEN
  SELECT output INTO o FROM ai.stockout_batch_outputs WHERE run_id=NEW.run_id AND output_id=NEW.record->>'output_id';
  IF NOT ((o->'release'=NEW.record->'release' AND o->'profile_id'=NEW.record->'input_ref'->'request'->'profile_id') IS TRUE) THEN
   RAISE EXCEPTION 'stockout_batch_success_requires_complete_matching_output' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER stockout_batch_completion_guard AFTER UPDATE ON ai.stockout_batch_runs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.stockout_batch_completion_guard();
""")


def downgrade() -> None:
    raise RuntimeError("stockout_job_history_downgrade_requires_backup_restore")
