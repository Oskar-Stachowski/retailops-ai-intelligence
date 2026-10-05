"""Authenticated, bounded observation input lane; no Source producer is created."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from retailops_ai.source_snapshot.files import nonfinite, unique_keys

from .history import ReplayError
from .store import Committed, Lease, ObservationStore, TransportRecord
from .wire import MAX_OFFSET, Stream, UUIDText

TOPIC = "retailops.source-observations.v1"


class BrokerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    source_authority_id: UUIDText
    bootstrap_servers: str = Field(min_length=1, max_length=2048, repr=False)
    security_protocol: Literal["SASL_SSL"] = "SASL_SSL"
    sasl_mechanism: Literal["SCRAM-SHA-256", "SCRAM-SHA-512"] = "SCRAM-SHA-256"
    username: SecretStr = Field(repr=False)
    password: SecretStr = Field(repr=False)
    ca_file: str = Field(min_length=1, max_length=4096, repr=False)

    @model_validator(mode="after")
    def endpoints(self) -> BrokerConfig:
        endpoints = self.bootstrap_servers.split(",")
        if len(endpoints) > 8:
            raise ValueError("observation_broker_endpoint_limit")
        for endpoint in endpoints:
            url = urlsplit("//" + endpoint)
            if (
                not url.hostname
                or not url.port
                or url.username
                or url.password
                or url.path
                or url.query
                or url.fragment
                or endpoint != endpoint.strip()
            ):
                raise ValueError("observation_broker_endpoint_invalid")
        if not self.username.get_secret_value() or not self.password.get_secret_value():
            raise ValueError("observation_broker_credentials_required")
        if not Path(self.ca_file).is_absolute():
            raise ValueError("observation_broker_absolute_ca_required")
        return self

    def backend(self) -> dict[str, Any]:
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": self.security_protocol,
            "sasl.mechanism": self.sasl_mechanism,
            "sasl.username": self.username.get_secret_value(),
            "sasl.password": self.password.get_secret_value(),
            "ssl.ca.location": self.ca_file,
            "enable.ssl.certificate.verification": True,
            "ssl.endpoint.identification.algorithm": "https",
            "allow.auto.create.topics": False,
            "log_level": 0,
        }

    @classmethod
    def read_private(cls, path: Path) -> BrokerConfig:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 16384
            ):
                raise ReplayError("observation_private_broker_config_required")
            raw = os.read(descriptor, 16385)
            if len(raw) > 16384:
                raise ReplayError("observation_private_broker_config_limit")
            try:
                json.loads(raw, object_pairs_hook=unique_keys, parse_constant=nonfinite)
                return cls.model_validate_json(raw)
            except (ValueError, RecursionError):
                raise ReplayError("observation_private_broker_config_invalid") from None
        finally:
            os.close(descriptor)


class Topology(Protocol):
    def inspect(self) -> tuple[Stream, int]: ...


class BrokerTopology:
    def __init__(self, admin: Any, authority: str) -> None:
        self.admin = admin
        self.authority = authority

    def inspect(self) -> tuple[Stream, int]:
        # Cluster discovery must complete before DescribeTopics negotiates IDs.
        native = import_module("confluent_kafka")
        admin_module = import_module("confluent_kafka.admin")
        try:
            cluster = self.admin.describe_cluster(request_timeout=5).result(timeout=6)
            description = self.admin.describe_topics(
                native.TopicCollection([TOPIC]), request_timeout=5
            )[TOPIC].result(timeout=6)
            resource = admin_module.ConfigResource(admin_module.ResourceType.TOPIC, TOPIC)
            configuration = self.admin.describe_configs([resource], request_timeout=5)[
                resource
            ].result(timeout=6)
            policy = configuration.get("cleanup.policy")
            if policy is None or policy.value != "delete":
                raise ReplayError("observation_delete_only_topic_required")
            partitions = sorted(item.id for item in description.partitions)
            if (
                description.name != TOPIC
                or description.is_internal
                or not 1 <= len(partitions) <= 32
                or partitions != list(range(len(partitions)))
                or any(item.leader is None for item in description.partitions)
            ):
                raise ReplayError("observation_broker_topology_invalid")
            if cluster.cluster_id is None or description.topic_id is None:
                raise ReplayError("observation_broker_identity_missing")
            stream = Stream(
                source_authority_id=self.authority,
                cluster_id=str(cluster.cluster_id),
                topic_id=str(description.topic_id),
            )
            return stream, len(partitions)
        except ReplayError:
            raise
        except Exception:
            raise ReplayError("observation_broker_topology_unavailable") from None


def build_client(config: BrokerConfig, group: str) -> tuple[Any, BrokerTopology]:
    ObservationStore._group(group)
    if not group.startswith("retailops-observations-") and not group.startswith(
        "ai10-observation-"
    ):
        raise ReplayError("observation_separate_consumer_group_required")
    native = import_module("confluent_kafka")
    admin_module = import_module("confluent_kafka.admin")
    values = config.backend()
    client = native.Consumer(
        {
            **values,
            "group.id": group,
            "client.id": "retailops-observations-sql",
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "auto.offset.reset": "error",
            "enable.partition.eof": False,
            "partition.assignment.strategy": "range",
            "isolation.level": "read_committed",
            "session.timeout.ms": 10000,
            "heartbeat.interval.ms": 3000,
            "max.poll.interval.ms": 120000,
        }
    )
    try:
        return client, BrokerTopology(admin_module.AdminClient(values), config.source_authority_id)
    except Exception:
        client.close()
        raise ReplayError("observation_broker_unavailable") from None


class ObservationRunner:
    """Eager group assignment, dense delete-only log, fail-stop on any error.

    PostgreSQL fencing protects effects. Only synchronous explicit message-next
    offsets are committed; a historical overlap never acknowledges an unread
    suffix merely because SQL's checkpoint is already ahead.
    """

    def __init__(
        self,
        client: Any,
        topology: Topology,
        store: ObservationStore,
        group: str,
    ) -> None:
        ObservationStore._group(group)
        self.client, self.topology, self.store, self.group = client, topology, store, group
        self.leases: dict[int, Lease] = {}
        self.delivery_offsets: dict[int, int] = {}
        self.stream: Stream | None = None
        self.partitions: int | None = None
        self.failed = False

    @staticmethod
    def _partition(partition: int, offset: int = -1001) -> Any:
        return import_module("confluent_kafka").TopicPartition(TOPIC, partition, offset)

    def _same_topology(self) -> None:
        stream, partitions = self.topology.inspect()
        if stream != self.stream or partitions != self.partitions:
            raise ReplayError("observation_broker_identity_changed")

    def assigned(self, client: Any, partitions: list[Any]) -> None:
        try:
            stream, total = self.topology.inspect()
            ids = [p.partition for p in partitions]
            if (
                not ids
                or len(set(ids)) != len(ids)
                or any(
                    p.topic != TOPIC or type(p.partition) is not int or not 0 <= p.partition < total
                    for p in partitions
                )
                or self.leases
            ):
                raise ReplayError("observation_partition_assignment_invalid")
            native = import_module("confluent_kafka")
            commits = client.committed(partitions, timeout=5)
            if (
                len(commits) != len(ids)
                or {p.partition for p in commits} != set(ids)
                or any(
                    p.topic != TOPIC
                    or p.error is not None
                    or type(p.offset) is not int
                    or (p.offset != native.OFFSET_INVALID and not 0 <= p.offset <= MAX_OFFSET)
                    for p in commits
                )
            ):
                raise ReplayError("observation_broker_positions_invalid")
            positions = {p.partition: p.offset for p in commits}
            self.stream, self.partitions = stream, total
            assigned = []
            for p in partitions:
                low, high = client.get_watermark_offsets(p, timeout=5, cached=False)
                committed = positions[p.partition]
                lease = self.store.claim(
                    self.group,
                    p.partition,
                    stream=stream,
                    partitions=total,
                    log_low=low,
                    log_high=high,
                    broker_committed=None if committed == native.OFFSET_INVALID else committed,
                )
                self.leases[p.partition] = lease
                self.delivery_offsets[p.partition] = lease.resume_offset
                assigned.append(self._partition(p.partition, lease.resume_offset))
            self._same_topology()
            client.assign(assigned)
        except Exception:
            self.failed = True
            raise

    def revoked(self, client: Any, partitions: list[Any]) -> None:
        try:
            for p in partitions:
                if p.topic != TOPIC:
                    raise ReplayError("observation_partition_assignment_invalid")
                lease = self.leases.pop(p.partition, None)
                self.delivery_offsets.pop(p.partition, None)
                if lease is not None:
                    self.store.release(lease)
            client.unassign()
        except Exception:
            self.failed = True
            raise

    def handle(self, message: Any) -> Committed:
        if self.failed:
            raise ReplayError("observation_runner_stopped")
        try:
            if message.error() is not None:
                raise ReplayError("observation_broker_poll_failed")
            p, offset = message.partition(), message.offset()
            if message.topic() != TOPIC or type(p) is not int or p not in self.leases:
                raise ReplayError("observation_partition_not_owned")
            if type(offset) is not int or offset != self.delivery_offsets[p]:
                raise ReplayError("observation_delivery_offset_gap")
            self._same_topology()
            timestamp = message.timestamp()[1]
            headers = message.headers()
            if headers is not None and not isinstance(headers, (list, tuple)):
                raise ReplayError("observation_transport_headers_invalid")
            record = TransportRecord(
                p,
                offset,
                message.value(),
                message.key(),
                tuple(headers or ()),
                None if timestamp == -1 else timestamp,
            )
            committed = self.store.process(self.leases[p], record)
            self._same_topology()
            # Deliberately ACK only the record actually read, even during overlap.
            receipt = self.client.commit(
                offsets=[self._partition(p, offset + 1)], asynchronous=False
            )
            if (
                not receipt
                or len(receipt) != 1
                or receipt[0].error is not None
                or receipt[0].topic != TOPIC
                or receipt[0].partition != p
                or receipt[0].offset != offset + 1
            ):
                raise ReplayError("observation_broker_ack_receipt_missing")
            self.delivery_offsets[p] = offset + 1
            return committed
        except Exception:
            self.failed = True
            raise

    def run(
        self,
        *,
        stop_event: threading.Event | None = None,
        max_messages: int = 100,
        max_seconds: int = 60,
    ) -> int:
        count = 0
        try:
            if (
                type(max_messages) is not int
                or not 1 <= max_messages <= 20000
                or type(max_seconds) is not int
                or not 1 <= max_seconds <= 3600
                or self.failed
            ):
                raise ReplayError("observation_runner_bounds_invalid")
            stop = stop_event or threading.Event()
            deadline = time.monotonic() + max_seconds
            self.client.subscribe(
                [TOPIC], on_assign=self.assigned, on_revoke=self.revoked, on_lost=self.revoked
            )
            while count < max_messages and not stop.is_set():
                if time.monotonic() >= deadline:
                    raise ReplayError("observation_runner_deadline")
                message = self.client.poll(0.25)
                # librdkafka can report a callback failure as a poll exception;
                # the sticky flag also prevents continuing when a caller catches it.
                if self.failed:
                    raise ReplayError("observation_runner_stopped")
                if message is not None:
                    self.handle(message)
                    count += 1
            return count
        except Exception:
            self.failed = True
            raise
        finally:
            try:
                for lease in list(self.leases.values()):
                    self.store.release(lease)
            finally:
                self.leases.clear()
                self.delivery_offsets.clear()
                self.client.close()
