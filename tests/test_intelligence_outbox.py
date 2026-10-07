"""Real PostgreSQL publication/outbox transactions; lifecycle/provider guards are explicit doubles."""

import importlib
import json
import os
import shutil
import socket
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from test_intelligence_events import (
    artifacts as artifacts,
)
from test_intelligence_events import (
    completed as completed,
)
from test_intelligence_events import (
    inputs as inputs,
)
from test_intelligence_events import (
    loaded as loaded,
)
from test_intelligence_events import (
    prepared_input as prepared_input,
)
from test_intelligence_events import (
    qualification as qualification,
)
from test_intelligence_events import (
    serving as serving,
)
from test_intelligence_events import (
    tables as tables,
)
from test_intelligence_events import (
    timeline as timeline,
)
from test_v12_queue import actor

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.forecast_jobs.v12_output_store import PostgresV12Publisher
from retailops_ai.intelligence_events.contracts import ForecastGenerated
from retailops_ai.intelligence_events.outbox import deliver_one

outbox_migration = importlib.import_module(
    "retailops_ai.migrations.versions.0020_intelligence_outbox"
)

POSTGRES = (
    "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
)


@pytest.fixture
def outbox_engine():
    if os.getenv("REQUIRE_AI10_OUTBOX_TESTS") != "1":
        pytest.skip("Set REQUIRE_AI10_OUTBOX_TESTS=1 for the disposable real PostgreSQL drill")
    docker_path = shutil.which("docker")
    if docker_path is None:
        pytest.fail("Docker is required for the isolated AI10 outbox drill")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    name = "retailops-ai10-outbox-" + uuid4().hex[:10]
    engine = None
    try:
        subprocess.run(
            [
                docker_path,
                "run",
                "-d",
                "--name",
                name,
                "--cpus",
                "1",
                "--memory",
                "512m",
                "-p",
                f"127.0.0.1:{port}:5432",
                "-e",
                "POSTGRES_USER=ai10",
                "-e",
                "POSTGRES_PASSWORD=ai10",
                "-e",
                "POSTGRES_DB=ai10",
                POSTGRES,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        engine = create_engine(
            f"postgresql+psycopg://ai10:ai10@127.0.0.1:{port}/ai10",
            hide_parameters=True,
            connect_args={"connect_timeout": 2},
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        with engine.begin() as connection:
            # Existing lifecycle constraints are covered by AI05; use small explicit tables
            # to isolate publication's transaction and the actual new outbox migration.
            connection.exec_driver_sql("""
CREATE SCHEMA ai;
CREATE TABLE ai.alembic_version(version_num text PRIMARY KEY);
CREATE TABLE ai.v12_forecast_outputs(artifact_id text PRIMARY KEY,run_id text UNIQUE,
 environment text,model_name text,document jsonb);
CREATE TABLE ai.v12_batch_runs(run_id text PRIMARY KEY,environment text,record jsonb);
CREATE TABLE ai.v12_batch_receipts(run_id text PRIMARY KEY,receipt jsonb);
CREATE TABLE ai.v12_model_heads(model_name text PRIMARY KEY,release_id text);
CREATE TABLE ai.v12_model_releases(release_id text PRIMARY KEY,release jsonb);
""")
            connection.execute(
                text("INSERT INTO ai.alembic_version VALUES (:revision)"),
                dict(revision=EXPECTED_REVISION),
            )
            with Operations.context(MigrationContext.configure(connection)):
                outbox_migration.upgrade()
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        subprocess.run([docker_path, "rm", "-fv", name], capture_output=True, timeout=45)


def test_atomic_publication_delivery_failure_and_crash_after_delivery(
    outbox_engine, completed, inputs
):
    _, run, receipt = completed
    release = receipt.release
    queue = SimpleNamespace(
        engine=outbox_engine,
        environment="test",
        model=run.resolved_model.model_name,
        get=lambda *_: run,
        output=lambda *_: receipt,
        _guard=lambda *_: None,
        _profile=lambda *_: inputs,
    )
    registry = SimpleNamespace(
        aliases=lambda *_: {
            "champion": release.binding.model_version,
            "rollback": release.previous_version,
        },
        validate=lambda *_: None,
    )
    with outbox_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO ai.v12_batch_runs VALUES (:id,'test',CAST(:doc AS jsonb))"),
            dict(id=run.run_id, doc=run.model_dump_json()),
        )
        connection.execute(
            text("INSERT INTO ai.v12_batch_receipts VALUES (:id,CAST(:doc AS jsonb))"),
            dict(id=run.run_id, doc=receipt.model_dump_json()),
        )
        connection.execute(
            text("INSERT INTO ai.v12_model_releases VALUES (:id,CAST(:doc AS jsonb))"),
            dict(id=release.release_id, doc=release.model_dump_json()),
        )
        connection.execute(
            text("INSERT INTO ai.v12_model_heads VALUES (:model,:id)"),
            dict(model=queue.model, id=release.release_id),
        )
    publisher = PostgresV12Publisher(queue, registry, events_enabled=True)

    def fail_outbox(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO ai.intelligence_outbox" in statement:
            raise RuntimeError("ai10_fail_after_outbox_insert")

    event.listen(outbox_engine, "after_cursor_execute", fail_outbox)
    try:
        with pytest.raises(RuntimeError, match="ai10_fail_after_outbox_insert"):
            publisher.publish(run.run_id, actor(inputs))
    finally:
        event.remove(outbox_engine, "after_cursor_execute", fail_outbox)
    with outbox_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM ai.v12_forecast_outputs")) == 0
        assert connection.scalar(text("SELECT count(*) FROM ai.intelligence_outbox")) == 0
    output = publisher.publish(run.run_id, actor(inputs))
    assert publisher.publish(run.run_id, actor(inputs)) == output
    with outbox_engine.connect() as connection:
        documents = connection.scalars(text("SELECT document FROM ai.intelligence_outbox")).all()
    assert len(documents) == len(output.rows) == 14
    assert len({document["event_id"] for document in documents}) == 14
    for document in documents:
        ForecastGenerated.model_validate_json(json.dumps(document))

    class ProducerDouble:
        def __init__(self):
            self.calls, self.confirm = [], False

        def produce(self, topic, *, key, value, on_delivery):
            self.calls.append((topic, key, value))
            if self.confirm:
                on_delivery(
                    None,
                    SimpleNamespace(
                        topic=lambda: topic, partition=lambda: 0, offset=lambda: len(self.calls) - 1
                    ),
                )

        def flush(self, timeout):
            return 0

    producer = ProducerDouble()
    with pytest.raises(RuntimeError, match="delivery_unconfirmed"):
        deliver_one(outbox_engine, producer, environment="test")
    producer.confirm = True

    def crash_receipt(conn, cursor, statement, parameters, context, executemany):
        if "UPDATE ai.intelligence_outbox" in statement:
            raise RuntimeError("ai10_crash_after_broker_delivery")

    event.listen(outbox_engine, "after_cursor_execute", crash_receipt)
    try:
        with pytest.raises(RuntimeError, match="crash_after_broker_delivery"):
            deliver_one(outbox_engine, producer, environment="test")
    finally:
        event.remove(outbox_engine, "after_cursor_execute", crash_receipt)
    with outbox_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM ai.intelligence_outbox WHERE delivered_at IS NOT NULL")
            )
            == 0
        )
    assert deliver_one(outbox_engine, producer, environment="test")
    assert producer.calls[0] == producer.calls[1] == producer.calls[2]
    assert not deliver_one(outbox_engine, producer, environment="local")
    remaining = 0
    while deliver_one(outbox_engine, producer, environment="test"):
        remaining += 1
    assert remaining == 13
    with outbox_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM ai.intelligence_outbox WHERE delivered_at IS NOT NULL")
            )
            == 14
        )
