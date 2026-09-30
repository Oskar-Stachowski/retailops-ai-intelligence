"""Immutable, bounded private inference input registration; no qualified run admission or outputs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_forecast_inputs"
down_revision: str | None = "0010_forecast_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.forecast_prepared_inputs (
 environment text NOT NULL CHECK(environment IN ('local','test')),
 profile_id text NOT NULL CHECK(profile_id ~ '^batch-profile-sha256-[0-9a-f]{64}$'),
 profile jsonb NOT NULL,
 profile_sha256 text NOT NULL CHECK(profile_sha256 ~ '^[0-9a-f]{64}$'),
 storage_bytes integer GENERATED ALWAYS AS (octet_length(profile::text)) STORED NOT NULL,
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment,profile_id),
 CHECK (storage_bytes BETWEEN 1 AND 67108864),
 CHECK ((jsonb_typeof(profile)='object' AND profile->>'profile_id'=profile_id
   AND profile->>'kind'='forecast_inference_inputs' AND profile->>'schema_version'='1.0'
   AND jsonb_typeof(profile->'rows')='array' AND jsonb_typeof(profile->'histories')='array'
   AND jsonb_array_length(profile->'rows') BETWEEN 1 AND 1400
   AND jsonb_array_length(profile->'histories') BETWEEN 1 AND 100
   AND (profile->>'as_of_time')::timestamptz<=registered_at) IS TRUE)
);
CREATE FUNCTION ai.forecast_input_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE existing_count bigint; existing_bytes bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(505050);
 SELECT count(*),coalesce(sum(storage_bytes),0) INTO existing_count,existing_bytes
  FROM ai.forecast_prepared_inputs WHERE environment=NEW.environment;
 IF existing_count>=256 OR existing_bytes+octet_length(NEW.profile::text)>268435456 THEN
  RAISE EXCEPTION 'forecast_input_capacity_exceeded' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER forecast_input_capacity BEFORE INSERT ON ai.forecast_prepared_inputs
 FOR EACH ROW EXECUTE FUNCTION ai.forecast_input_capacity();
CREATE TRIGGER model_immutable BEFORE UPDATE OR DELETE ON ai.forecast_prepared_inputs
 FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("forecast_inputs_downgrade_requires_backup_restore")
