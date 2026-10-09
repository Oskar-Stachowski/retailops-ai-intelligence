"""Atomic Assistant enqueue and bounded, repeatable human-review delivery."""

import hashlib
import json
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import Connection, Engine, text
from sqlalchemy.ext.asyncio import AsyncConnection

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.assistant.contracts import PersistedSuggestion
from retailops_ai.intelligence_events.outbox import DeliveryMessage, EventProducer
from retailops_ai.intelligence_events.suggestion_contracts import (
    RecommendationGenerated,
    suggestion_event,
)

OUTBOX_LOCK = 384790023
CAPACITY = 1000
RETENTION_SECONDS = 900


def environment_check(environment: str) -> None:
    if environment not in {"test", "local"}:
        raise ValueError("suggestion_outbox_environment")


MAINTAIN = """
UPDATE ai.assistant_suggestion_outbox SET status='expired',completed_at=clock_timestamp()
WHERE environment=:env AND status='pending' AND expires_at<=clock_timestamp()
"""
PURGE = """
DELETE FROM ai.assistant_suggestion_outbox
WHERE environment=:env AND status<>'pending' AND retain_until<=clock_timestamp()
"""


async def enqueue_suggestions(
    connection: AsyncConnection,
    suggestions: Sequence[PersistedSuggestion],
    *,
    environment: str,
) -> int:
    """Called inside completion's transaction, after its fenced terminal update."""
    environment_check(environment)
    events = [suggestion_event(s) for s in suggestions]
    if not events:
        return 0
    if len(events) > 5 or len({e.event_id for e in events}) != len(events):
        raise ValueError("suggestion_outbox_batch_bound")
    await connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": OUTBOX_LOCK})
    await connection.execute(text(MAINTAIN), {"env": environment})
    await connection.execute(text(PURGE), {"env": environment})
    for event in events:
        raw = event.wire_bytes()
        values = dict(
            id=event.event_id,
            recommendation=event.payload.recommendation_id,
            answer=event.payload.answer_id,
            trace=event.payload.trace_id,
            env=environment,
            key=event.partition_key,
            document=raw.decode(),
            wire=raw,
            digest=hashlib.sha256(raw).hexdigest(),
            expires=event.payload.expires_at,
            retain=event.payload.created_at + timedelta(seconds=RETENTION_SECONDS),
        )
        await connection.execute(
            text("""
INSERT INTO ai.assistant_suggestion_outbox
 (event_id,recommendation_id,answer_id,trace_id,environment,partition_key,document,
  wire_bytes,wire_sha256,expires_at,retain_until)
VALUES (:id,:recommendation,:answer,:trace,:env,:key,CAST(:document AS jsonb),
 :wire,:digest,:expires,:retain)
ON CONFLICT(event_id) DO NOTHING
"""),
            values,
        )
        row = (
            (
                await connection.execute(
                    text("""SELECT document,wire_bytes,environment,partition_key
FROM ai.assistant_suggestion_outbox WHERE event_id=:id"""),
                    {"id": event.event_id},
                )
            )
            .mappings()
            .one()
        )
        if (
            row["document"] != json.loads(raw)
            or bytes(row["wire_bytes"]) != raw
            or row["environment"] != environment
            or row["partition_key"] != event.partition_key
        ):
            raise ValueError("suggestion_outbox_identity_collision")
    return len(events)


def _boundary(connection: Connection) -> None:
    connection.execute(text("SET LOCAL lock_timeout='1s'"))
    connection.execute(text("SET LOCAL statement_timeout='3s'"))
    if (
        connection.scalar(text("SELECT current_user")) != "ai_app"
        or connection.scalar(text("SELECT current_database()")) != "retailops_ai"
        or connection.scalar(text("SELECT version_num FROM ai.alembic_version"))
        != EXPECTED_REVISION
        or connection.scalar(text("SHOW TimeZone")) not in {"UTC", "Etc/UTC"}
    ):
        raise ValueError("suggestion_outbox_database_boundary")


def deliver_suggestion_one(engine: Engine, producer: EventProducer, *, environment: str) -> bool:
    """ACK precedes the SQL receipt. A crash can repeat identical original bytes."""
    environment_check(environment)
    with engine.begin() as connection:
        _boundary(connection)
        connection.execute(text(MAINTAIN), {"env": environment})
        connection.execute(text(PURGE), {"env": environment})
        row = (
            connection.execute(
                text("""SELECT event_id,document,wire_bytes,wire_sha256,partition_key,
 expires_at,clock_timestamp() AS observed_at FROM ai.assistant_suggestion_outbox
 WHERE environment=:env AND status='pending' AND expires_at>clock_timestamp()
 ORDER BY queued_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                {"env": environment},
            )
            .mappings()
            .first()
        )
        if row is None:
            return False
        event = RecommendationGenerated.model_validate_json(json.dumps(row["document"]))
        raw = bytes(row["wire_bytes"])
        if (
            event.event_id != row["event_id"]
            or event.partition_key != row["partition_key"]
            or event.payload.expires_at != row["expires_at"]
            or raw != event.wire_bytes()
            or hashlib.sha256(raw).hexdigest() != row["wire_sha256"]
        ):
            raise ValueError("suggestion_outbox_stored_binding")
        # Validation can consume the last part of a short TTL. Check the SQL
        # clock again before allowing any external call.
        observed_at = connection.scalar(text("SELECT clock_timestamp()"))
        if observed_at >= row["expires_at"]:
            connection.execute(
                text("""UPDATE ai.assistant_suggestion_outbox SET status='expired',
 completed_at=clock_timestamp() WHERE event_id=:id AND status='pending'"""),
                {"id": event.event_id},
            )
            return False
        receipts: list[tuple[object, DeliveryMessage]] = []
        producer.produce(
            event.topic,
            key=event.partition_key.encode(),
            value=raw,
            on_delivery=lambda error, message: receipts.append((error, message)),
        )
        remaining = producer.flush(min(15.0, (row["expires_at"] - observed_at).total_seconds()))
        if remaining or len(receipts) != 1 or receipts[0][0] is not None:
            raise RuntimeError("suggestion_outbox_delivery_unconfirmed")
        message = receipts[0][1]
        partition, offset = message.partition(), message.offset()
        if (
            message.topic() != event.topic
            or partition is None
            or offset is None
            or partition < 0
            or offset < 0
        ):
            raise RuntimeError("suggestion_outbox_delivery_position_invalid")
        connection.execute(
            text("""UPDATE ai.assistant_suggestion_outbox SET status='delivered',
 completed_at=clock_timestamp(),delivered_partition=:partition,delivered_offset=:offset
 WHERE event_id=:id AND status='pending'"""),
            {"id": event.event_id, "partition": partition, "offset": offset},
        )
        return True
