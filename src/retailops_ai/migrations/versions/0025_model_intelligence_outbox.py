"""Add durable publication events bound to the native anomaly and stockout results."""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_model_intelligence_outbox"
down_revision: str | None = "0024_ai10_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.model_intelligence_outbox (
 event_id uuid PRIMARY KEY,
 event_type text NOT NULL CHECK(event_type IN ('anomaly_detected','stockout_risk_scored')),
 result_id text NOT NULL,
 anomaly_id text REFERENCES ai.anomaly_results(anomaly_id),
 stockout_output_id text REFERENCES ai.stockout_batch_outputs(output_id),
 environment text NOT NULL CHECK(environment IN ('local','test')),
 topic text NOT NULL CHECK(topic='retailops.intelligence.v2'),
 partition_key text NOT NULL CHECK(partition_key ~ '^[0-9a-f]{64}$'),
 document jsonb NOT NULL CHECK(octet_length(document::text)<=32768),
 created_at timestamptz NOT NULL DEFAULT now(),
 delivered_at timestamptz,
 delivered_partition integer,
 delivered_offset bigint,
 UNIQUE(event_type,result_id),
 CHECK(((event_type='anomaly_detected' AND anomaly_id IS NOT NULL
  AND stockout_output_id IS NULL AND result_id=anomaly_id
  AND document->'payload'->>'anomaly_id'=result_id)
  OR (event_type='stockout_risk_scored' AND stockout_output_id IS NOT NULL
  AND anomaly_id IS NULL AND result_id ~ '^risk-sha256-[0-9a-f]{64}$'
  AND document->'payload'->>'risk_id'=result_id)) IS TRUE),
 CHECK((document->>'event_id'=event_id::text AND document->>'event_type'=event_type
  AND document->>'topic'=topic AND document->>'schema_version'='2.0'
  AND document->>'source'='retailops-ai'
  AND document->'occurred_at'=document->'ingested_at'
  AND document->'occurred_at'=document->'payload'->'generated_at'
  AND document->'correlation_id'=document->'payload'->'inference_run_id') IS TRUE),
 CHECK((delivered_at IS NULL AND delivered_partition IS NULL AND delivered_offset IS NULL)
  OR (delivered_at IS NOT NULL AND delivered_partition IS NOT NULL AND delivered_offset IS NOT NULL
      AND delivered_partition>=0 AND delivered_offset>=0))
);
CREATE INDEX model_intelligence_outbox_pending
 ON ai.model_intelligence_outbox(environment,created_at,event_id) WHERE delivered_at IS NULL;
CREATE FUNCTION ai.model_intelligence_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR
  (to_jsonb(NEW)-ARRAY['delivered_at','delivered_partition','delivered_offset'])
  IS DISTINCT FROM
  (to_jsonb(OLD)-ARRAY['delivered_at','delivered_partition','delivered_offset']) THEN
  RAISE EXCEPTION 'intelligence_event_immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER model_intelligence_event_immutable
 BEFORE UPDATE OR DELETE ON ai.model_intelligence_outbox
 FOR EACH ROW EXECUTE FUNCTION ai.model_intelligence_event_immutable();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ai.model_intelligence_outbox) THEN
  RAISE EXCEPTION 'model_outbox_downgrade_requires_empty_publication';
 END IF;
END $$;
DROP TABLE ai.model_intelligence_outbox;
DROP FUNCTION ai.model_intelligence_event_immutable();
""")
