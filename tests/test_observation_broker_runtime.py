"""Mandatory actual PostgreSQL + TLS/SCRAM Redpanda input-lane acceptance."""

from __future__ import annotations

import importlib
import json
import os
import select
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from test_observation_replay import STREAM, TIME, record, row

from retailops_ai.source_replay import ObservationHistory, ReplayError
from retailops_ai.source_replay.broker import TOPIC, BrokerConfig, ObservationRunner, build_client
from retailops_ai.source_replay.store import ObservationStore
from retailops_ai.source_replay.wire import canonical

ROOT = Path(__file__).resolve().parents[1]
POSTGRES = (
    "postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea"
)
REDPANDA = "redpandadata/redpanda:v25.3.6@sha256:ac152ec27adccf9482af2649d293f398eb03c860d7469fc49f86e30d870ea408"


def command(args, *, input=None, timeout=90):
    try:
        return subprocess.run(
            args, input=input, capture_output=True, check=True, timeout=timeout
        ).stdout
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("observation_runtime_command_failed") from None


def private_file(path, data):
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(0o600)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass(repr=False)
class Runtime:
    engine: object
    config: BrokerConfig
    admin: object
    native: object
    producer_config: dict
    private: Path
    checks: list[str] = field(default_factory=list)

    def __repr__(self):
        return "<isolated observation runtime>"

    def group(self):
        return "ai10-observation-" + uuid4().hex

    def lane(self, group=None, store=None):
        group = group or self.group()
        client, topology = build_client(self.config, group)
        return ObservationRunner(client, topology, store or ObservationStore(self.engine), group)

    def positions(self, group):
        client, _ = build_client(self.config, group)
        try:
            result = client.committed(
                [self.native.TopicPartition(TOPIC, p) for p in range(3)], timeout=10
            )
            assert len(result) == 3 and all(p.error is None for p in result)
            return [p.offset for p in sorted(result, key=lambda p: p.partition)]
        finally:
            client.close()

    def counts(self, group):
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT fact_count,receipt_count FROM ai.observation_streams WHERE group_id=:g"
                ),
                dict(g=group),
            ).first()
            return tuple(row) if row else (0, 0)

    def passed(self, name):
        assert name not in self.checks
        self.checks.append(name)


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    if os.getenv("REQUIRE_AI10_OBSERVATION_BROKER_TESTS") != "1":
        pytest.skip("Mandatory remote gate owns PostgreSQL and TLS/SCRAM Redpanda")
    docker = shutil.which("docker")
    if not docker:
        pytest.fail("Docker required for mandatory observation broker acceptance")
    native = importlib.import_module("confluent_kafka")
    admin_module = importlib.import_module("confluent_kafka.admin")
    private = tmp_path_factory.mktemp("ai10-observation-private")
    private.chmod(0o700)
    for directory in ("broker", "data"):
        (private / directory).mkdir(mode=0o700)
    names = [
        "ai10-observation-pg-" + uuid4().hex[:12],
        "ai10-observation-broker-" + uuid4().hex[:12],
    ]
    passwords = {
        key: uuid4().hex + uuid4().hex for key in ("database", "admin", "producer", "reader")
    }
    db_port, kafka_port, admin_port = port(), port(), port()
    engine, rt = None, None
    try:
        for name in ("trusted", "untrusted"):
            command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "2",
                    "-subj",
                    "/CN=AI10-" + name,
                    "-keyout",
                    str(private / (name + ".key")),
                    "-out",
                    str(private / (name + ".crt")),
                ]
            )
        command(
            [
                "openssl",
                "req",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(private / "broker/broker.key"),
                "-out",
                str(private / "broker.csr"),
            ]
        )
        private_file(
            private / "extensions",
            "subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n",
        )
        command(
            [
                "openssl",
                "x509",
                "-req",
                "-days",
                "2",
                "-in",
                str(private / "broker.csr"),
                "-CA",
                str(private / "trusted.crt"),
                "-CAkey",
                str(private / "trusted.key"),
                "-CAcreateserial",
                "-extfile",
                str(private / "extensions"),
                "-out",
                str(private / "broker/broker.crt"),
            ]
        )
        private_file(private / "broker/ca.crt", (private / "trusted.crt").read_bytes())
        (private / "broker/broker.key").chmod(0o600)
        private_file(
            private / "broker.env", "RP_BOOTSTRAP_USER=bootstrap:" + passwords["admin"] + "\n"
        )
        private_file(
            private / "broker/.bootstrap.yaml",
            "enable_sasl: true\nadmin_api_require_auth: true\nhttp_authentication: [BASIC]\nsuperusers: [bootstrap]\nauto_create_topics_enabled: false\nsasl_mechanisms: [SCRAM]\n",
        )
        private_file(
            private / "broker/redpanda.yaml",
            f"""redpanda:
  data_directory: /var/lib/redpanda/data
  node_id: 0
  seed_servers: []
  rpc_server: {{address: 0.0.0.0, port: 33145}}
  advertised_rpc_api: {{address: 127.0.0.1, port: 33145}}
  kafka_api: [{{address: 0.0.0.0, port: 9092, name: secure}}]
  advertised_kafka_api: [{{address: 127.0.0.1, port: {kafka_port}, name: secure}}]
  kafka_api_tls:
    - {{name: secure, enabled: true, require_client_auth: false, key_file: /private/broker.key, cert_file: /private/broker.crt, truststore_file: /private/ca.crt}}
  admin: [{{address: 0.0.0.0, port: 9644, name: secure-admin}}]
  admin_api_tls:
    - {{name: secure-admin, enabled: true, require_client_auth: false, key_file: /private/broker.key, cert_file: /private/broker.crt, truststore_file: /private/ca.crt}}
""",
        )
        private_file(
            private / "database.env",
            f"POSTGRES_USER=ai10\nPOSTGRES_DB=ai10\nPOSTGRES_PASSWORD={passwords['database']}\nPOSTGRES_HOST_AUTH_METHOD=scram-sha-256\n",
        )
        command(
            [
                docker,
                "run",
                "-d",
                "--name",
                names[0],
                "--cpus",
                "1",
                "--memory",
                "512m",
                "--env-file",
                str(private / "database.env"),
                "-p",
                f"127.0.0.1:{db_port}:5432",
                POSTGRES,
            ]
        )
        command(
            [
                docker,
                "run",
                "-d",
                "--name",
                names[1],
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cpus",
                "1",
                "--memory",
                "768m",
                "--env-file",
                str(private / "broker.env"),
                "-p",
                f"127.0.0.1:{kafka_port}:9092",
                "-p",
                f"127.0.0.1:{admin_port}:9644",
                "-v",
                f"{private / 'broker'}:/private:rw",
                "-v",
                f"{private / 'data'}:/var/lib/redpanda/data",
                REDPANDA,
                "redpanda",
                "start",
                "--config",
                "/private/redpanda.yaml",
                "--mode",
                "dev-container",
                "--smp",
                "1",
                "--memory",
                "512M",
                "--reserve-memory",
                "0M",
                "--overprovisioned",
                "--check=false",
            ]
        )
        engine = create_engine(
            f"postgresql+psycopg://ai10:{passwords['database']}@127.0.0.1:{db_port}/ai10",
            hide_parameters=True,
            connect_args={"connect_timeout": 2},
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                break
            except Exception:
                assert time.monotonic() < deadline, "isolated PostgreSQL unavailable"
                time.sleep(0.2)
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA ai"))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module(
                    "retailops_ai.migrations.versions.0021_observation_replay"
                ).upgrade()
        opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=ssl.create_default_context(cafile=str(private / "trusted.crt"))),
        )
        import base64

        auth = base64.b64encode(("bootstrap:" + passwords["admin"]).encode()).decode()
        request = Request(
            f"https://127.0.0.1:{admin_port}/v1/status/ready",
            headers={"Authorization": "Basic " + auth},
        )
        deadline = time.monotonic() + 90
        while True:
            try:
                with opener.open(request, timeout=3) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                pass
            assert time.monotonic() < deadline, "isolated TLS/SCRAM broker unavailable"
            time.sleep(0.5)
        common = {
            "bootstrap.servers": f"127.0.0.1:{kafka_port}",
            "security.protocol": "SASL_SSL",
            "sasl.mechanism": "SCRAM-SHA-256",
            "ssl.ca.location": str(private / "trusted.crt"),
            "enable.ssl.certificate.verification": True,
            "ssl.endpoint.identification.algorithm": "https",
            "log_level": 0,
            "allow.auto.create.topics": False,
        }
        admin = admin_module.AdminClient(
            {**common, "sasl.username": "bootstrap", "sasl.password": passwords["admin"]}
        )
        for future in admin.alter_user_scram_credentials(
            [
                admin_module.UserScramCredentialUpsertion(
                    name,
                    admin_module.ScramCredentialInfo(
                        admin_module.ScramMechanism.SCRAM_SHA_256, 4096
                    ),
                    passwords[name].encode(),
                )
                for name in ("producer", "reader")
            ],
            request_timeout=10,
        ).values():
            future.result(timeout=12)
        for future in admin.create_topics(
            [
                admin_module.NewTopic(
                    TOPIC,
                    num_partitions=3,
                    replication_factor=1,
                    config={"cleanup.policy": "delete"},
                ),
                admin_module.NewTopic("foreign-topic", num_partitions=1, replication_factor=1),
            ],
            request_timeout=10,
        ).values():
            future.result(timeout=12)
        grants = [
            (
                "producer",
                admin_module.ResourceType.TOPIC,
                TOPIC,
                admin_module.ResourcePatternType.LITERAL,
                [admin_module.AclOperation.WRITE, admin_module.AclOperation.DESCRIBE],
            ),
            (
                "reader",
                admin_module.ResourceType.TOPIC,
                TOPIC,
                admin_module.ResourcePatternType.LITERAL,
                [
                    admin_module.AclOperation.READ,
                    admin_module.AclOperation.DESCRIBE,
                    admin_module.AclOperation.DESCRIBE_CONFIGS,
                ],
            ),
            (
                "reader",
                admin_module.ResourceType.GROUP,
                "ai10-observation-",
                admin_module.ResourcePatternType.PREFIXED,
                [admin_module.AclOperation.READ, admin_module.AclOperation.DESCRIBE],
            ),
            (
                "reader",
                admin_module.ResourceType.BROKER,
                "kafka-cluster",
                admin_module.ResourcePatternType.LITERAL,
                [admin_module.AclOperation.DESCRIBE],
            ),
        ]
        bindings = [
            admin_module.AclBinding(
                kind,
                resource,
                pattern,
                "User:" + principal,
                "*",
                operation,
                admin_module.AclPermissionType.ALLOW,
            )
            for principal, kind, resource, pattern, operations in grants
            for operation in operations
        ]
        for future in admin.create_acls(bindings, request_timeout=10).values():
            future.result(timeout=12)
        config = BrokerConfig.model_validate_json(
            json.dumps(
                dict(
                    source_authority_id=STREAM.source_authority_id,
                    bootstrap_servers=common["bootstrap.servers"],
                    username="reader",
                    password=passwords["reader"],
                    ca_file=common["ssl.ca.location"],
                )
            )
        )
        producer_config = {
            **common,
            "sasl.username": "producer",
            "sasl.password": passwords["producer"],
            "enable.idempotence": False,
            "acks": "all",
            "delivery.timeout.ms": 10000,
        }
        rt = Runtime(engine, config, admin, native, producer_config, private)
        producer = native.Producer(producer_config)
        delivered = []
        messages = [
            (0, canonical(record(row()).envelope)),
            (0, canonical(record(row(2, 4), 1, 0).envelope)),
            (1, canonical(record(row(), 0, 1).envelope)),
            (2, b"raw-poison"),
        ]
        reference = ObservationHistory(STREAM, partitions=3).apply_batch(
            [record(row()), record(row(2, 4), 1, 0), record(row(), 0, 1)], stream=STREAM
        )
        assert len(reference.rows) == 2
        for p, value in messages:
            producer.produce(
                TOPIC,
                value=value,
                key=b"raw-key",
                headers=[("a", None), ("a", b"1")],
                timestamp=1790000000000,
                partition=p,
                on_delivery=lambda error, message: delivered.append(error),
            )
        assert producer.flush(15) == 0 and delivered == [None] * 4
        yield rt
    finally:
        if rt is not None:
            target = os.getenv("AI10_OBSERVATION_BROKER_REPORT")
            if target:
                report = {
                    "status": "passed" if len(rt.checks) == 12 else "incomplete",
                    "actual_broker_ack_performed": True,
                    "broker": REDPANDA,
                    "database": POSTGRES,
                    "tls_hostname_verified": True,
                    "sasl": "SCRAM-SHA-256",
                    "checks": sorted(rt.checks),
                    "source_authority": "explicit fixture; no operational Source emitter",
                    "source_live_capture_supported": False,
                    "full_43_table_handoff": False,
                    "models_qualified": False,
                }
                path = Path(target)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if engine is not None:
            engine.dispose()
        for name in names:
            subprocess.run([docker, "rm", "-fv", name], capture_output=True, timeout=45)
        shutil.rmtree(private)
        if rt is not None and len(rt.checks) != 12:
            pytest.fail("Mandatory broker acceptance did not complete every runtime check")


def fetch(runner, partition=0):
    runner.assigned(runner.client, [runner._partition(partition)])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        message = runner.client.poll(0.25)
        if message is not None:
            assert message.error() is None
            return message
    pytest.fail("actual broker message unavailable")


def close(runner):
    try:
        for lease in runner.leases.values():
            runner.store.release(lease)
    finally:
        runner.client.close()


def test_actual_subscription_correction_dedup_raw_quarantine_and_ack(runtime):
    runner = runtime.lane()
    group = runner.group
    assert runner.run(max_messages=4, max_seconds=30) == 4
    assert runtime.counts(group) == (2, 4) and runtime.positions(group) == [2, 1, 1]
    with runtime.engine.connect() as conn:
        receipt = conn.execute(
            text(
                "SELECT raw_value,transport,reason FROM ai.observation_receipts WHERE group_id=:g AND partition=2"
            ),
            dict(g=group),
        ).one()
        assert (
            bytes(receipt.raw_value) == b"raw-poison"
            and receipt.reason == "observation_envelope_rejected"
        )
        assert receipt.transport["headers"] == [["a", None], ["a", "MQ=="]]
    with pytest.raises(ReplayError, match="quarantined_prefix"):
        runner.store.capture(group, stream=runner.stream, partitions=3)
    runtime.passed("subscription_correction_dedup_quarantine_ack")


def test_actual_sql_capture_and_overlapping_broker_replay(runtime):
    runner = runtime.lane()
    group = runner.group
    try:
        for partition, count in ((0, 2), (1, 1)):
            message = fetch(runner, partition)
            runner.handle(message)
            if count == 2:
                deadline = time.monotonic() + 10
                message = None
                while message is None and time.monotonic() < deadline:
                    message = runner.client.poll(0.25)
                assert message is not None
                runner.handle(message)
            runner.revoked(runner.client, [runner._partition(partition)])
        stream = runner.stream
        capture = runner.store.capture(group, stream=stream, partitions=3)
        history = ObservationHistory.restore(canonical(capture), stream=stream, partitions=3)
        assert (
            history.quantity_total(TIME) == 10
            and history.quantity_total(TIME + timedelta(hours=1)) == 4
        )
        assert [b.next_offset for b in capture.boundaries] == [2, 1, 0]
        # Deliberately move only this owned group's broker checkpoint backward.
        runner.client.commit(offsets=[runner._partition(0, 0)], asynchronous=False)
        message = fetch(runner, 0)
        result = runner.handle(message)
        assert result.replayed and result.next_offset == 2
        assert runtime.positions(group)[0] == 1  # never ACK the unread SQL suffix
        assert canonical(runner.store.capture(group, stream=stream, partitions=3)) == canonical(
            capture
        )
        assert runtime.counts(group) == (2, 3)
    finally:
        close(runner)
    runtime.passed("sql_capture_overlap_ack_only_delivered")


@pytest.mark.parametrize("phase", ["before_commit", "after_commit_before_ack"])
def test_actual_sigkill_at_sql_and_broker_ack_boundaries(runtime, phase):
    group = runtime.group()
    initial, _ = build_client(runtime.config, group)
    try:
        receipt = initial.commit(
            offsets=[runtime.native.TopicPartition(TOPIC, 0, 0)], asynchronous=False
        )
        assert len(receipt) == 1 and receipt[0].offset == 0 and receipt[0].error is None
    finally:
        initial.close()
    child = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/observation_broker_child.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        configuration = runtime.config.model_dump(mode="json")
        configuration.update(
            username=runtime.config.username.get_secret_value(),
            password=runtime.config.password.get_secret_value(),
        )
        child.stdin.write(
            json.dumps(
                dict(
                    group=group,
                    phase=phase,
                    broker=configuration,
                    dsn=runtime.engine.url.render_as_string(hide_password=False),
                )
            )
        )
        child.stdin.close()
        assert select.select([child.stdout], [], [], 30)[0], "child did not reach kill boundary"
        assert child.stdout.readline().strip() == "ready"
        child.kill()
        assert child.wait(timeout=10) == -signal.SIGKILL
        expected = (0, 0) if phase == "before_commit" else (1, 1)
        assert runtime.counts(group) == expected
        assert runtime.positions(group) == [
            0,
            runtime.native.OFFSET_INVALID,
            runtime.native.OFFSET_INVALID,
        ]
        runner = runtime.lane(group)
        try:
            message = fetch(runner)
            result = runner.handle(message)
            assert result.replayed == (phase == "after_commit_before_ack")
            assert runtime.counts(group) == (1, 1) and runtime.positions(group)[0] == 1
        finally:
            close(runner)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
    runtime.passed("sigkill_" + phase)


def test_actual_connection_termination_rolls_back_and_never_acknowledges(runtime):
    runner = runtime.lane()
    message = fetch(runner)

    def terminate(conn, cursor, statement, parameters, context, many):
        if "INSERT INTO ai.observation_receipts" in statement:
            with runtime.engine.begin() as other:
                assert other.scalar(
                    text("SELECT pg_terminate_backend(:pid)"),
                    dict(pid=conn.connection.driver_connection.info.backend_pid),
                )

    event.listen(runtime.engine, "after_cursor_execute", terminate)
    try:
        with pytest.raises(ReplayError, match="database_failure"):
            runner.handle(message)
        assert runtime.counts(runner.group) == (0, 0)
        assert runtime.positions(runner.group) == [runtime.native.OFFSET_INVALID] * 3
    finally:
        event.remove(runtime.engine, "after_cursor_execute", terminate)
        close(runner)
    runtime.passed("sql_connection_loss_no_ack")


def test_actual_second_owner_fences_first_before_ack(runtime):
    first = runtime.lane()
    message = fetch(first)
    second = runtime.lane(first.group)
    try:
        other = fetch(second)
        with pytest.raises(ReplayError, match="lease_fenced"):
            first.handle(message)
        assert runtime.positions(first.group) == [runtime.native.OFFSET_INVALID] * 3
        assert second.handle(other).outcome == "inserted"
        assert runtime.counts(first.group) == (1, 1) and runtime.positions(first.group)[0] == 1
    finally:
        close(first)
        close(second)
    runtime.passed("second_owner_fencing")


@pytest.mark.parametrize(
    "change,expected", [("wrong_password", "_AUTHENTICATION"), ("untrusted_ca", "_SSL")]
)
def test_actual_authentication_and_ca_rejection(runtime, change, expected):
    module = importlib.import_module("confluent_kafka.admin")
    errors = []
    config = runtime.config.backend()
    config.update(
        {"sasl.password": "wrong"}
        if change == "wrong_password"
        else {"ssl.ca.location": str(runtime.private / "untrusted.crt")}
    )
    config["error_cb"] = errors.append
    client = module.AdminClient(config)
    with pytest.raises(runtime.native.KafkaException):
        client.list_topics(timeout=5)
    client.poll(0)
    assert any(e.code() == getattr(runtime.native.KafkaError, expected) for e in errors)
    runtime.passed(change)


def test_actual_reader_cannot_write_topic(runtime):
    delivered = []
    producer = runtime.native.Producer({**runtime.config.backend(), "delivery.timeout.ms": 5000})
    producer.produce(
        TOPIC,
        value=b"forbidden",
        partition=0,
        on_delivery=lambda error, message: delivered.append(error),
    )
    assert producer.flush(8) == 0 and len(delivered) == 1
    assert delivered[0].code() == runtime.native.KafkaError.TOPIC_AUTHORIZATION_FAILED
    runtime.passed("reader_write_denied")


def test_actual_foreign_group_and_topic_acl_denials(runtime):
    module = importlib.import_module("confluent_kafka.admin")
    admin = module.AdminClient(runtime.config.backend())
    for future, code in [
        (
            admin.describe_consumer_groups(["foreign-group"], request_timeout=5)["foreign-group"],
            runtime.native.KafkaError.GROUP_AUTHORIZATION_FAILED,
        ),
        (
            admin.describe_topics(
                runtime.native.TopicCollection(["foreign-topic"]), request_timeout=5
            )["foreign-topic"],
            runtime.native.KafkaError.TOPIC_AUTHORIZATION_FAILED,
        ),
        (
            admin.create_topics(
                [
                    module.NewTopic(
                        "forbidden-created-topic", num_partitions=1, replication_factor=1
                    )
                ],
                request_timeout=5,
            )["forbidden-created-topic"],
            runtime.native.KafkaError.TOPIC_AUTHORIZATION_FAILED,
        ),
    ]:
        with pytest.raises(runtime.native.KafkaException) as exc:
            future.result(timeout=6)
        assert exc.value.args[0].code() == code
    runtime.passed("foreign_group_topic_create_denied")


def test_actual_compaction_policy_change_stops_without_ack(runtime):
    runner = runtime.lane()
    message = fetch(runner)
    module = importlib.import_module("confluent_kafka.admin")
    resource = module.ConfigResource(
        module.ResourceType.TOPIC, TOPIC, set_config={"cleanup.policy": "compact,delete"}
    )
    try:
        runtime.admin.alter_configs([resource], request_timeout=5)[resource].result(timeout=6)
        with pytest.raises(ReplayError, match="delete_only_topic_required"):
            runner.handle(message)
        assert runtime.counts(runner.group) == (0, 0)
        assert runtime.positions(runner.group) == [runtime.native.OFFSET_INVALID] * 3
    finally:
        restore = module.ConfigResource(
            module.ResourceType.TOPIC, TOPIC, set_config={"cleanup.policy": "delete"}
        )
        runtime.admin.alter_configs([restore], request_timeout=5)[restore].result(timeout=6)
        close(runner)
    runtime.passed("compaction_change_no_ack")


def test_actual_topic_uuid_replacement_stops_existing_projection(runtime):
    # Last test owns destructive replacement of only this fixture topic.
    runner = runtime.lane()
    message = fetch(runner)
    original, total = runner.topology.inspect()
    module = importlib.import_module("confluent_kafka.admin")
    try:
        runtime.admin.delete_topics([TOPIC], request_timeout=5)[TOPIC].result(timeout=6)
        runtime.admin.create_topics(
            [
                module.NewTopic(
                    TOPIC,
                    num_partitions=3,
                    replication_factor=1,
                    config={"cleanup.policy": "delete"},
                )
            ],
            request_timeout=5,
        )[TOPIC].result(timeout=6)
        replacement, count = runner.topology.inspect()
        assert replacement.topic_id != original.topic_id and count == total == 3
        with pytest.raises(ReplayError, match="identity_changed"):
            runner.handle(message)
        assert runtime.counts(runner.group) == (0, 0)
        assert runtime.positions(runner.group) == [runtime.native.OFFSET_INVALID] * 3
    finally:
        close(runner)
    runtime.passed("topic_uuid_replacement_no_ack")
