"""Deliver the original owned AI10 v12 SQL outbox to the consumer's owned loopback broker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from sqlalchemy import create_engine, text

from retailops_ai.intelligence_events.contracts import ForecastGenerated
from retailops_ai.intelligence_events.kafka import ConfluentEventProducer
from retailops_ai.intelligence_events.outbox import EventProducer, deliver_one


def failure_summary(error: Exception) -> dict[str, str]:
    """Expose fixed delivery guards, without database messages or private controls."""
    allowed = {
        "ai10_v12_delivery_private_control_required",
        "ai10_v12_delivery_control_byte_limit",
        "ai10_v12_delivery_control_invalid",
        "ai10_v12_delivery_owned_runner_required",
        "ai10_v12_delivery_exact_owned_context",
        "ai10_v12_delivery_original_database_owner",
        "ai10_v12_delivery_complete_original_pending_census",
        "ai10_v12_delivery_real_broker_ack_required",
        "intelligence_outbox_environment",
        "intelligence_outbox_stored_binding",
        "intelligence_outbox_delivery_unconfirmed",
        "intelligence_outbox_delivery_position_invalid",
    }
    category = str(error) if isinstance(error, ValueError | RuntimeError) else ""
    return {
        "status": "failed",
        "category": category if category in allowed else "ai10_v12_original_outbox_delivery",
        "exception_type": type(error).__name__,
    }


def private(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("ai10_v12_delivery_private_control_required")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("ai10_v12_delivery_control_byte_limit")
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("ai10_v12_delivery_control_invalid")
    return document


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
            raise ValueError("ai10_v12_delivery_owned_runner_required")
        original, broker = private(args.original_control), private(args.broker_control)
        if (
            re.fullmatch(r"[0-9a-f]{32}", str(original.get("owner", ""))) is None
            or original.get("commit") != os.environ.get("GITHUB_SHA")
            or original.get("workflow_run_id") != int(os.environ["GITHUB_RUN_ID"])
            or re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", str(broker.get("bootstrap.servers", "")))
            is None
            or set(broker) != {"bootstrap.servers"}
        ):
            raise ValueError("ai10_v12_delivery_exact_owned_context")
        engine = create_engine(
            original["database_url"], hide_parameters=True, connect_args={"connect_timeout": 3}
        )
        with engine.connect() as connection:
            if (
                connection.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='ai10_v12_native_owner'")
                )
                != original["owner"]
            ):
                raise ValueError("ai10_v12_delivery_original_database_owner")
            rows = connection.execute(
                text(
                    "SELECT event_id,document,artifact_id,delivered_at FROM ai.intelligence_outbox WHERE environment='test' ORDER BY event_id"
                )
            ).all()
        events = [ForecastGenerated.model_validate_json(json.dumps(row.document)) for row in rows]
        census = {
            str(value.event_id): hashlib.sha256(value.model_dump_json().encode()).hexdigest()
            for value in events
        }
        if (
            len(rows) != 56
            or any(
                row.artifact_id != original["publication_id"] or row.delivered_at is not None
                for row in rows
            )
            or census != original["event_sha256"]
        ):
            raise ValueError("ai10_v12_delivery_complete_original_pending_census")
        native = import_module("confluent_kafka").Producer(
            {**broker, "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 10000}
        )
        producer = ConfluentEventProducer(cast(EventProducer, native))
        count = 0
        while count < 56 and deliver_one(engine, producer, environment="test"):
            count += 1
        with engine.connect() as connection:
            delivered = connection.execute(
                text(
                    "SELECT event_id,document,delivered_at,delivered_partition,delivered_offset FROM ai.intelligence_outbox WHERE environment='test' ORDER BY event_id"
                )
            ).all()
        if count != 56 or any(
            row.delivered_at is None
            or row.delivered_partition is None
            or row.delivered_offset is None
            for row in delivered
        ):
            raise ValueError("ai10_v12_delivery_real_broker_ack_required")
        report = dict(
            status="passed",
            original_AI_database_publisher_attested=True,
            producer_commit=original["commit"],
            workflow_run_id=original["workflow_run_id"],
            publication_id=original["publication_id"],
            delivered=count,
            receipts=[
                dict(
                    event_id=str(row.event_id),
                    event_sha256=census[str(row.event_id)],
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
        print(json.dumps(failure_summary(error)))
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
