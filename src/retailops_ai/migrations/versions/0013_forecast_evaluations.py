"""Immutable bounded development-evaluation evidence, separate from model admission."""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_forecast_evaluations"
down_revision: str | None = "0012_forecast_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.forecast_evaluations (
 environment text NOT NULL CHECK(environment IN ('test','local')),
 evaluation_id text NOT NULL CHECK(evaluation_id ~ '^forecast-quality-sha256-[0-9a-f]{64}$'),
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 evidence jsonb NOT NULL CHECK(octet_length(evidence::text)<=131072),
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment,evaluation_id),
 CHECK((evidence->>'evidence_sha256'=evidence_sha256
  AND evidence->'descriptor'->>'evaluation_id'=evaluation_id
  AND evidence->'descriptor'->>'schema_version'='1.0'
  AND evidence->'descriptor'->>'model_name'='retailops-demand-forecast'
  AND evidence->'descriptor'->'serving_eligible'='false'::jsonb
  AND evidence->'descriptor'->'registered_model_version'='null'::jsonb
  AND evidence->'descriptor'->>'quality_status' IN ('passed','failed','not_ready')
  AND ((evidence->'descriptor'->>'purpose'='historical_development_evidence')
    OR (environment='test' AND evidence->'descriptor'->>'purpose'='synthetic_acceptance_only'))
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'product_ids') BETWEEN 1 AND 10000
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'selling_location_ids') BETWEEN 1 AND 1000
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'channels') BETWEEN 1 AND 2
  AND jsonb_array_length(evidence->'descriptor'->'metrics')=14
  AND (evidence->'descriptor'->>'generated_at')::timestamptz<=registered_at) IS TRUE)
);
CREATE TRIGGER model_immutable BEFORE UPDATE OR DELETE ON ai.forecast_evaluations
 FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("forecast_evaluations_downgrade_requires_backup_restore")
