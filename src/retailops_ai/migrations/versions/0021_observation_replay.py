"""Bounded AI observation projection, durable transport and immutable SQL captures."""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_observation_replay"
down_revision: str | None = "0020_intelligence_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.observation_streams (
 group_id text PRIMARY KEY CHECK (length(group_id) BETWEEN 1 AND 128),
 stream jsonb NOT NULL,
 partitions integer NOT NULL CHECK (partitions BETWEEN 1 AND 32),
 fact_count integer NOT NULL DEFAULT 0 CHECK (fact_count BETWEEN 0 AND 10000),
 receipt_count integer NOT NULL DEFAULT 0 CHECK (receipt_count BETWEEN 0 AND 20000)
);
CREATE TABLE ai.observation_partitions (
 group_id text NOT NULL REFERENCES ai.observation_streams(group_id),
 partition integer NOT NULL CHECK (partition BETWEEN 0 AND 31),
 epoch bigint NOT NULL DEFAULT 0 CHECK (epoch >= 0),
 owner uuid,
 next_offset bigint NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
 PRIMARY KEY(group_id,partition)
);
CREATE TABLE ai.observation_identities (
 group_id text NOT NULL REFERENCES ai.observation_streams(group_id),
 observation_id uuid NOT NULL,
 grain jsonb NOT NULL,
 grain_sha256 text NOT NULL CHECK (grain_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(group_id,observation_id),
 UNIQUE(group_id,grain_sha256)
);
CREATE TABLE ai.observation_versions (
 group_id text NOT NULL,
 observation_id uuid NOT NULL,
 version bigint NOT NULL CHECK (version > 0),
 row_id uuid NOT NULL,
 available_at timestamptz NOT NULL,
 fact_sha256 text NOT NULL CHECK (fact_sha256 ~ '^[0-9a-f]{64}$'),
 document jsonb NOT NULL CHECK (octet_length(document::text) <= 32768),
 PRIMARY KEY(group_id,observation_id,version),
 UNIQUE(group_id,row_id),
 UNIQUE(group_id,fact_sha256),
 FOREIGN KEY(group_id,observation_id)
  REFERENCES ai.observation_identities(group_id,observation_id)
);
CREATE TABLE ai.observation_receipts (
 group_id text NOT NULL,
 partition integer NOT NULL,
 offset_value bigint NOT NULL CHECK (offset_value >= 0 AND offset_value < 9223372036854775807),
 transport_sha256 text NOT NULL CHECK (transport_sha256 ~ '^[0-9a-f]{64}$'),
 raw_value bytea CHECK (octet_length(raw_value) <= 32768),
 transport jsonb NOT NULL CHECK (octet_length(transport::text) <= 16384),
 event_id uuid,
 event_sha256 text CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
 fact_sha256 text CHECK (fact_sha256 ~ '^[0-9a-f]{64}$'),
 outcome text NOT NULL CHECK (outcome IN ('inserted','duplicate','quarantined')),
 reason text,
 PRIMARY KEY(group_id,partition,offset_value),
 FOREIGN KEY(group_id,partition) REFERENCES ai.observation_partitions(group_id,partition),
 FOREIGN KEY(group_id,fact_sha256) REFERENCES ai.observation_versions(group_id,fact_sha256),
 CHECK ((outcome='quarantined' AND reason IS NOT NULL
         AND event_id IS NULL AND event_sha256 IS NULL AND fact_sha256 IS NULL)
     OR (outcome IN ('inserted','duplicate') AND reason IS NULL
         AND event_id IS NOT NULL AND event_sha256 IS NOT NULL AND fact_sha256 IS NOT NULL))
);
CREATE INDEX observation_event_identity ON ai.observation_receipts(group_id,event_id)
 WHERE event_id IS NOT NULL;
CREATE TABLE ai.observation_captures (
 group_id text NOT NULL REFERENCES ai.observation_streams(group_id),
 capture_id text NOT NULL CHECK (capture_id ~ '^observation-capture-sha256-[0-9a-f]{64}$'),
 captured_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 capture_sha256 text NOT NULL CHECK (capture_sha256 ~ '^[0-9a-f]{64}$'),
 document bytea NOT NULL CHECK (octet_length(document) <= 16777216),
 PRIMARY KEY(group_id,capture_id)
);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE ai.observation_captures;
DROP TABLE ai.observation_receipts;
DROP TABLE ai.observation_versions;
DROP TABLE ai.observation_identities;
DROP TABLE ai.observation_partitions;
DROP TABLE ai.observation_streams;
""")
