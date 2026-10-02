"""Local development acceptance is isolated from the normal model namespace and quality."""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_v12_development"
down_revision: str | None = "0018_v12_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE OR REPLACE FUNCTION ai.v12_approved_capsule_valid(c jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
 SELECT (c->>'version'='forecast-v12-inference-release-1.0.0'
 AND c->>'purpose'='qualified_forecast_v12' AND c->'serving_eligible'='true'::jsonb
 AND c->'registered_in_mlflow'='false'::jsonb AND c->'activated_as_champion'='false'::jsonb
 AND c->'qualification'->>'purpose'='serving_load_predict_acceptance'
 AND c->'qualification'->'serving_eligible'='false'::jsonb
 AND ((c->'qualification'->'pin'->>'forecast_model_status'='ready'
       AND c->'qualification'->'development_acceptance' IS NULL)
 OR (c->'qualification'->'pin'->>'forecast_model_status'='not_ready'
       AND c->'qualification'->'development_acceptance'='{"decision": {"sha256": "11cd0e1fdd10629543bcb66aaedb3bd9b7b7fc5b31473575b998d951c93f00d2", "size_bytes": 3513}, "original_quality_reclassified": false, "production_deployment_authorized": false, "scope": "local_development_only", "version": "v12-development-acceptance-1.0.0"}'::jsonb
       AND c->'qualification'->'pin'->>'run_id'='functional-v12-run-sha256-345a725d435a477374292cb9483350fb5c50c8ba87d06668c727e0a9f964fb6b'
       AND c->'qualification'->'pin'->'manifest'->>'sha256'='29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e'))
 AND c->'qualification'->'pin'->'serving_eligible'='false'::jsonb
 AND c->'qualification'->'source_packages_verified'='true'::jsonb
 AND c->'qualification'->'full_export_verified'='true'::jsonb
 AND c->'qualification'->'repeatability_verified'='true'::jsonb
 AND c->'approval'->>'qualification_id'=c->'qualification'->>'qualification_id'
 AND (SELECT count(*) FROM jsonb_object_keys(c->'approval'->'gates'))=10
 AND c->'approval'->'gates' ?& ARRAY['source','features','pit','protocol','segments','signature',
     'resources','security_license','model_card','freshness_drift_compatibility']
 AND NOT EXISTS (SELECT 1 FROM jsonb_each(c->'approval'->'gates') g WHERE g.value->>'status' IS DISTINCT FROM 'passed')
 ) IS TRUE;
$$;

DO $$
DECLARE constraint_record record; definition text; replacement text;
BEGIN
 FOR constraint_record IN
  SELECT c.conname, c.conrelid::regclass AS table_name, pg_get_constraintdef(c.oid) AS definition
  FROM pg_constraint c WHERE c.contype='c' AND c.conrelid IN
   ('ai.v12_model_decisions'::regclass,'ai.v12_forecast_outputs'::regclass,'ai.v12_evaluations'::regclass)
 LOOP
  definition=constraint_record.definition;
  replacement=replace(definition, '''retailops-demand-forecast-v12-mechanics''::text',
   '''retailops-demand-forecast-v12-mechanics''::text, ''retailops-demand-forecast-v12-development''::text');
  replacement=replace(replacement, 'model_name = ''retailops-demand-forecast-v12''::text',
   'model_name = ANY (ARRAY[''retailops-demand-forecast-v12''::text, ''retailops-demand-forecast-v12-development''::text])');
  IF definition IS DISTINCT FROM replacement THEN
   EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',constraint_record.table_name,constraint_record.conname);
   EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s',constraint_record.table_name,constraint_record.conname,replacement);
  END IF;
 END LOOP;
 definition=pg_get_functiondef('ai.v12_batch_guard()'::regprocedure);
 replacement=replace(definition,
  'NEW.record->''resolved_model''->>''model_name''=''retailops-demand-forecast-v12''',
  'NEW.record->''resolved_model''->>''model_name'' IN (''retailops-demand-forecast-v12'',''retailops-demand-forecast-v12-development'')');
 IF definition=replacement THEN RAISE EXCEPTION 'v12_existing_batch_guard_changed'; END IF;
 EXECUTE replacement;
END $$;
ALTER TABLE ai.v12_model_versions ADD CONSTRAINT v12_development_namespace
 CHECK (((model_name='retailops-demand-forecast-v12-development') =
  (binding->'approval'->'qualification'->'development_acceptance' IS NOT NULL)) IS TRUE);
""")


def downgrade() -> None:
    raise RuntimeError("v12_development_history_downgrade_requires_backup_restore")
