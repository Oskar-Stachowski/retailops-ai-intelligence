"""Deliver a bounded batch from AI's durable outbox using private broker configuration."""

import argparse
import json
import os
import stat
import sys
from importlib import import_module
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.intelligence_events.kafka import ConfluentEventProducer
from retailops_ai.intelligence_events.outbox import EventProducer, deliver_one


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--broker-config", type=Path, required=True)
    parser.add_argument("--max-events", type=int, default=100)
    args = parser.parse_args()
    engine = None
    try:
        if not 1 <= args.max_events <= 1400:
            raise ValueError("intelligence_outbox_batch_limit")
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("intelligence_outbox_database_required")
        fd = os.open(args.broker_config, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("intelligence_broker_config_permissions")
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("intelligence_broker_config_limit")
        config = json.loads(raw)
        if not isinstance(config, dict) or not config.get("bootstrap.servers"):
            raise ValueError("intelligence_broker_config_invalid")
        native = import_module("confluent_kafka").Producer(
            {**config, "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 10000}
        )
        producer = ConfluentEventProducer(cast(EventProducer, native))
        engine = create_engine(
            settings.database_url.get_secret_value(),
            connect_args={"connect_timeout": 3},
            hide_parameters=True,
        )
        delivered = 0
        while delivered < args.max_events and deliver_one(
            engine, producer, environment=settings.app_env
        ):
            delivered += 1
        print(json.dumps({"status": "completed", "delivered": delivered}))
        return 0
    except Exception:
        sys.stderr.write('{"error":"intelligence_outbox_unavailable"}\n')
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
