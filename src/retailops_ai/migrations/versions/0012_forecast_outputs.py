"""Qualified input admission and transactional, partitioned forecast publication."""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_forecast_outputs"
down_revision: str | None = "0011_forecast_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE ai.forecast_batch_runs DROP CONSTRAINT forecast_batch_runs_profile_id_fkey;
CREATE TABLE ai.forecast_output_manifests (
 artifact_id text PRIMARY KEY CHECK(artifact_id ~ '^predictions-sha256-[0-9a-f]{64}$'),
 run_id text NOT NULL UNIQUE REFERENCES ai.forecast_batch_runs(run_id),
 environment text NOT NULL CHECK(environment IN ('test','local')),
 scope_key text NOT NULL CHECK(scope_key ~ '^[0-9a-f]{64}$'),
 manifest jsonb NOT NULL,
 CHECK(octet_length(manifest::text)<=524288),
 CHECK((manifest->>'artifact_id'=artifact_id AND manifest->>'run_id'=run_id
  AND manifest->>'purpose'='qualified_forecast' AND manifest->'forecast_quality_approved'='true'::jsonb
  AND manifest->'complete'='true'::jsonb AND manifest->>'kind'='predictions'
  AND manifest->>'schema_version'='1.0' AND (manifest->>'row_count')::integer BETWEEN 1 AND 1400
  AND jsonb_array_length(manifest->'partitions') BETWEEN 1 AND 6) IS TRUE)
);
CREATE TABLE ai.forecast_output_partitions (
 artifact_id text NOT NULL REFERENCES ai.forecast_output_manifests(artifact_id),
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 5),
 partition jsonb NOT NULL,
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(artifact_id,ordinal),
 CHECK(octet_length(partition::text)<=524288),
 CHECK(((partition->>'ordinal')::integer=ordinal
       AND jsonb_array_length(partition->'predictions') BETWEEN 1 AND 256) IS TRUE)
);
CREATE TABLE ai.forecast_output_heads (
 environment text NOT NULL CHECK(environment IN ('test','local')),
 scope_key text NOT NULL CHECK(scope_key ~ '^[0-9a-f]{64}$'),
 artifact_id text NOT NULL REFERENCES ai.forecast_output_manifests(artifact_id),
 PRIMARY KEY(environment,scope_key)
);
CREATE FUNCTION ai.forecast_output_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r ai.forecast_batch_runs; k text;
BEGIN
 SELECT * INTO r FROM ai.forecast_batch_runs WHERE run_id=NEW.run_id;
 IF (r.environment=NEW.environment AND r.record->>'purpose'='qualified_forecast' AND r.status='running'
     AND r.lease_expires>clock_timestamp() AND r.attempt_deadline>clock_timestamp()
     AND r.run_deadline>clock_timestamp()) IS NOT TRUE THEN
  RAISE EXCEPTION 'forecast_output_requires_live_qualified_run' USING ERRCODE='23514';
 END IF;
 FOREACH k IN ARRAY ARRAY['run_id','release_id','resolved_model','image_digest'] LOOP
  IF NEW.manifest->k IS DISTINCT FROM r.record->k THEN
   RAISE EXCEPTION 'forecast_output_release_binding' USING ERRCODE='23514';
  END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['source_dataset_id','curated_dataset_id','feature_set_id','profile_id','as_of_time','scope'] LOOP
  IF NEW.manifest->k IS DISTINCT FROM r.record->'input_ref'->k THEN
   RAISE EXCEPTION 'forecast_output_input_binding' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF (NEW.manifest->>'horizon_days')::integer IS DISTINCT FROM (SELECT max(value::integer) FROM jsonb_array_elements_text(r.record->'input_ref'->'request'->'horizons_days') AS horizons(value)) THEN
  RAISE EXCEPTION 'forecast_output_horizon_binding' USING ERRCODE='23514';
 END IF;
 IF (NEW.manifest->>'generated_at')::timestamptz>clock_timestamp()
    OR (NEW.manifest->>'generated_at')::timestamptz<(r.record->>'started_at')::timestamptz THEN
  RAISE EXCEPTION 'forecast_output_time_binding' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_output_binding BEFORE INSERT ON ai.forecast_output_manifests
 FOR EACH ROW EXECUTE FUNCTION ai.forecast_output_binding();
CREATE FUNCTION ai.forecast_output_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE m jsonb; r ai.forecast_batch_runs; n integer; unique_n integer; bad integer; receipts integer;
BEGIN
 SELECT o.manifest INTO m FROM ai.forecast_output_manifests o WHERE o.artifact_id=NEW.artifact_id;
 SELECT b.* INTO r FROM ai.forecast_batch_runs b WHERE b.run_id=m->>'run_id';
 IF (r.status='succeeded' AND r.record->'output_ref'->>'artifact_id'=NEW.artifact_id
     AND EXISTS(SELECT 1 FROM ai.forecast_batch_attempts a WHERE a.run_id=r.run_id
                AND a.attempt=(r.record->>'attempt')::integer AND a.record=r.record)) IS NOT TRUE THEN
  RAISE EXCEPTION 'forecast_output_requires_success_and_history' USING ERRCODE='23514';
 END IF;
 SELECT count(*),count(DISTINCT (x->>'product_id',x->>'selling_location_id',x->>'channel',x->>'horizon_days')),
  count(*) FILTER(WHERE ((m->'scope'->'product_ids') ? (x->>'product_id')
   AND (m->'scope'->'selling_location_ids') ? (x->>'selling_location_id')
   AND x->'channel'=m->'scope'->'channel' AND x->'forecast_origin'=m->'as_of_time'
   AND (x->>'horizon_days')::integer BETWEEN 1 AND (m->>'horizon_days')::integer
   AND x->>'business_timezone'='UTC' AND x->>'cutoff_policy'='end_of_day_second_v1'
   AND (x->>'target_date')::date=((m->>'as_of_time')::timestamptz AT TIME ZONE 'UTC')::date+(x->>'horizon_days')::integer
   AND jsonb_typeof(x->'predicted_units')='number' AND (x->>'predicted_units')::numeric>=0) IS NOT TRUE)
 INTO n,unique_n,bad FROM ai.forecast_output_partitions p CROSS JOIN LATERAL jsonb_array_elements(p.partition->'predictions') x
 WHERE p.artifact_id=NEW.artifact_id;
 SELECT count(*) INTO receipts FROM ai.forecast_output_partitions p JOIN LATERAL jsonb_array_elements(m->'partitions') x
  ON (x->>'ordinal')::integer=p.ordinal AND (x->>'rows')::integer=jsonb_array_length(p.partition->'predictions')
     AND x->>'sha256'=p.sha256 WHERE p.artifact_id=NEW.artifact_id;
 IF n<>(m->>'row_count')::integer OR n<>unique_n OR bad<>0
    OR receipts<>jsonb_array_length(m->'partitions')
    OR n<>jsonb_array_length(m->'scope'->'product_ids')*jsonb_array_length(m->'scope'->'selling_location_ids')*(m->>'horizon_days')::integer THEN
  RAISE EXCEPTION 'forecast_output_incomplete_grain_or_receipts' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER forecast_output_complete AFTER INSERT ON ai.forecast_output_manifests
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.forecast_output_complete();
CREATE CONSTRAINT TRIGGER forecast_partition_complete AFTER INSERT ON ai.forecast_output_partitions
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION ai.forecast_output_complete();
CREATE FUNCTION ai.forecast_output_head_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND (NEW.environment,NEW.scope_key) IS DISTINCT FROM (OLD.environment,OLD.scope_key) THEN
  RAISE EXCEPTION 'forecast_output_head_identity_changed' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM ai.forecast_output_manifests m JOIN ai.forecast_batch_runs r USING(run_id)
    WHERE m.artifact_id=NEW.artifact_id AND m.environment=NEW.environment AND m.scope_key=NEW.scope_key
     AND r.status='succeeded' AND r.record->'output_ref'->>'artifact_id'=NEW.artifact_id) THEN
  RAISE EXCEPTION 'forecast_output_head_requires_complete_success' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_output_head_binding BEFORE INSERT OR UPDATE ON ai.forecast_output_heads
 FOR EACH ROW EXECUTE FUNCTION ai.forecast_output_head_binding();
CREATE OR REPLACE FUNCTION ai.forecast_batch_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k text; release jsonb; profile jsonb;
BEGIN
 SELECT r.release INTO release FROM ai.model_releases r WHERE r.release_id=NEW.release_id;
 IF NEW.record->>'purpose'='lifecycle_mechanics_only' THEN
  SELECT p.profile INTO profile FROM ai.forecast_batch_profiles p WHERE p.profile_id=NEW.profile_id;
  IF (NEW.environment='test' AND release->'binding'->>'model_name'='retailops-demand-forecast-mechanics'
      AND profile->>'environment'='test' AND profile->>'purpose'='lifecycle_mechanics_only') IS NOT TRUE THEN
   RAISE EXCEPTION 'forecast_batch_mechanics_namespace' USING ERRCODE='23514';
  END IF;
 ELSE
  SELECT jsonb_build_object('source_dataset_id',p.profile->'feature_manifest'->'descriptor'->'parent'->'source_dataset_id',
    'curated_dataset_id',p.profile->'feature_manifest'->'descriptor'->'parent'->'curated_dataset_id',
    'feature_set_id',p.profile->'feature_manifest'->'feature_set_id','as_of_time',p.profile->'as_of_time') INTO profile
   FROM ai.forecast_prepared_inputs p WHERE p.profile_id=NEW.profile_id AND p.environment=NEW.environment;
  IF (NEW.record->>'purpose'='qualified_forecast' AND release->'binding'->>'model_name'='retailops-demand-forecast') IS NOT TRUE THEN
   RAISE EXCEPTION 'forecast_batch_qualified_namespace' USING ERRCODE='23514';
  END IF;
 END IF;
 IF (NEW.record->'resolved_model'=release->'binding' AND NEW.record->'image_digest'=release->'image_digest'
     AND NEW.record->'purpose'=release->'binding'->'qualification'->'purpose') IS NOT TRUE THEN
  RAISE EXCEPTION 'forecast_batch_release_profile_binding' USING ERRCODE='23514';
 END IF;
 FOREACH k IN ARRAY ARRAY['source_dataset_id','curated_dataset_id','feature_set_id','as_of_time'] LOOP
  IF profile IS NULL OR NEW.record->'input_ref'->k IS DISTINCT FROM profile->k THEN
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
 IF (NEW.record->>'status')='succeeded' THEN
  IF OLD.lease_expires<=clock_timestamp() OR OLD.attempt_deadline<=clock_timestamp() OR OLD.run_deadline<=clock_timestamp() THEN
   RAISE EXCEPTION 'forecast_batch_expired_publication' USING ERRCODE='23514';
  END IF;
  IF NEW.record->>'purpose'='lifecycle_mechanics_only' THEN
   IF NOT EXISTS(SELECT 1 FROM ai.forecast_mechanics_outputs o WHERE o.run_id=NEW.run_id
    AND o.artifact_id=NEW.record->'output_ref'->>'artifact_id' AND o.output->'release_id'=NEW.record->'release_id'
    AND o.output->'profile_id'=NEW.record->'input_ref'->'profile_id') THEN
    RAISE EXCEPTION 'forecast_batch_requires_persisted_complete_output' USING ERRCODE='23514';
   END IF;
  ELSE
   IF NOT EXISTS(SELECT 1 FROM ai.forecast_output_manifests o WHERE o.run_id=NEW.run_id
    AND o.artifact_id=NEW.record->'output_ref'->>'artifact_id') THEN
    RAISE EXCEPTION 'forecast_batch_requires_persisted_complete_output' USING ERRCODE='23514';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
""")
    for table in ("forecast_output_manifests", "forecast_output_partitions"):
        op.execute(
            f"CREATE TRIGGER model_immutable BEFORE UPDATE OR DELETE ON ai.{table} FOR EACH ROW EXECUTE FUNCTION ai.model_immutable()"
        )


def downgrade() -> None:
    raise RuntimeError("forecast_outputs_downgrade_requires_backup_restore")
