"""Accepted model publications and their outbox commit in the same SQL transaction."""

import json
from collections.abc import Sequence

from sqlalchemy import Connection, Engine, text

from retailops_ai.anomaly_portfolio.serving_contract import Item
from retailops_ai.intelligence_events.model_contracts import (
    AnomalyDetected,
    ModelEvent,
    StockoutRiskScored,
    anomaly_event,
    stockout_event,
)
from retailops_ai.intelligence_events.outbox import DeliveryMessage, EventProducer
from retailops_ai.stockout_jobs.batch import StockoutOutput


def _environment(environment: str) -> None:
    if environment not in {"local", "test"}:
        raise ValueError("intelligence_outbox_environment")


def _enqueue(
    connection: Connection,
    event: ModelEvent,
    *,
    environment: str,
    stockout_output_id: str | None = None,
) -> None:
    _environment(environment)
    result_id = (
        event.payload.anomaly_id if isinstance(event, AnomalyDetected) else event.payload.risk_id
    )
    anomaly_id = result_id if isinstance(event, AnomalyDetected) else None
    values = dict(
        id=str(event.event_id),
        kind=event.event_type,
        result=result_id,
        anomaly=anomaly_id,
        output=stockout_output_id,
        environment=environment,
        topic=event.topic,
        key=event.partition_key,
        document=event.model_dump_json(),
    )
    connection.execute(
        text("""
INSERT INTO ai.model_intelligence_outbox
 (event_id,event_type,result_id,anomaly_id,stockout_output_id,environment,topic,partition_key,document)
VALUES (:id,:kind,:result,:anomaly,:output,:environment,:topic,:key,CAST(:document AS jsonb))
ON CONFLICT(event_id) DO NOTHING
"""),
        values,
    )
    stored = (
        connection.execute(
            text("""
SELECT document,environment,anomaly_id,stockout_output_id,partition_key
FROM ai.model_intelligence_outbox WHERE event_id=:id
"""),
            dict(id=values["id"]),
        )
        .mappings()
        .one()
    )
    if dict(stored) != dict(
        document=json.loads(event.model_dump_json()),
        environment=environment,
        anomaly_id=anomaly_id,
        stockout_output_id=stockout_output_id,
        partition_key=event.partition_key,
    ):
        raise ValueError("intelligence_outbox_identity_collision")


def enqueue_anomalies(connection: Connection, items: Sequence[Item], *, environment: str) -> int:
    for item in items:
        _enqueue(connection, anomaly_event(item), environment=environment)
    return len(items)


def enqueue_stockout(connection: Connection, output: StockoutOutput, *, environment: str) -> int:
    output = StockoutOutput.model_validate_json(output.model_dump_json())
    for item in output.items:
        _enqueue(
            connection,
            stockout_event(item),
            environment=environment,
            stockout_output_id=output.output_id,
        )
    return len(output.items)


def deliver_model_one(engine: Engine, producer: EventProducer, *, environment: str) -> bool:
    """Broker ACK before SQL receipt; a crash repeats the original immutable event."""
    _environment(environment)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout='3s'"))
        connection.execute(text("SET LOCAL statement_timeout='3s'"))
        row = (
            connection.execute(
                text("""
SELECT event_id,event_type,result_id,anomaly_id,stockout_output_id,document,topic,partition_key
FROM ai.model_intelligence_outbox WHERE environment=:environment AND delivered_at IS NULL
ORDER BY created_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED
"""),
                dict(environment=environment),
            )
            .mappings()
            .first()
        )
        if row is None:
            return False
        expected: ModelEvent
        if row["event_type"] == "anomaly_detected":
            event: ModelEvent = AnomalyDetected.model_validate_json(json.dumps(row["document"]))
            native = connection.scalar(
                text("SELECT record FROM ai.anomaly_results WHERE anomaly_id=:id"),
                dict(id=row["anomaly_id"]),
            )
            anomaly = anomaly_event(Item.model_validate_json(json.dumps(native)))
            expected, result_id = anomaly, anomaly.payload.anomaly_id
        elif row["event_type"] == "stockout_risk_scored":
            event = StockoutRiskScored.model_validate_json(json.dumps(row["document"]))
            native = connection.scalar(
                text("SELECT output FROM ai.stockout_batch_outputs WHERE output_id=:id"),
                dict(id=row["stockout_output_id"]),
            )
            output = StockoutOutput.model_validate_json(json.dumps(native))
            found = [item for item in output.items if item.risk_id == row["result_id"]]
            if len(found) != 1:
                raise ValueError("intelligence_outbox_native_result_binding")
            risk = stockout_event(found[0])
            expected, result_id = risk, risk.payload.risk_id
        else:
            raise ValueError("intelligence_model_event_type")
        if (
            event != expected
            or str(event.event_id) != str(row["event_id"])
            or result_id != row["result_id"]
            or event.partition_key != row["partition_key"]
            or event.topic != row["topic"]
        ):
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
UPDATE ai.model_intelligence_outbox SET delivered_at=now(),delivered_partition=:partition,
 delivered_offset=:offset WHERE event_id=:id
"""),
            dict(id=row["event_id"], partition=partition, offset=offset),
        )
        return True
