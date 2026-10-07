"""Bounded PostgreSQL receiver projection; never a capture of operational Source SQL."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from retailops_ai.source_snapshot.files import nonfinite, unique_keys

from .history import ObservationHistory, ReplayError
from .wire import (
    MAX_FACTS,
    MAX_OFFSET,
    MAX_RECEIPTS,
    Capture,
    Envelope,
    Record,
    Stream,
    canonical,
    digest,
)

MAX_RECORD_BYTES = 32768
MAX_CAPTURES = 32


@dataclass(frozen=True)
class TransportRecord:
    partition: int
    offset: int
    value: bytes | None = field(repr=False)
    key: bytes | None = field(default=None, repr=False)
    headers: tuple[tuple[str, bytes | None], ...] = field(default=(), repr=False)
    timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        if type(self.partition) is not int or not 0 <= self.partition <= 31:
            raise ReplayError("observation_partition_unknown")
        if type(self.offset) is not int or not 0 <= self.offset < MAX_OFFSET:
            raise ReplayError("observation_transport_offset_invalid")
        if self.value is not None and (
            type(self.value) is not bytes or len(self.value) > MAX_RECORD_BYTES
        ):
            raise ReplayError("observation_transport_value_limit")
        if self.key is not None and (type(self.key) is not bytes or len(self.key) > 4096):
            raise ReplayError("observation_transport_key_limit")
        if self.timestamp_ms is not None and (
            type(self.timestamp_ms) is not int or not 0 <= self.timestamp_ms < MAX_OFFSET
        ):
            raise ReplayError("observation_transport_timestamp_invalid")
        if type(self.headers) is not tuple or len(self.headers) > 16:
            raise ReplayError("observation_transport_headers_limit")
        size = 0
        for item in self.headers:
            if type(item) is not tuple or len(item) != 2:
                raise ReplayError("observation_transport_headers_invalid")
            name, value = item
            if not isinstance(name, str) or not 1 <= len(name.encode()) <= 128:
                raise ReplayError("observation_transport_headers_invalid")
            if value is not None and type(value) is not bytes:
                raise ReplayError("observation_transport_headers_invalid")
            size += len(name.encode()) + (len(value) if value is not None else 0)
        if size > 4096:
            raise ReplayError("observation_transport_headers_limit")

    def metadata(self) -> dict[str, Any]:
        def encoded(value: bytes | None) -> str | None:
            return base64.b64encode(value).decode() if value is not None else None

        return {
            "key": encoded(self.key),
            "headers": [[name, encoded(value)] for name, value in self.headers],
            "timestamp_ms": self.timestamp_ms,
        }

    def fingerprint(self) -> str:
        document = {
            **self.metadata(),
            "value": None if self.value is None else base64.b64encode(self.value).decode(),
        }
        return hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass(frozen=True)
class Lease:
    group: str
    partition: int
    owner: UUID
    epoch: int
    resume_offset: int
    stream: Stream


@dataclass(frozen=True)
class Committed:
    outcome: Literal["inserted", "duplicate", "quarantined"]
    next_offset: int
    replayed: bool = False


class ObservationStore:
    """A stream row lock serializes cross-partition business identity changes.

    Caller supplies trusted topology/log bounds; no broker discovery or network
    credentials are implemented here. Groups are independent bounded projections.
    """

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ReplayError("observation_postgresql_required")
        self.engine = engine

    @contextmanager
    def _transaction(self, *, capture: bool = False) -> Iterator[Connection]:
        try:
            with self.engine.connect() as connection:
                if capture:
                    connection = connection.execution_options(isolation_level="REPEATABLE READ")
                with connection.begin():
                    connection.execute(text("SET LOCAL lock_timeout='3s'"))
                    connection.execute(text("SET LOCAL statement_timeout='5s'"))
                    yield connection
        except SQLAlchemyError:
            raise ReplayError("observation_database_failure") from None

    @staticmethod
    def _group(group: str) -> None:
        if not isinstance(group, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", group):
            raise ReplayError("observation_group_invalid")

    @staticmethod
    def _stream(connection: Connection, group: str, stream: Stream, *, lock: bool = False) -> Any:
        sql = "SELECT * FROM ai.observation_streams WHERE group_id=:g"
        if lock:
            sql += " FOR UPDATE"
        row = connection.execute(text(sql), dict(g=group)).mappings().first()
        if row is None or row.stream != stream.model_dump(mode="json"):
            raise ReplayError("observation_stream_identity_changed")
        return row

    def claim(
        self,
        group: str,
        partition: int,
        *,
        stream: Stream,
        partitions: int,
        log_low: int,
        log_high: int,
        broker_committed: int | None = None,
    ) -> Lease:
        self._group(group)
        if type(partitions) is not int or not 1 <= partitions <= 32:
            raise ReplayError("observation_invalid_partition_count")
        if type(partition) is not int or not 0 <= partition < partitions:
            raise ReplayError("observation_partition_unknown")
        if (
            any(
                type(value) is not int or not 0 <= value <= MAX_OFFSET
                for value in (log_low, log_high)
            )
            or log_low > log_high
        ):
            raise ReplayError("observation_log_bounds_invalid")
        if broker_committed is not None and (
            type(broker_committed) is not int or not 0 <= broker_committed <= MAX_OFFSET
        ):
            raise ReplayError("observation_broker_commit_invalid")
        owner = uuid4()
        with self._transaction() as connection:
            inserted = connection.execute(
                text("""
INSERT INTO ai.observation_streams(group_id,stream,partitions)
VALUES (:g,CAST(:s AS jsonb),:n) ON CONFLICT(group_id) DO NOTHING RETURNING group_id
"""),
                dict(g=group, s=stream.model_dump_json(), n=partitions),
            ).first()
            state = self._stream(connection, group, stream, lock=True)
            if state.partitions != partitions:
                raise ReplayError("observation_partition_vector_changed")
            if inserted is not None:
                for p in range(partitions):
                    connection.execute(
                        text(
                            "INSERT INTO ai.observation_partitions(group_id,partition) VALUES (:g,:p)"
                        ),
                        dict(g=group, p=p),
                    )
            row = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.observation_partitions WHERE group_id=:g AND partition=:p FOR UPDATE"
                    ),
                    dict(g=group, p=partition),
                )
                .mappings()
                .one()
            )
            if log_low > row.next_offset:
                raise ReplayError("observation_retention_gap")
            if log_high < row.next_offset:
                raise ReplayError("observation_log_rewound")
            if broker_committed is not None and broker_committed > row.next_offset:
                raise ReplayError("observation_broker_ahead_of_database")
            resume = row.next_offset if broker_committed is None else max(log_low, broker_committed)
            epoch = row.epoch + 1
            connection.execute(
                text(
                    "UPDATE ai.observation_partitions SET epoch=:e,owner=:o WHERE group_id=:g AND partition=:p"
                ),
                dict(g=group, p=partition, e=epoch, o=owner),
            )
        return Lease(group, partition, owner, epoch, resume, stream)

    def release(self, lease: Lease) -> bool:
        with self._transaction() as connection:
            self._stream(connection, lease.group, lease.stream, lock=True)
            changed = connection.execute(
                text("""
UPDATE ai.observation_partitions SET owner=NULL,epoch=epoch+1
WHERE group_id=:g AND partition=:p AND owner=:o AND epoch=:e
RETURNING partition
"""),
                dict(g=lease.group, p=lease.partition, o=lease.owner, e=lease.epoch),
            ).first()
        return changed is not None

    @staticmethod
    def _decode(raw: bytes | None) -> Envelope:
        try:
            if raw is None:
                raise ValueError
            decoded = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=nonfinite)
            if not isinstance(decoded, dict):
                raise ValueError
            return Envelope.model_validate_json(raw)
        except (ValueError, RecursionError):
            raise ReplayError("observation_envelope_rejected") from None

    @staticmethod
    def _accept(connection: Connection, group: str, envelope: Envelope) -> bool:
        row = envelope.fact
        params = dict(
            g=group, obs=row.observation_id, v=row.version, id=row.id, event=envelope.event_id
        )
        event_hash = connection.scalar(
            text(
                "SELECT event_sha256 FROM ai.observation_receipts WHERE group_id=:g AND event_id=:event LIMIT 1"
            ),
            params,
        )
        if event_hash is not None and event_hash != digest(envelope):
            raise ReplayError("observation_event_identity_collision")
        existing = connection.scalar(
            text(
                "SELECT fact_sha256 FROM ai.observation_versions WHERE group_id=:g AND observation_id=:obs AND version=:v"
            ),
            params,
        )
        if existing is not None:
            if existing != digest(row):
                raise ReplayError("observation_fact_version_collision")
            return False
        if connection.scalar(
            text("SELECT 1 FROM ai.observation_versions WHERE group_id=:g AND row_id=:id"), params
        ):
            raise ReplayError("observation_row_identity_collision")
        grain = json.dumps(
            [row.business_date.isoformat(), row.product_id, row.selling_location_id, row.channel],
            separators=(",", ":"),
        )
        grain_hash = hashlib.sha256(grain.encode()).hexdigest()
        identity = connection.scalar(
            text(
                "SELECT grain FROM ai.observation_identities WHERE group_id=:g AND observation_id=:obs"
            ),
            params,
        )
        if identity is not None and identity != json.loads(grain):
            raise ReplayError("observation_history_grain_changed")
        other = connection.scalar(
            text(
                "SELECT observation_id FROM ai.observation_identities WHERE group_id=:g AND grain_sha256=:h"
            ),
            dict(g=group, h=grain_hash),
        )
        if other is not None and str(other) != row.observation_id:
            raise ReplayError("observation_natural_grain_collision")
        neighbors = connection.execute(
            text(
                "SELECT version,available_at FROM ai.observation_versions WHERE group_id=:g AND observation_id=:obs AND version IN (:before,:after)"
            ),
            {**params, "before": row.version - 1, "after": row.version + 1},
        ).all()
        if any(
            (neighbor.version < row.version and neighbor.available_at > row.available_at)
            or (neighbor.version > row.version and neighbor.available_at < row.available_at)
            for neighbor in neighbors
        ):
            raise ReplayError("observation_history_availability_regression")
        connection.execute(
            text(
                "INSERT INTO ai.observation_identities(group_id,observation_id,grain,grain_sha256) VALUES (:g,:obs,CAST(:grain AS jsonb),:h) ON CONFLICT(group_id,observation_id) DO NOTHING"
            ),
            {**params, "grain": grain, "h": grain_hash},
        )
        connection.execute(
            text("""
INSERT INTO ai.observation_versions(group_id,observation_id,version,row_id,available_at,fact_sha256,document)
VALUES (:g,:obs,:v,:id,:at,:h,CAST(:doc AS jsonb))
"""),
            {**params, "at": row.available_at, "h": digest(row), "doc": row.model_dump_json()},
        )
        return True

    def process(self, lease: Lease, record: TransportRecord) -> Committed:
        if record.partition != lease.partition:
            raise ReplayError("observation_partition_unknown")
        with self._transaction() as connection:
            state = self._stream(connection, lease.group, lease.stream, lock=True)
            partition = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.observation_partitions WHERE group_id=:g AND partition=:p FOR UPDATE"
                    ),
                    dict(g=lease.group, p=lease.partition),
                )
                .mappings()
                .one()
            )
            if partition.owner != lease.owner or partition.epoch != lease.epoch:
                raise ReplayError("observation_lease_fenced")
            fingerprint = record.fingerprint()
            if record.offset < partition.next_offset:
                receipt = (
                    connection.execute(
                        text(
                            "SELECT transport_sha256,outcome FROM ai.observation_receipts WHERE group_id=:g AND partition=:p AND offset_value=:n"
                        ),
                        dict(g=lease.group, p=lease.partition, n=record.offset),
                    )
                    .mappings()
                    .first()
                )
                if receipt is None or receipt.transport_sha256 != fingerprint:
                    raise ReplayError("observation_overlap_transport_mismatch")
                return Committed(receipt.outcome, partition.next_offset, replayed=True)
            if record.offset != partition.next_offset:
                raise ReplayError("observation_transport_offset_gap")
            if state.receipt_count >= MAX_RECEIPTS:
                raise ReplayError("observation_candidate_size_limit")
            envelope = None
            try:
                envelope = self._decode(record.value)
            except ReplayError:
                pass
            if (
                envelope is not None
                and envelope.source_authority_id != lease.stream.source_authority_id
            ):
                raise ReplayError("observation_source_authority_changed")
            inserted, reason = False, None
            outcome: Literal["inserted", "duplicate", "quarantined"] = "quarantined"
            if envelope is None:
                reason = "observation_envelope_rejected"
            else:
                if state.fact_count >= MAX_FACTS and not connection.scalar(
                    text(
                        "SELECT 1 FROM ai.observation_versions WHERE group_id=:g AND observation_id=:obs AND version=:v"
                    ),
                    dict(g=lease.group, obs=envelope.fact.observation_id, v=envelope.fact.version),
                ):
                    raise ReplayError("observation_candidate_size_limit")
                try:
                    inserted = self._accept(connection, lease.group, envelope)
                    outcome = "inserted" if inserted else "duplicate"
                except ReplayError as error:
                    reason = str(error)
            connection.execute(
                text("""
INSERT INTO ai.observation_receipts(group_id,partition,offset_value,transport_sha256,raw_value,transport,
 event_id,event_sha256,fact_sha256,outcome,reason)
VALUES (:g,:p,:n,:h,:raw,CAST(:transport AS jsonb),:event,:event_hash,:fact_hash,:outcome,:reason)
"""),
                dict(
                    g=lease.group,
                    p=lease.partition,
                    n=record.offset,
                    h=fingerprint,
                    raw=record.value,
                    transport=json.dumps(record.metadata()),
                    event=envelope.event_id if reason is None and envelope else None,
                    event_hash=digest(envelope) if reason is None and envelope else None,
                    fact_hash=digest(envelope.fact) if reason is None and envelope else None,
                    outcome=outcome,
                    reason=reason,
                ),
            )
            connection.execute(
                text(
                    "UPDATE ai.observation_partitions SET next_offset=:n WHERE group_id=:g AND partition=:p"
                ),
                dict(g=lease.group, p=lease.partition, n=record.offset + 1),
            )
            connection.execute(
                text(
                    "UPDATE ai.observation_streams SET receipt_count=receipt_count+1,fact_count=fact_count+:added WHERE group_id=:g"
                ),
                dict(g=lease.group, added=int(inserted)),
            )
        return Committed(outcome, record.offset + 1)

    def capture(self, group: str, *, stream: Stream, partitions: int) -> Capture:
        self._group(group)
        with self._transaction(capture=True) as connection:
            state = self._stream(connection, group, stream)
            if state.partitions != partitions:
                raise ReplayError("observation_partition_vector_changed")
            boundaries = connection.execute(
                text(
                    "SELECT partition,next_offset FROM ai.observation_partitions WHERE group_id=:g ORDER BY partition"
                ),
                dict(g=group),
            ).all()
            receipts = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.observation_receipts WHERE group_id=:g ORDER BY partition,offset_value"
                    ),
                    dict(g=group),
                )
                .mappings()
                .all()
            )
            if any(row.outcome == "quarantined" for row in receipts):
                raise ReplayError("observation_capture_quarantined_prefix")
            history = ObservationHistory(stream, partitions=partitions)
            for receipt in receipts:
                try:
                    metadata = receipt.transport

                    def decoded(value: str | None) -> bytes | None:
                        return base64.b64decode(value, validate=True) if value is not None else None

                    transport = TransportRecord(
                        receipt.partition,
                        receipt.offset_value,
                        bytes(receipt.raw_value),
                        key=decoded(metadata["key"]),
                        headers=tuple(
                            (name, decoded(value)) for name, value in metadata["headers"]
                        ),
                        timestamp_ms=metadata["timestamp_ms"],
                    )
                    if (
                        transport.metadata() != metadata
                        or transport.fingerprint() != receipt.transport_sha256
                    ):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ReplayError("observation_stored_transport_mismatch") from None
                envelope = self._decode(bytes(receipt.raw_value))
                if (
                    digest(envelope) != receipt.event_sha256
                    or digest(envelope.fact) != receipt.fact_sha256
                ):
                    raise ReplayError("observation_stored_receipt_mismatch")
                history.process(
                    Record(
                        partition=receipt.partition, offset=receipt.offset_value, envelope=envelope
                    ),
                    stream=stream,
                )
            facts = connection.execute(
                text(
                    "SELECT fact_sha256,document FROM ai.observation_versions WHERE group_id=:g ORDER BY observation_id,version"
                ),
                dict(g=group),
            ).all()
            if (
                [(b.partition, b.next_offset) for b in history.boundaries]
                != [(b.partition, b.next_offset) for b in boundaries]
                or len(history.rows) != state.fact_count
                or len(receipts) != state.receipt_count
                or [(digest(row), row.model_dump(mode="json")) for row in history.rows]
                != [(fact.fact_sha256, fact.document) for fact in facts]
            ):
                raise ReplayError("observation_stored_projection_mismatch")
            capture = history.capture()
            raw = canonical(capture)
            prior = connection.scalar(
                text(
                    "SELECT document FROM ai.observation_captures WHERE group_id=:g AND capture_id=:id"
                ),
                dict(g=group, id=capture.capture_id),
            )
            if prior is not None:
                if bytes(prior) != raw:
                    raise ReplayError("observation_stored_capture_mismatch")
            else:
                # All capture writers take this lock after the RR read. A concurrent
                # stream write causes a serialization failure, never a mixed boundary.
                self._stream(connection, group, stream, lock=True)
                if (
                    connection.scalar(
                        text("SELECT count(*) FROM ai.observation_captures WHERE group_id=:g"),
                        dict(g=group),
                    )
                    >= MAX_CAPTURES
                ):
                    raise ReplayError("observation_capture_count_limit")
                connection.execute(
                    text(
                        "INSERT INTO ai.observation_captures(group_id,capture_id,capture_sha256,document) VALUES (:g,:id,:h,:raw)"
                    ),
                    dict(
                        g=group, id=capture.capture_id, h=hashlib.sha256(raw).hexdigest(), raw=raw
                    ),
                )
        return capture

    def load_capture(
        self, group: str, capture_id: str, *, stream: Stream, partitions: int
    ) -> Capture:
        self._group(group)
        if not re.fullmatch(r"observation-capture-sha256-[0-9a-f]{64}", capture_id):
            raise ReplayError("observation_capture_identity_invalid")
        with self._transaction(capture=True) as connection:
            self._stream(connection, group, stream)
            saved = (
                connection.execute(
                    text(
                        "SELECT document,capture_sha256 FROM ai.observation_captures WHERE group_id=:g AND capture_id=:id"
                    ),
                    dict(g=group, id=capture_id),
                )
                .mappings()
                .first()
            )
            if saved is None:
                raise ReplayError("observation_capture_not_found")
            raw = bytes(saved.document)
            if hashlib.sha256(raw).hexdigest() != saved.capture_sha256:
                raise ReplayError("observation_stored_capture_mismatch")
            capture = ObservationHistory.restore(
                raw, stream=stream, partitions=partitions
            ).capture()
            if capture.capture_id != capture_id:
                raise ReplayError("observation_stored_capture_mismatch")
        return capture


def process_then_ack(
    store: ObservationStore,
    lease: Lease,
    record: TransportRecord,
    ack: Callable[[int, int], None],
) -> Committed:
    """A transport adapter can only acknowledge a returned, committed position.

    ACK failures propagate; the DB receipt makes redelivery safe. This callback
    boundary does not itself discover, authenticate or commit to a real broker.
    """
    committed = store.process(lease, record)
    ack(record.partition, committed.next_offset)
    return committed
