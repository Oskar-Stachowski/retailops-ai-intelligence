"""SQL outbox joins publication's transaction; broker delivery can safely repeat."""

import json
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import Connection, Engine, text

from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt, V12BatchRun
from retailops_ai.forecast_jobs.v12_publication import V12Publication
from retailops_ai.intelligence_events.contracts import ForecastGenerated, forecast_events


def enqueue_forecasts(
    connection: Connection, output: V12Publication, run: V12BatchRun, receipt: V12BatchReceipt
) -> int:
    events = forecast_events(output, run, receipt)
    for event in events:
        raw = event.model_dump_json()
        connection.execute(
            text("""
INSERT INTO ai.intelligence_outbox(event_id,artifact_id,environment,topic,partition_key,document)
VALUES (:id,:artifact,:env,:topic,:key,CAST(:document AS jsonb))
ON CONFLICT(event_id) DO NOTHING
"""),
            dict(
                id=str(event.event_id),
                artifact=output.artifact_id,
                env=output.environment,
                topic=event.topic,
                key=event.partition_key,
                document=raw,
            ),
        )
        stored = connection.scalar(
            text("SELECT document FROM ai.intelligence_outbox WHERE event_id=:id"),
            dict(id=str(event.event_id)),
        )
        if stored != json.loads(raw):
            raise ValueError("intelligence_outbox_identity_collision")
    return len(events)


class DeliveryMessage(Protocol):
    def topic(self) -> str | None: ...
    def partition(self) -> int | None: ...
    def offset(self) -> int | None: ...


class EventProducer(Protocol):
    def produce(
        self,
        topic: str,
        *,
        key: bytes,
        value: bytes,
        on_delivery: Callable[[object, DeliveryMessage], None],
    ) -> None: ...

    def flush(self, timeout: float) -> int: ...


def deliver_one(engine: Engine, producer: EventProducer, *, environment: str) -> bool:
    """Hold one row lock through a bounded delivery; crash leaves a safe duplicate for retry."""
    if environment not in {"local", "test"}:
        raise ValueError("intelligence_outbox_environment")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout='3s'"))
        row = connection.execute(
            text("""
SELECT event_id,document,partition_key FROM ai.intelligence_outbox
WHERE environment=:env AND delivered_at IS NULL
ORDER BY created_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED
"""),
            dict(env=environment),
        ).first()
        if row is None:
            return False
        event = ForecastGenerated.model_validate_json(json.dumps(row.document))
        if str(event.event_id) != str(row.event_id) or event.partition_key != row.partition_key:
            raise ValueError("intelligence_outbox_stored_binding")
        receipts: list[tuple[object, DeliveryMessage]] = []
        producer.produce(
            event.topic,
            key=event.partition_key.encode(),
            value=event.model_dump_json().encode(),
            on_delivery=lambda error, message: receipts.append((error, message)),
        )
        remaining = producer.flush(15)
        if remaining or len(receipts) != 1 or receipts[0][0] is not None:
            raise RuntimeError("intelligence_outbox_delivery_unconfirmed")
        message = receipts[0][1]
        partition, offset = message.partition(), message.offset()
        if (
            message.topic() != event.topic
            or partition is None
            or offset is None
            or partition < 0
            or offset < 0
        ):
            raise RuntimeError("intelligence_outbox_delivery_position_invalid")
        connection.execute(
            text("""
UPDATE ai.intelligence_outbox SET delivered_at=now(),delivered_partition=:partition,
 delivered_offset=:offset WHERE event_id=:id
"""),
            dict(id=row.event_id, partition=partition, offset=offset),
        )
        return True
