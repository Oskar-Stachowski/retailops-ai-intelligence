"""Private-stdin real broker worker killed at SQL/ACK boundaries by the CI drill."""

import json
import sys
import threading
import time

from sqlalchemy import create_engine, event

from retailops_ai.source_replay.broker import BrokerConfig, ObservationRunner, build_client
from retailops_ai.source_replay.store import ObservationStore


def main():
    private = json.load(sys.stdin)
    config = BrokerConfig.model_validate_json(json.dumps(private["broker"]))
    engine = create_engine(private["dsn"], hide_parameters=True)
    client, topology = build_client(config, private["group"])
    phase = private["phase"]

    def ready():
        print("ready", flush=True)
        threading.Event().wait(120)
        raise RuntimeError("observation_child_not_killed")

    class KillStore(ObservationStore):
        def process(self, lease, record):
            result = super().process(lease, record)
            if phase == "after_commit_before_ack":
                ready()
            return result

    def before_commit(conn, cursor, statement, parameters, context, many):
        if "INSERT INTO ai.observation_receipts" in statement:
            ready()

    if phase == "before_commit":
        event.listen(engine, "after_cursor_execute", before_commit)
    runner = ObservationRunner(client, topology, KillStore(engine), private["group"])
    # Pin partition zero for a deterministic kill boundary. Normal eager group
    # subscription is exercised independently by the full three-partition drill.
    runner.assigned(client, [runner._partition(0)])
    deadline = time.monotonic() + 30
    try:
        while time.monotonic() < deadline:
            message = client.poll(0.25)
            if message is not None:
                runner.handle(message)
                raise RuntimeError("observation_child_kill_boundary_missing")
        raise RuntimeError("observation_child_record_missing")
    finally:
        client.close()
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.stderr.write("observation_child_failed\n")
        raise SystemExit(2) from None
