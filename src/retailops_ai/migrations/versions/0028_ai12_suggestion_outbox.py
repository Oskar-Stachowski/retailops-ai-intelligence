"""Add bounded Assistant delivery without changing historical migrations."""

from collections.abc import Sequence

from alembic import op

revision: str = "0028_ai12_suggestion_outbox"
down_revision: str | None = "0027_ai10_ai12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE ai.assistant_suggestion_outbox (
 event_id uuid PRIMARY KEY,
 recommendation_id uuid NOT NULL UNIQUE,
 answer_id uuid NOT NULL,
 trace_id uuid NOT NULL,
 environment text NOT NULL CHECK(environment IN ('local','test')),
 topic text NOT NULL DEFAULT 'retailops.intelligence.v2' CHECK(topic='retailops.intelligence.v2'),
 partition_key text NOT NULL CHECK(partition_key ~ '^[0-9a-f]{64}$'),
 document jsonb NOT NULL,
 wire_bytes bytea NOT NULL CHECK(octet_length(wire_bytes) BETWEEN 1 AND 32768),
 wire_sha256 text NOT NULL CHECK(wire_sha256 ~ '^[0-9a-f]{64}$'),
 expires_at timestamptz NOT NULL,
 queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 retain_until timestamptz NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','delivered','expired')),
 completed_at timestamptz,
 delivered_partition integer,
 delivered_offset bigint,
 CHECK((convert_from(wire_bytes,'UTF8')::jsonb=document
  AND encode(sha256(wire_bytes),'hex')=wire_sha256) IS TRUE),
 CHECK((document->>'event_id'=event_id::text
  AND document->>'event_type'='recommendation_generated'
  AND document->>'schema_version'='2.0' AND document->>'topic'=topic
  AND document->>'source'='retailops-ai'
  AND document->>'correlation_id'=trace_id::text
  AND document->'occurred_at'=document->'ingested_at'
  AND document->'occurred_at'=document->'payload'->'created_at'
  AND document->'payload'->>'recommendation_id'=recommendation_id::text
  AND document->'payload'->>'answer_id'=answer_id::text
  AND document->'payload'->>'trace_id'=trace_id::text
  AND document->'payload'->>'origin'='retailops-ai'
  AND document->'payload'->>'policy_version'='read-only-review-v1'
  AND NOT (document->'payload' ? 'evidence_observed_at')
  AND document->'payload'->>'status'='proposed'
  AND document->'payload'->'requires_human_review'='true'::jsonb
  AND (document->'payload'->>'expires_at')::timestamptz=expires_at
  AND (document->'payload'->>'source_as_of')::timestamptz
      <=(document->'payload'->>'created_at')::timestamptz
  AND (document->'payload'->>'created_at')::timestamptz<expires_at
  AND expires_at<=(document->'payload'->>'source_as_of')::timestamptz+interval '300 seconds'
  AND retain_until=(document->'payload'->>'created_at')::timestamptz+interval '900 seconds') IS TRUE),
 CHECK((status='pending' AND completed_at IS NULL
        AND delivered_partition IS NULL AND delivered_offset IS NULL)
  OR (status='expired' AND completed_at IS NOT NULL
        AND delivered_partition IS NULL AND delivered_offset IS NULL)
  OR (status='delivered' AND completed_at IS NOT NULL
        AND delivered_partition IS NOT NULL AND delivered_offset IS NOT NULL
        AND delivered_partition>=0 AND delivered_offset>=0)),
 CHECK(completed_at IS NULL OR completed_at>=queued_at)
);
CREATE INDEX assistant_suggestion_outbox_pending
 ON ai.assistant_suggestion_outbox(environment,queued_at,event_id) WHERE status='pending';
CREATE FUNCTION ai.assistant_suggestion_delivery_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' THEN
  PERFORM pg_advisory_xact_lock(384790023);
  IF NEW.status<>'pending' OR (NOT EXISTS(SELECT 1 FROM ai.assistant_suggestion_outbox
      WHERE event_id=NEW.event_id) AND (NEW.expires_at<=clock_timestamp() OR
   (SELECT count(*) FROM ai.assistant_suggestion_outbox WHERE environment=NEW.environment)>=1000
   OR NOT EXISTS(SELECT 1 FROM ai.assistant_suggestions s
    JOIN ai.assistant_runs r USING(trace_id)
    JOIN ai.assistant_answers a ON (a.answer_id,a.trace_id)=(s.answer_id,s.trace_id)
    WHERE s.recommendation_id=NEW.recommendation_id AND s.answer_id=NEW.answer_id
     AND s.trace_id=NEW.trace_id AND r.environment=NEW.environment AND r.status='succeeded'
     AND s.record=NEW.document->'payload'
     AND r.record->>'agent_config_version'=s.record->>'agent_config_version'))) THEN
   RAISE EXCEPTION 'suggestion_outbox_origin_or_capacity' USING ERRCODE='23514';
  END IF;
 ELSIF TG_OP='DELETE' THEN
  IF OLD.status='pending' OR OLD.retain_until>clock_timestamp() THEN
   RAISE EXCEPTION 'suggestion_outbox_retention' USING ERRCODE='23514';
  END IF;
 ELSE
  IF OLD.status<>'pending' OR NEW.status NOT IN ('delivered','expired') OR
   (to_jsonb(NEW)-ARRAY['status','completed_at','delivered_partition','delivered_offset'])
    IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['status','completed_at','delivered_partition','delivered_offset'])
   OR (NEW.status='expired' AND OLD.expires_at>clock_timestamp()) THEN
   RAISE EXCEPTION 'suggestion_outbox_illegal_transition' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER assistant_suggestion_delivery_guard BEFORE INSERT OR UPDATE OR DELETE
 ON ai.assistant_suggestion_outbox FOR EACH ROW EXECUTE FUNCTION ai.assistant_suggestion_delivery_guard();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM ai.assistant_suggestion_outbox) THEN
  RAISE EXCEPTION 'suggestion_outbox_downgrade_requires_empty';
 END IF;
END $$;
DROP TABLE ai.assistant_suggestion_outbox;
DROP FUNCTION ai.assistant_suggestion_delivery_guard();
""")
