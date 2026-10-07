"""Independent append-only anomaly model decisions, bindings and approved release history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_anomaly_lifecycle"
down_revision: str | None = "0019_v12_development"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.anomaly_model_decisions (
 decision_id text PRIMARY KEY,
 model_name text NOT NULL CHECK (model_name='retailops-sales-anomaly'),
 request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((record->'request'->>'decision_id'=decision_id AND record->'request'->>'model_name'=model_name) IS TRUE)
);
CREATE TABLE ai.anomaly_model_steps (
 decision_id text NOT NULL REFERENCES ai.anomaly_model_decisions(decision_id),
 phase text NOT NULL CHECK (phase IN ('create_attempted','version_bound','aliases_verified','completed')),
 record jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(decision_id,phase)
);
CREATE TABLE ai.anomaly_model_versions (
 model_name text NOT NULL,
 model_version text NOT NULL CHECK (model_version ~ '^[1-9][0-9]{0,9}$'),
 binding jsonb NOT NULL,
 decision_id text NOT NULL REFERENCES ai.anomaly_model_decisions(decision_id),
 PRIMARY KEY(model_name,model_version),
 CHECK ((binding->>'model_name'=model_name AND binding->>'model_version'=model_version) IS TRUE)
);
CREATE TABLE ai.anomaly_model_releases (
 release_id text PRIMARY KEY,
 model_name text NOT NULL,
 model_version text NOT NULL,
 release jsonb NOT NULL,
 decision_id text NOT NULL REFERENCES ai.anomaly_model_decisions(decision_id),
 FOREIGN KEY(model_name,model_version) REFERENCES ai.anomaly_model_versions(model_name,model_version),
 CHECK ((release->>'release_id'=release_id AND release->'binding'->>'model_name'=model_name
 AND release->'binding'->>'model_version'=model_version AND release->>'decision_id'=decision_id) IS TRUE)
);
CREATE TABLE ai.anomaly_model_heads (
 model_name text PRIMARY KEY,
 release_id text NOT NULL REFERENCES ai.anomaly_model_releases(release_id)
);
CREATE FUNCTION ai.anomaly_model_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'model_history_is_immutable' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER anomaly_model_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_model_decisions FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
CREATE TRIGGER anomaly_model_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_model_steps FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
CREATE TRIGGER anomaly_model_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_model_versions FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
CREATE TRIGGER anomaly_model_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_model_releases FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("model_history_downgrade_requires_database_backup_restore")
