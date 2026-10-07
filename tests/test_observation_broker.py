"""Receiver ordering, identity and failure boundaries without optional Kafka imports."""

from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from test_observation_replay import STREAM

from retailops_ai.source_replay import ReplayError, broker
from retailops_ai.source_replay.broker import BrokerConfig, ObservationRunner
from retailops_ai.source_replay.store import Committed, Lease


def config(**changes):
    return BrokerConfig.model_validate_json(
        __import__("json").dumps(
            {
                "source_authority_id": STREAM.source_authority_id,
                "bootstrap_servers": "127.0.0.1:9092",
                "username": "private-user",
                "password": "private-password",
                "ca_file": "/private/ca.crt",
                **changes,
            }
        )
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"security_protocol": "PLAINTEXT"},
        {"security_protocol": "SSL"},
        {"sasl_mechanism": "PLAIN"},
        {"username": ""},
        {"password": ""},
        {"ca_file": "relative.crt"},
        {"bootstrap_servers": "host"},
        {"bootstrap_servers": "user:password@host:9092"},
        {"bootstrap_servers": "host:9092/path"},
        {"bootstrap_servers": "host:9092?query"},
        {"bootstrap_servers": " host:9092"},
        {"bootstrap_servers": ",".join(["host:9092"] * 9)},
        {"enable.auto.commit": True},
    ],
)
def test_broker_configuration_rejects_insecure_or_ambiguous_options(changes):
    with pytest.raises(ValueError):
        config(**changes)


def test_broker_configuration_keeps_secrets_out_of_repr_and_enforces_tls():
    value = config()
    assert "private-user" not in repr(value) and "private-password" not in repr(value)
    backend = value.backend()
    assert backend["security.protocol"] == "SASL_SSL"
    assert backend["enable.ssl.certificate.verification"] is True
    assert backend["ssl.endpoint.identification.algorithm"] == "https"
    assert backend["allow.auto.create.topics"] is False


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o660])
def test_private_configuration_rejects_permissions(tmp_path, mode):
    path = tmp_path / "broker.json"
    path.write_text(config().model_dump_json())
    path.chmod(mode)
    with pytest.raises(ReplayError, match="private_broker_config_required"):
        BrokerConfig.read_private(path)


def test_private_configuration_rejects_symlink_duplicate_key_and_size(tmp_path):
    path = tmp_path / "broker.json"
    # SecretStr serialization is redacted: write the explicit fixture instead.
    import json

    document = {**config().model_dump(mode="json"), "username": "user", "password": "password"}
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    assert BrokerConfig.read_private(path).password.get_secret_value() == "password"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError):
        BrokerConfig.read_private(link)
    path.write_text('{"password":"secret","password":"different"}')
    with pytest.raises(ReplayError, match="config_invalid"):
        BrokerConfig.read_private(path)
    path.write_bytes(b"x" * 16385)
    with pytest.raises(ReplayError, match="config_required"):
        BrokerConfig.read_private(path)


def tp(topic, partition, offset=-1001):
    return NS(topic=topic, partition=partition, offset=offset, error=None)


class Client:
    def __init__(self):
        self.commits = []
        self.closed = False
        self.assigned = []
        self.saved = [tp(broker.TOPIC, 0)]
        self.receipt = None
        self.messages = []

    def committed(self, partitions, timeout):
        return self.saved

    def get_watermark_offsets(self, partition, timeout, cached):
        assert cached is False
        return 0, 20

    def assign(self, partitions):
        self.assigned = partitions

    def unassign(self):
        self.assigned = []

    def commit(self, offsets, asynchronous):
        assert asynchronous is False
        self.commits.extend(offsets)
        return offsets if self.receipt is None else self.receipt

    def subscribe(self, topics, **callbacks):
        assert topics == [broker.TOPIC]
        callbacks["on_assign"](self, [tp(broker.TOPIC, 0)])

    def poll(self, timeout):
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


class Store:
    def __init__(self):
        self.trace = []
        self.next = 1
        self.failure = None
        self.after = None
        self.lease = Lease("ai10-observation-unit", 0, uuid4(), 1, 0, STREAM)

    def claim(self, group, partition, **kwargs):
        assert kwargs["stream"] == STREAM and kwargs["partitions"] == 1
        return self.lease

    def process(self, lease, record):
        self.trace.append(record)
        if self.failure:
            raise self.failure
        if self.after:
            self.after()
        return Committed("duplicate", self.next, replayed=self.next > record.offset + 1)

    def release(self, lease):
        self.trace.append("released")
        return True


class Topology:
    def __init__(self):
        self.value = STREAM, 1

    def inspect(self):
        return self.value


def message(offset=0, **changes):
    values = dict(
        topic=broker.TOPIC,
        partition=0,
        offset=offset,
        value=b"private-raw",
        key=b"private-key",
        headers=[("a", None), ("a", b"1")],
        timestamp=(1, 42),
        error=None,
    )
    values.update(changes)
    return NS(**{name: lambda value=value: value for name, value in values.items()})


@pytest.fixture
def lane(monkeypatch):
    monkeypatch.setattr(
        broker, "import_module", lambda name: NS(TopicPartition=tp, OFFSET_INVALID=-1001)
    )
    client, topology, store = Client(), Topology(), Store()
    runner = ObservationRunner(client, topology, store, "ai10-observation-unit")
    runner.assigned(client, [tp(broker.TOPIC, 0)])
    return runner, client, topology, store


def test_historical_overlap_acknowledges_only_actual_delivered_offset(lane):
    runner, client, topology, store = lane
    store.next = 9
    result = runner.handle(message())
    assert result.next_offset == 9 and result.replayed
    assert client.commits[0].offset == 1 and runner.delivery_offsets[0] == 1
    saved = store.trace[0]
    assert saved.value == b"private-raw" and saved.key == b"private-key"
    assert saved.headers == (("a", None), ("a", b"1")) and saved.timestamp_ms == 42


@pytest.mark.parametrize("phase", ["before_sql", "after_sql"])
def test_topology_change_blocks_ack_and_stops_runner(lane, phase):
    runner, client, topology, store = lane

    def change():
        topology.value = STREAM.model_copy(update={"topic_id": "replacement"}), 1

    if phase == "before_sql":
        change()
    else:
        store.after = change
    with pytest.raises(ReplayError, match="identity_changed"):
        runner.handle(message())
    assert not client.commits and len(store.trace) == (phase == "after_sql")
    with pytest.raises(ReplayError, match="runner_stopped"):
        runner.handle(message())


def test_database_failure_prevents_ack_and_following_delivery(lane):
    runner, client, topology, store = lane
    store.failure = ReplayError("observation_database_failure")
    with pytest.raises(ReplayError, match="database_failure"):
        runner.handle(message())
    assert not client.commits
    with pytest.raises(ReplayError, match="runner_stopped"):
        runner.handle(message(1))


@pytest.mark.parametrize(
    "receipts",
    [
        [],
        [tp("foreign", 0, 1)],
        [tp(broker.TOPIC, 1, 1)],
        [tp(broker.TOPIC, 0, 2)],
        [tp(broker.TOPIC, 0, 1)] * 2,
    ],
)
def test_missing_or_mismatched_ack_receipt_stops_after_durable_effect(lane, receipts):
    runner, client, topology, store = lane
    client.receipt = receipts
    with pytest.raises(ReplayError, match="ack_receipt_missing"):
        runner.handle(message())
    assert len(store.trace) == 1 and runner.delivery_offsets[0] == 0 and runner.failed


@pytest.mark.parametrize(
    "change",
    [
        {"offset": 2},
        {"topic": "foreign"},
        {"partition": 1},
        {"error": "failure"},
        {"headers": {"a": b"b"}},
        {"value": b"x" * 32769},
    ],
)
def test_untrusted_delivery_rejected_before_sql_or_ack(lane, change):
    runner, client, topology, store = lane
    with pytest.raises(ReplayError):
        runner.handle(message(**change))
    assert not store.trace and not client.commits and runner.failed


@pytest.mark.parametrize("kind", ["foreign", "duplicate", "negative", "missing", "error"])
def test_assignment_rejects_untrusted_broker_positions(lane, kind):
    runner, client, topology, store = lane
    runner.revoked(client, [tp(broker.TOPIC, 0)])
    bad = tp(broker.TOPIC, 0, 0)
    if kind == "foreign":
        bad.topic = "foreign"
    elif kind == "negative":
        bad.offset = -2
    elif kind == "error":
        bad.error = "error"
    client.saved = [] if kind == "missing" else [bad, bad] if kind == "duplicate" else [bad]
    with pytest.raises(ReplayError, match="positions_invalid"):
        runner.assigned(client, [tp(broker.TOPIC, 0)])
    assert runner.failed and not client.assigned


def test_run_releases_leases_and_closes_on_failure(lane):
    runner, client, topology, store = lane
    runner.revoked(client, [tp(broker.TOPIC, 0)])
    client.messages = [message(offset=2)]
    with pytest.raises(ReplayError, match="offset_gap"):
        runner.run(max_messages=1)
    assert client.closed and not runner.leases and store.trace.count("released") == 2


def test_build_client_pins_manual_commit_and_eager_assignment(monkeypatch):
    options = []
    client = Client()
    monkeypatch.setattr(
        broker,
        "import_module",
        lambda name: NS(
            Consumer=lambda values: options.append(values) or client,
            AdminClient=lambda values: object(),
        ),
    )
    assert broker.build_client(config(), "ai10-observation-unit")[0] is client
    value = options[0]
    assert value["enable.auto.commit"] is False and value["enable.auto.offset.store"] is False
    assert (
        value["auto.offset.reset"] == "error" and value["partition.assignment.strategy"] == "range"
    )
    assert value["isolation.level"] == "read_committed"
    with pytest.raises(ReplayError, match="separate_consumer_group"):
        broker.build_client(config(), "retailops-intelligence-v2")


@pytest.mark.parametrize(
    "change",
    ["compact", "sparse", "missing-cluster", "nil-topic", "foreign", "internal", "no-leader"],
)
def test_topology_rejects_invalid_identity_layout_and_policy(monkeypatch, change):
    cluster = NS(cluster_id=STREAM.cluster_id)
    description = NS(
        name=broker.TOPIC,
        is_internal=False,
        topic_id=STREAM.topic_id,
        partitions=[NS(id=0, leader=NS(id=0))],
    )
    policy = NS(value="delete")
    if change == "compact":
        policy.value = "compact,delete"
    elif change == "sparse":
        description.partitions[0].id = 1
    elif change == "missing-cluster":
        cluster.cluster_id = None
    elif change == "nil-topic":
        description.topic_id = "A" * 22
    elif change == "foreign":
        description.name = "foreign"
    elif change == "internal":
        description.is_internal = True
    elif change == "no-leader":
        description.partitions[0].leader = None

    def future(value):
        return NS(result=lambda **kwargs: value)

    resource = object()
    admin = NS(
        describe_cluster=lambda **kwargs: future(cluster),
        describe_topics=lambda collection, **kwargs: {broker.TOPIC: future(description)},
        describe_configs=lambda collection, **kwargs: {
            resource: future({"cleanup.policy": policy})
        },
    )
    monkeypatch.setattr(
        broker,
        "import_module",
        lambda name: NS(
            TopicCollection=lambda topics: topics,
            ConfigResource=lambda *args: resource,
            ResourceType=NS(TOPIC=2),
        ),
    )
    with pytest.raises(ReplayError):
        broker.BrokerTopology(admin, STREAM.source_authority_id).inspect()
