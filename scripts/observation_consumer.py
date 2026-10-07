"""Consume a bounded authenticated observation batch into the migrated AI database."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from sqlalchemy import create_engine

from retailops_ai.config import load_settings
from retailops_ai.source_replay.broker import BrokerConfig, ObservationRunner, build_client
from retailops_ai.source_replay.store import ObservationStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--broker-config", type=Path, required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--max-messages", type=int, default=100)
    parser.add_argument("--max-seconds", type=int, default=60)
    args = parser.parse_args()
    engine, client = None, None
    try:
        configuration = BrokerConfig.read_private(args.broker_config)
        settings = load_settings(args.env_file)
        if settings.database_url is None:
            raise ValueError("observation_database_required")
        engine = create_engine(
            settings.database_url.get_secret_value(),
            connect_args={"connect_timeout": 3},
            hide_parameters=True,
        )
        client, topology = build_client(configuration, args.group_id)
        runner = ObservationRunner(client, topology, ObservationStore(engine), args.group_id)
        stop = threading.Event()

        def stopped(_signum: int, _frame: object) -> None:
            stop.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, stopped)
        client = None  # runner owns close, including on bounds/assignment/DB failure
        processed = runner.run(
            stop_event=stop, max_messages=args.max_messages, max_seconds=args.max_seconds
        )
        print(json.dumps({"status": "completed", "processed": processed}))
        return 0
    except Exception:
        sys.stderr.write('{"error":"observation_consumer_unavailable"}\n')
        return 2
    finally:
        if client is not None:
            client.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
