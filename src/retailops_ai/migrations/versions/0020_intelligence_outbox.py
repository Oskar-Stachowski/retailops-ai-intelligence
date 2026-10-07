"""Immutable publication events and bounded, at-least-once delivery receipts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_intelligence_outbox"
down_revision: str | None = "0019_v12_development"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.intelligence_outbox (
 event_id uuid PRIMARY KEY,
 artifact_id text NOT NULL REFERENCES ai.v12_forecast_outputs(artifact_id),
 environment text NOT NULL CHECK(environment IN ('local','test')),
 topic text NOT NULL CHECK(topic='retailops.intelligence.v2'),
 partition_key text NOT NULL,
 document jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 delivered_at timestamptz,
 delivered_partition integer,
 delivered_offset bigint,
 CHECK (octet_length(document::text) <= 32768),
 CHECK ((delivered_at IS NULL AND delivered_partition IS NULL AND delivered_offset IS NULL)
     OR (delivered_at IS NOT NULL AND delivered_partition >= 0 AND delivered_offset >= 0))
);
CREATE INDEX intelligence_outbox_pending ON ai.intelligence_outbox(environment,created_at,event_id)
 WHERE delivered_at IS NULL;
""")


def downgrade() -> None:
    op.execute("DROP TABLE ai.intelligence_outbox")
