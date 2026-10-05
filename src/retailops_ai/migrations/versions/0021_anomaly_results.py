"""Atomic complete anomaly outputs and independent principal/request idempotency."""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_anomaly_results"
down_revision: str | None = "0020_anomaly_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.anomaly_batches (
 batch_id text PRIMARY KEY CHECK(batch_id ~ '^anomaly-batch-sha256-[0-9a-f]{64}$'),
 release_id text NOT NULL REFERENCES ai.anomaly_model_releases(release_id),
 as_of timestamptz NOT NULL,
 row_count integer NOT NULL CHECK(row_count BETWEEN 1 AND 10000),
 manifest jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((manifest->>'batch_id'=batch_id AND manifest->'descriptor'->>'release_id'=release_id
 AND (manifest->'descriptor'->>'row_count')::integer=row_count
 AND (manifest->'descriptor'->>'as_of')::timestamptz=as_of) IS TRUE)
);
CREATE TABLE ai.anomaly_results (
 anomaly_id text PRIMARY KEY CHECK(anomaly_id ~ '^anomaly-sha256-[0-9a-f]{64}$'),
 batch_id text NOT NULL REFERENCES ai.anomaly_batches(batch_id),
 position integer NOT NULL CHECK(position BETWEEN 0 AND 9999),
 event_type text NOT NULL CHECK(event_type IN ('sale_completed','return_completed')),
 product_id text NOT NULL,
 selling_location_id text NOT NULL,
 channel text NOT NULL CHECK(channel IN ('store','online','marketplace','wholesale')),
 currency text NOT NULL CHECK(currency IN ('PLN','EUR')),
 business_date date NOT NULL,
 scoring_origin timestamptz NOT NULL,
 record jsonb NOT NULL,
 UNIQUE(batch_id,position),
 UNIQUE(batch_id,event_type,product_id,selling_location_id,channel,currency,business_date),
 CHECK((record->>'anomaly_id'=anomaly_id AND record->>'batch_id'=batch_id
 AND record->>'event_type'=event_type AND record->>'product_id'=product_id
 AND record->>'selling_location_id'=selling_location_id AND record->>'channel'=channel
 AND record->>'currency'=currency AND (record->>'business_date')::date=business_date
 AND (record->>'scoring_origin')::timestamptz=scoring_origin
 AND (record->'status'='"scored"'::jsonb OR record->'status'='"insufficient_data"'::jsonb)) IS TRUE),
 CHECK(((record->>'status'='scored' AND record->'input_status'='"ready_input"'::jsonb
 AND jsonb_typeof(record->'score')='number' AND jsonb_typeof(record->'threshold')='number'
 AND jsonb_typeof(record->'alert')='boolean' AND jsonb_typeof(record->'severity')='string')
 OR (record->>'status'='insufficient_data' AND record->'score'='null'::jsonb
 AND record->'threshold'='null'::jsonb AND record->'alert'='null'::jsonb
 AND record->'severity'='null'::jsonb)) IS TRUE)
);
CREATE TABLE ai.anomaly_requests (
 principal_id text NOT NULL,
 request_id text NOT NULL,
 request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 batch_id text NOT NULL REFERENCES ai.anomaly_batches(batch_id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(principal_id,request_id)
);
CREATE INDEX anomaly_result_scope ON ai.anomaly_results(product_id,selling_location_id,channel,business_date,batch_id);
CREATE TRIGGER anomaly_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_batches FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
CREATE TRIGGER anomaly_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_results FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
CREATE TRIGGER anomaly_immutable BEFORE UPDATE OR DELETE ON ai.anomaly_requests FOR EACH ROW EXECUTE FUNCTION ai.anomaly_model_immutable();
""")


def downgrade() -> None:
    raise RuntimeError("anomaly_output_downgrade_requires_backup_restore")
