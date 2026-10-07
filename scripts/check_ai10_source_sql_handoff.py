"""Receive an actual Source capture and its overlap from the same TLS/SCRAM broker into owned AI SQL."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine, create_engine, text

from retailops_ai.source_replay import Capture, ObservationHistory, Record, Stream
from retailops_ai.source_replay.broker import BrokerConfig, ObservationRunner, build_client
from retailops_ai.source_replay.store import ObservationStore
from retailops_ai.source_replay.wire import canonical
from retailops_ai.source_snapshot.files import read_bytes, read_json

ROOT = Path(__file__).resolve().parents[1]
POSTGRES = (
    "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
)


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def command(executable: str, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603 - resolved executable and fixed owned resource arguments
        [executable, *arguments], capture_output=True, text=True, timeout=90, check=False
    )
    require(result.returncode == 0, "ai10_source_sql_handoff_owned_command_failed")
    return result.stdout.strip()


def receive(
    engine: Engine, config: BrokerConfig, stream: Stream, group: str, count: int
) -> tuple[Capture, list[int], int]:
    client, topology = build_client(config, group)
    runner = ObservationRunner(client, topology, ObservationStore(engine), group)
    replayed = 0
    try:
        runner.assigned(client, [runner._partition(partition) for partition in range(3)])
        require(
            runner.stream == stream and runner.partitions == 3,
            "ai10_source_sql_handoff_exact_broker_identity",
        )
        deadline, received = time.monotonic() + 60, 0
        while received < count:
            require(time.monotonic() < deadline, "ai10_source_sql_handoff_receive_deadline")
            message = client.poll(0.25)
            if message is not None:
                replayed += int(runner.handle(message).replayed)
                received += 1
        captured = runner.store.capture(group, stream=stream, partitions=3)
        committed = client.committed(
            [runner._partition(partition) for partition in range(3)], timeout=5
        )
        require(
            len(committed) == 3
            and {value.partition for value in committed} == {0, 1, 2}
            and all(value.error is None for value in committed),
            "ai10_source_sql_handoff_broker_ack_receipts",
        )
        positions = [
            max(0, partition.offset)
            for partition in sorted(committed, key=lambda value: value.partition)
        ]
        require(
            positions == [value.next_offset for value in captured.boundaries],
            "ai10_source_sql_handoff_actual_sql_ack_vector",
        )
        return captured, positions, replayed
    finally:
        try:
            runner.revoked(
                client, [runner._partition(partition) for partition in list(runner.leases)]
            )
        finally:
            client.close()


def run(args: argparse.Namespace) -> dict[str, Any]:
    require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
        and os.environ.get("RUNNER_OS") == "Linux",
        "ai10_source_sql_handoff_owned_hosted_runner_required",
    )
    docker, git = shutil.which("docker"), shutil.which("git")
    require(docker is not None and git is not None, "ai10_source_sql_handoff_tools_required")
    if docker is None or git is None:
        raise ValueError("ai10_source_sql_handoff_tools_required")
    pin = read_json(ROOT, "docs/reference/ai10-source-capture-producer.json")
    require(
        command(git, "-C", str(ROOT), "rev-parse", "HEAD") == os.environ.get("GITHUB_SHA"),
        "ai10_source_sql_handoff_receiver_commit",
    )
    require(
        command(git, "-C", str(args.source_root.resolve()), "rev-parse", "HEAD") == pin["commit"],
        "ai10_source_sql_handoff_source_commit",
    )
    require(
        not args.report.exists() and not args.report.is_symlink(),
        "ai10_source_sql_handoff_create_only_report",
    )
    config = BrokerConfig.read_private(args.broker_config)
    raw = read_bytes(args.capture.parent, args.capture.name, 16 * 1024**2)
    captured = Capture.model_validate_json(raw)
    wire = read_json(args.replay.parent, args.replay.name)
    stream = Stream.model_validate_json(json.dumps(wire["stream"]))
    records = [Record.model_validate_json(json.dumps(value)) for value in wire["records"]]
    require(
        captured.stream == stream
        and config.source_authority_id == stream.source_authority_id
        and len(captured.boundaries) == 3
        and len(captured.rows) == 2
        and len(captured.receipts) == 3
        and len(records) == 4,
        "ai10_source_sql_handoff_original_complete_prefix",
    )
    reference = (
        ObservationHistory(stream, partitions=3).apply_batch(records, stream=stream).capture()
    )
    require(
        canonical(
            ObservationHistory(stream, partitions=3)
            .apply_batch(records[:3], stream=stream)
            .capture()
        )
        == canonical(captured),
        "ai10_source_sql_handoff_prefix_binding",
    )
    owner = uuid.uuid4().hex
    name, engine, owned = "ai10-source-receiver-" + owner[:12], None, False
    report: dict[str, Any] = dict(status="failed", category="ai10_source_sql_handoff")
    with tempfile.TemporaryDirectory(prefix="ai10-source-receiver-private-") as directory:
        work = Path(directory)
        work.chmod(0o700)
        password = uuid.uuid4().hex + uuid.uuid4().hex
        env_file = work / "database.env"
        env_file.write_text(
            f"POSTGRES_USER=ai10\nPOSTGRES_DB=ai10\nPOSTGRES_PASSWORD={password}\nPOSTGRES_HOST_AUTH_METHOD=scram-sha-256\n"
        )
        env_file.chmod(0o600)
        try:
            command(
                docker,
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "retailops.ai10.source.receiver.owner=" + owner,
                "--env-file",
                str(env_file),
                "-p",
                "127.0.0.1::5432",
                POSTGRES,
            )
            owned = True
            binding = command(docker, "port", name, "5432/tcp")
            require(
                bool(re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", binding)),
                "ai10_source_sql_handoff_loopback_database",
            )
            engine = create_engine(
                f"postgresql+psycopg://ai10:{password}@{binding}/ai10",
                hide_parameters=True,
                connect_args={"connect_timeout": 3},
            )
            deadline = time.monotonic() + 30
            while True:
                try:
                    with engine.connect() as connection:
                        connection.execute(text("SELECT 1"))
                    break
                except Exception:
                    require(time.monotonic() < deadline, "ai10_source_sql_handoff_database_ready")
                    time.sleep(0.1)
            with engine.begin() as connection:
                connection.execute(text("CREATE SCHEMA ai"))
                with Operations.context(MigrationContext.configure(connection)):
                    importlib.import_module(
                        "retailops_ai.migrations.versions.0021_observation_replay"
                    ).upgrade()
            group = "ai10-observation-" + owner
            prefix, prefix_ack, _ = receive(engine, config, stream, group, 3)
            require(
                canonical(prefix) == canonical(captured),
                "ai10_source_sql_handoff_original_source_equals_AI_SQL",
            )
            now = datetime.now(UTC)
            before = ObservationHistory.restore(
                canonical(prefix), stream=stream, partitions=3
            ).quantity_total(now)
            client, _ = build_client(config, group)
            try:
                client.commit(
                    offsets=[ObservationRunner._partition(partition, 0) for partition in range(3)],
                    asynchronous=False,
                )
            finally:
                client.close()
            resumed, final_ack, replayed = receive(engine, config, stream, group, 4)
            full, full_ack, _ = receive(engine, config, stream, "ai10-observation-full-" + owner, 4)
            after = ObservationHistory.restore(
                canonical(resumed), stream=stream, partitions=3
            ).quantity_total(now)
            require(
                canonical(resumed) == canonical(full) == canonical(reference)
                and before == 4
                and after == 7
                and replayed == 3
                and final_ack == full_ack,
                "ai10_source_sql_handoff_overlap_correction_full_equality",
            )
            with engine.connect() as connection:
                count = connection.execute(
                    text(
                        "SELECT fact_count,receipt_count FROM ai.observation_streams WHERE group_id=:g"
                    ),
                    dict(g=group),
                ).one()
            require(tuple(count) == (3, 4), "ai10_source_sql_handoff_no_duplicate_SQL_effect")
            report = dict(
                status="passed",
                scope="original_Source_SQL_TLS_SCRAM_capture_to_same_broker_actual_AI_SQL_and_ACK",
                source_commit=pin["commit"],
                receiver_commit=os.environ["GITHUB_SHA"],
                workflow_run_id=int(os.environ["GITHUB_RUN_ID"]),
                capture_id=captured.capture_id,
                capture_sha256=hashlib.sha256(raw).hexdigest(),
                prefix_AI_capture_id=prefix.capture_id,
                final_AI_capture_id=resumed.capture_id,
                actual_Source_SQL_TLS_SCRAM=True,
                actual_AI_SQL_or_ACK=True,
                partitions=3,
                prefix_ACK=prefix_ack,
                final_ACK=final_ack,
                prefix_facts=2,
                prefix_receipts=3,
                final_facts=3,
                final_receipts=4,
                actual_original_broker_overlap_records=replayed,
                full_equals_capture_overlap=True,
                quantity_before_correction=before,
                quantity_after_correction=after,
                full_43_table_SQL_snapshot=False,
                model_qualification=False,
                acceptance_inputs="explicit generated observation fixtures; operational original Source publisher executed",
            )
        finally:
            if engine is not None:
                engine.dispose()
            if owned:
                require(
                    command(
                        docker,
                        "inspect",
                        "--format",
                        '{{ index .Config.Labels "retailops.ai10.source.receiver.owner" }}',
                        name,
                    )
                    == owner,
                    "ai10_source_sql_handoff_cleanup_owner",
                )
                command(docker, "rm", "-fv", name)
    fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("broker-config", "capture", "replay", "source-root", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        result = run(parser.parse_args())
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "status",
                        "scope",
                        "quantity_before_correction",
                        "quantity_after_correction",
                    )
                }
            )
        )
        return 0
    except Exception:
        print('{"status":"failed","category":"ai10_source_sql_handoff"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
