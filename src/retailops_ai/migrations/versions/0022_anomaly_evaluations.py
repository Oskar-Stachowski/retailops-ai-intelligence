"""Immutable anomaly quality projections bound to enrolled model versions."""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_anomaly_evaluations"
down_revision: str | None = "0021_anomaly_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.anomaly_evaluations (
 evaluation_id text PRIMARY KEY CHECK(evaluation_id ~ '^anomaly-quality-sha256-[0-9a-f]{64}$'),
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 model_name text NOT NULL DEFAULT 'retailops-sales-anomaly' CHECK(model_name='retailops-sales-anomaly'),
 model_version text NOT NULL,
 evidence jsonb NOT NULL,
 registered_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(model_name,model_version) REFERENCES ai.anomaly_model_versions(model_name,model_version),
 CHECK((evidence->>'evidence_sha256'=evidence_sha256
 AND evidence->'descriptor'->>'evaluation_id'=evaluation_id
 AND evidence->'descriptor'->>'model_name'=model_name
 AND evidence->'descriptor'->>'registered_model_version'=model_version
 AND evidence->'descriptor'->>'quality_status'='passed') IS TRUE)
);
CREATE TRIGGER anomaly_evaluation_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_evaluations
 FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("anomaly_evaluation_downgrade_requires_backup_restore")
