"""Separate immutable, bounded v12 campaign evaluations; no model promotion."""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_v12_evaluations"
down_revision: str | None = "0017_v12_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.v12_evaluations (
 environment text NOT NULL CHECK(environment IN ('test','local')),
 model_name text NOT NULL CHECK(model_name IN ('retailops-demand-forecast-v12','retailops-demand-forecast-v12-mechanics')),
 evaluation_id text NOT NULL CHECK(evaluation_id ~ '^v12-evaluation-sha256-[0-9a-f]{64}$'),
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 evidence jsonb NOT NULL CHECK(octet_length(evidence::text)<=12582912),
 registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(environment,evaluation_id),
 CHECK((evidence->>'version'='forecast-v12-evaluation-evidence-1.0.0'
  AND evidence->>'evaluation_id'=evaluation_id AND evidence->>'evidence_sha256'=evidence_sha256
  AND evidence->'descriptor'->>'model_name'=model_name
  AND evidence->'descriptor'->>'quality_status' IN ('passed','not_ready')
  AND evidence->'descriptor'->'serving_eligible'='false'::jsonb
  AND evidence->'descriptor'->'registered_model_version'='null'::jsonb
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'product_ids') BETWEEN 1 AND 10000
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'selling_location_ids') BETWEEN 1 AND 1000
  AND jsonb_array_length(evidence->'descriptor'->'scope'->'channels') BETWEEN 1 AND 2
  AND (evidence->'descriptor'->>'exported_at')::timestamptz<=registered_at) IS TRUE),
 CHECK(environment='test' OR model_name='retailops-demand-forecast-v12')
);
CREATE INDEX v12_evaluations_environment_model ON ai.v12_evaluations(environment,model_name);
CREATE TRIGGER v12_evaluation_immutable BEFORE UPDATE OR DELETE ON ai.v12_evaluations
 FOR EACH ROW EXECUTE FUNCTION ai.model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("v12_evaluations_downgrade_requires_backup_restore")
