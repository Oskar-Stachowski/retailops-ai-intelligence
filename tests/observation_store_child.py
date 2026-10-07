"""Private stdin configuration; parent kills an actual transaction-owning process."""

import base64
import json
import sys
import time
from uuid import UUID

from sqlalchemy import create_engine, event

from retailops_ai.source_replay.store import Lease, ObservationStore, TransportRecord
from retailops_ai.source_replay.wire import Stream


def main():
    document = json.load(sys.stdin)
    engine = create_engine(
        document["dsn"], hide_parameters=True, connect_args={"connect_timeout": 2}
    )
    data = document["lease"]
    lease = Lease(
        data["group"],
        data["partition"],
        UUID(data["owner"]),
        data["epoch"],
        data["resume_offset"],
        Stream.model_validate(data["stream"]),
    )
    record = TransportRecord(lease.partition, 0, base64.b64decode(document["value"]))

    def pause():
        print("ready", flush=True)
        time.sleep(45)

    if document["phase"] == "before_commit":

        def after_execute(conn, cursor, statement, parameters, context, many):
            if "INSERT INTO ai.observation_receipts" in statement:
                pause()

        event.listen(engine, "after_cursor_execute", after_execute)
    ObservationStore(engine).process(lease, record)
    if document["phase"] == "after_commit_before_ack":
        pause()
    raise RuntimeError("parent_did_not_kill_child")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("child_failed", file=sys.stderr)
        raise SystemExit(2) from None
