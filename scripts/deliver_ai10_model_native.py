"""Deliver the complete original owned stockout/anomaly SQL outbox using its real publisher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from deliver_ai10_v12_native import private
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.intelligence_events.acceptance_export import event_document
from retailops_ai.intelligence_events.kafka import ConfluentEventProducer
from retailops_ai.intelligence_events.model_outbox import deliver_model_one
from retailops_ai.intelligence_events.outbox import EventProducer


def census(
    rows: list[Any], control: dict[str, Any], *, pending: bool
) -> dict[str, tuple[str, str]]:
    """Bind the entire SQL publication to the sealed canonical census and actual wire serialization."""
    if type(control.get("rows")) is not int or not 1 <= control["rows"] <= 1400:
        raise ValueError("ai10_model_delivery_census_size")
    if len(rows) != control["rows"]:
        raise ValueError("ai10_model_delivery_complete_original_census")
    hashes = {}
    for row in rows:
        event = event_document(row.document)
        identity = str(event.event_id)
        if (
            identity in hashes
            or identity != str(row.event_id)
            or event.event_type != control["kind"]
            or str(event.correlation_id) != control["correlation_id"]
            or row.environment != "test"
            or row.topic != event.topic
            or row.partition_key != event.partition_key
            or (
                pending
                and any(
                    value is not None
                    for value in (row.delivered_at, row.delivered_partition, row.delivered_offset)
                )
            )
            or (
                not pending
                and (
                    row.delivered_at is None
                    or type(row.delivered_partition) is not int
                    or type(row.delivered_offset) is not int
                    or row.delivered_partition < 0
                    or row.delivered_offset < 0
                )
            )
        ):
            raise ValueError("ai10_model_delivery_original_event_binding")
        hashes[identity] = (
            hashlib.sha256(canonical_bytes(event.model_dump(mode="json"))).hexdigest(),
            hashlib.sha256(event.model_dump_json().encode()).hexdigest(),
        )
    if (
        canonical_sha256({key: value[0] for key, value in hashes.items()})
        != control["census_sha256"]
    ):
        raise ValueError("ai10_model_delivery_original_census_digest")
    return hashes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-control", type=Path, required=True)
    parser.add_argument("--broker-control", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    engine = None
    try:
        if (
            os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        ):
            raise ValueError("ai10_model_delivery_owned_runner_required")
        original, broker = private(args.original_control), private(args.broker_control)
        url = make_url(original["database_url"])
        if (
            re.fullmatch(r"[0-9a-f]{32}", str(original.get("owner", ""))) is None
            or original.get("commit") != os.environ.get("GITHUB_SHA")
            or original.get("workflow_run_id") != int(os.environ["GITHUB_RUN_ID"])
            or url.drivername != "postgresql+psycopg"
            or url.host != "127.0.0.1"
            or url.port is None
            or original.get("kind") not in {"anomaly_detected", "stockout_risk_scored"}
            or re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", str(broker.get("bootstrap.servers", "")))
            is None
            or set(broker) != {"bootstrap.servers"}
        ):
            raise ValueError("ai10_model_delivery_exact_owned_context")
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
        query = text("""
SELECT event_id,document,environment,topic,partition_key,delivered_at,delivered_partition,delivered_offset
FROM ai.model_intelligence_outbox WHERE environment='test' ORDER BY event_id LIMIT 1401
""")
        with engine.connect() as connection:
            if (
                connection.scalar(
                    text(
                        "SELECT value FROM ai.service_metadata WHERE name='ai10_native_model_owner'"
                    )
                )
                != original["owner"]
            ):
                raise ValueError("ai10_model_delivery_original_database_owner")
            hashes = census(list(connection.execute(query).all()), original, pending=True)
        native = import_module("confluent_kafka").Producer(
            {
                **broker,
                "enable.idempotence": True,
                "acks": "all",
                "delivery.timeout.ms": 10000,
            }
        )
        producer = ConfluentEventProducer(cast(EventProducer, native))
        count = 0
        while count < original["rows"] and deliver_model_one(engine, producer, environment="test"):
            count += 1
        with engine.connect() as connection:
            delivered = list(connection.execute(query).all())
        if count != original["rows"] or census(delivered, original, pending=False) != hashes:
            raise ValueError("ai10_model_delivery_real_broker_ack_required")
        report = dict(
            status="passed",
            original_AI_database_publisher_attested=True,
            producer_commit=original["commit"],
            workflow_run_id=original["workflow_run_id"],
            census_id=original["census_id"],
            model_kind=original["kind"],
            delivered=count,
            receipts=[
                dict(
                    event_id=str(row.event_id),
                    event_sha256=hashes[str(row.event_id)][0],
                    wire_sha256=hashes[str(row.event_id)][1],
                    partition=row.delivered_partition,
                    offset=row.delivered_offset,
                )
                for row in delivered
            ],
        )
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.write("\n")
        print(
            json.dumps(
                dict(status="passed", delivered=count, original_AI_database_publisher_attested=True)
            )
        )
        return 0
    except Exception as error:
        failure = dict(
            status="failed",
            category="ai10_model_original_outbox_delivery",
            exception_type=type(error).__name__,
        )
        if isinstance(error, ValueError) and re.fullmatch(r"ai10_model_[a-z_]{1,100}", str(error)):
            failure["reason"] = str(error)
        print(json.dumps(failure))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
