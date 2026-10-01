"""Immutable, complete v12 forecasts, separate from private computation receipts and v1."""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_v12_outputs"
down_revision: str | None = "0016_v12_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.v12_forecast_outputs (
 artifact_id text PRIMARY KEY CHECK(artifact_id ~ '^v12-forecasts-sha256-[0-9a-f]{64}$'),
 run_id text NOT NULL UNIQUE REFERENCES ai.v12_batch_runs(run_id),
 environment text NOT NULL CHECK(environment IN ('local','test')),
 model_name text NOT NULL CHECK(model_name IN ('retailops-demand-forecast-v12','retailops-demand-forecast-v12-mechanics')),
 document jsonb NOT NULL CHECK(octet_length(document::text)<=12582912),
 CHECK((document->>'artifact_id'=artifact_id AND document->>'run_id'=run_id
  AND document->>'environment'=environment AND document->'resolved_model'->>'model_name'=model_name
  AND document->>'version'='forecast-v12-publication-1.0.0'
  AND document->>'purpose'='qualified_v12_forecast' AND document->'complete'='true'::jsonb
  AND jsonb_array_length(document->'rows') BETWEEN 1 AND 1400) IS TRUE),
 CHECK(environment='test' OR model_name='retailops-demand-forecast-v12')
);
CREATE INDEX v12_outputs_environment_model ON ai.v12_forecast_outputs(environment,model_name);
CREATE FUNCTION ai.v12_forecast_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r ai.v12_batch_runs; c jsonb; k text; n integer; u integer; pk integer; bad integer;
BEGIN
 SELECT * INTO r FROM ai.v12_batch_runs WHERE run_id=NEW.run_id;
 SELECT receipt INTO c FROM ai.v12_batch_receipts WHERE run_id=NEW.run_id;
 IF NOT ((r.status='succeeded' AND r.environment=NEW.environment
  AND r.record->'output_id'=c->'artifact_id' AND NEW.document->'receipt_id'=c->'artifact_id'
  AND NEW.document->'predictions_sha256'=c->'predictions_sha256'
  AND EXISTS(SELECT 1 FROM ai.v12_batch_attempts a WHERE a.run_id=r.run_id
   AND a.attempt=(r.record->>'attempt')::integer AND a.record=r.record)
  AND (NEW.document->>'generated_at')::timestamptz >= (r.record->>'completed_at')::timestamptz
  AND (NEW.document->>'generated_at')::timestamptz <= clock_timestamp()) IS TRUE) THEN
  RAISE EXCEPTION 'v12_forecast_requires_complete_computation' USING ERRCODE='23514';
 END IF;
 IF ((NEW.document->'resolved_model'->'approval'->>'reviewed_at')::timestamptz <= clock_timestamp()
  AND (NEW.document->'resolved_model'->'approval'->'qualification'->>'valid_until')::timestamptz > clock_timestamp()) IS NOT TRUE THEN
  RAISE EXCEPTION 'v12_forecast_approval_expired' USING ERRCODE='23514';
 END IF;
 FOREACH k IN ARRAY ARRAY['run_id','release_id','resolved_model','image_digest','environment'] LOOP
  IF NEW.document->k IS DISTINCT FROM r.record->k THEN
   RAISE EXCEPTION 'v12_forecast_release_pin' USING ERRCODE='23514';
  END IF;
 END LOOP;
 FOREACH k IN ARRAY ARRAY['profile_id','source_dataset_id','curated_dataset_id','feature_set_id','as_of_time','scope'] LOOP
  IF NEW.document->k IS DISTINCT FROM r.record->'input_ref'->k THEN
   RAISE EXCEPTION 'v12_forecast_input_pin' USING ERRCODE='23514';
  END IF;
 END LOOP;
 IF NEW.document->'horizon_days' IS DISTINCT FROM c->'horizon_days' THEN
  RAISE EXCEPTION 'v12_forecast_horizon_pin' USING ERRCODE='23514';
 END IF;
 SELECT count(*), count(DISTINCT (x->>'product_id',x->>'selling_location_id',x->>'channel',x->>'horizon_days')),
  count(DISTINCT x->'prediction'->>'key'),
  count(*) FILTER(WHERE ((NEW.document->'scope'->'product_ids') ? (x->>'product_id')
   AND (NEW.document->'scope'->'selling_location_ids') ? (x->>'selling_location_id')
   AND x->'channel'=NEW.document->'scope'->'channel' AND x->'forecast_origin'=NEW.document->'as_of_time'
   AND (x->>'horizon_days')::integer BETWEEN 1 AND (NEW.document->>'horizon_days')::integer
   AND x->>'business_timezone'='UTC' AND x->>'cutoff_policy'='end_of_day_second_v1'
   AND (x->>'target_date')::date=((NEW.document->>'as_of_time')::timestamptz AT TIME ZONE 'UTC')::date+(x->>'horizon_days')::integer
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(c->'parts') part,
     LATERAL jsonb_array_elements(part->'predictions') prediction
     WHERE prediction=x->'prediction' AND part->'profile_id'=x->'execution_profile_id')) IS NOT TRUE)
 INTO n,u,pk,bad FROM jsonb_array_elements(NEW.document->'rows') x;
 IF n<>u OR n<>pk OR bad<>0 OR n<>jsonb_array_length(NEW.document->'scope'->'product_ids')
  *jsonb_array_length(NEW.document->'scope'->'selling_location_ids')*(NEW.document->>'horizon_days')::integer
  OR n<>(SELECT sum(jsonb_array_length(part->'predictions')) FROM jsonb_array_elements(c->'parts') part) THEN
  RAISE EXCEPTION 'v12_forecast_incomplete_or_changed_functionals' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER v12_forecast_binding BEFORE INSERT ON ai.v12_forecast_outputs
 FOR EACH ROW EXECUTE FUNCTION ai.v12_forecast_binding();
CREATE TRIGGER v12_forecast_immutable BEFORE UPDATE OR DELETE ON ai.v12_forecast_outputs
 FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("v12_outputs_downgrade_requires_backup_restore")
