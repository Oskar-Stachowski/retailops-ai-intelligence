"""Accept pinned watermark metadata without rewriting immutable 1.0 profiles or outputs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_forecast_freshness"
down_revision: str | None = "0013_forecast_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
DO $$ DECLARE c record;
BEGIN
 FOR c IN SELECT conrelid::regclass relation, conname FROM pg_constraint
  WHERE contype='c' AND conrelid IN ('ai.forecast_prepared_inputs'::regclass,'ai.forecast_output_manifests'::regclass)
   AND pg_get_constraintdef(oid) LIKE '%schema_version%'
 LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',c.relation,c.conname); END LOOP;
END $$;
ALTER TABLE ai.forecast_prepared_inputs ADD CONSTRAINT forecast_input_shape CHECK (
 (jsonb_typeof(profile)='object' AND profile->>'profile_id'=profile_id
  AND profile->>'kind'='forecast_inference_inputs'
  AND profile->>'schema_version' IN ('1.0','1.1')
  AND jsonb_typeof(profile->'rows')='array' AND jsonb_typeof(profile->'histories')='array'
  AND jsonb_array_length(profile->'rows') BETWEEN 1 AND 1400
  AND jsonb_array_length(profile->'histories') BETWEEN 1 AND 100
  AND (profile->>'as_of_time')::timestamptz<=registered_at
  AND ((profile->>'schema_version'='1.0' AND NOT (profile ? 'source_freshness'))
    OR (profile->>'schema_version'='1.1' AND jsonb_typeof(profile->'source_freshness')='object'
     AND (profile->'source_freshness'->'watermark'='null'::jsonb
      OR (profile->'source_freshness'->'watermark'->>'as_of_time')::timestamptz<=registered_at)))) IS TRUE
);
ALTER TABLE ai.forecast_output_manifests ADD CONSTRAINT forecast_output_shape CHECK (
 (manifest->>'artifact_id'=artifact_id AND manifest->>'run_id'=run_id
  AND manifest->>'purpose'='qualified_forecast' AND manifest->'forecast_quality_approved'='true'::jsonb
  AND manifest->'complete'='true'::jsonb AND manifest->>'kind'='predictions'
  AND manifest->>'schema_version' IN ('1.0','1.1') AND (manifest->>'row_count')::integer BETWEEN 1 AND 1400
  AND jsonb_array_length(manifest->'partitions') BETWEEN 1 AND 6
  AND ((manifest->>'schema_version'='1.0' AND NOT (manifest ? 'source_freshness'))
   OR (manifest->>'schema_version'='1.1' AND jsonb_typeof(manifest->'source_freshness')='object'))) IS TRUE
);
CREATE FUNCTION ai.forecast_source_freshness_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; expected jsonb; series jsonb;
BEGIN
 SELECT i.profile INTO p FROM ai.forecast_prepared_inputs i JOIN ai.forecast_batch_runs r
  ON r.environment=i.environment AND r.profile_id=i.profile_id WHERE r.run_id=NEW.run_id;
 IF p->>'schema_version' IS DISTINCT FROM NEW.manifest->>'schema_version' THEN
  RAISE EXCEPTION 'forecast_output_freshness_version_binding' USING ERRCODE='23514';
 END IF;
 IF NEW.manifest->>'schema_version'='1.1' THEN
  SELECT jsonb_agg(x ORDER BY x->>'product_id',x->>'selling_location_id',x->>'channel') INTO series
   FROM jsonb_array_elements(p->'source_freshness'->'observations') x
   WHERE (NEW.manifest->'scope'->'product_ids') ? (x->>'product_id')
    AND (NEW.manifest->'scope'->'selling_location_ids') ? (x->>'selling_location_id')
    AND x->'channel'=NEW.manifest->'scope'->'channel';
  expected=jsonb_set(p->'source_freshness','{observations}',coalesce(series,'[]'::jsonb));
  IF NEW.manifest->'source_freshness' IS DISTINCT FROM expected THEN
   RAISE EXCEPTION 'forecast_output_freshness_input_binding' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_source_freshness_binding BEFORE INSERT ON ai.forecast_output_manifests
 FOR EACH ROW EXECUTE FUNCTION ai.forecast_source_freshness_binding();
""")


def downgrade() -> None:
    raise RuntimeError("forecast_freshness_downgrade_requires_backup_restore")
