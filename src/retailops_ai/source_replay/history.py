"""Fail-closed candidate reduction, independent of delivery and database commits."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from .wire import (
    MAX_CAPTURE_BYTES,
    MAX_FACTS,
    MAX_RECEIPTS,
    Boundary,
    Capture,
    Envelope,
    ObservationVersion,
    Receipt,
    Record,
    Stream,
    canonical,
    digest,
)


class ReplayError(ValueError):
    """Fixed reason only; never echo a private source fact or envelope."""


def check_history(rows: Iterable[ObservationVersion]) -> None:
    histories: dict[str, list[ObservationVersion]] = {}
    for row in rows:
        histories.setdefault(row.observation_id, []).append(row)
    for versions in histories.values():
        previous: ObservationVersion | None = None
        for expected, row in enumerate(sorted(versions, key=lambda r: r.version), start=1):
            if row.version != expected:
                raise ReplayError("observation_history_version_gap")
            if previous is not None and row.available_at < previous.available_at:
                raise ReplayError("observation_history_availability_regression")
            previous = row


def validate_capture(capture: Capture) -> None:
    partitions = [boundary.partition for boundary in capture.boundaries]
    if partitions != list(range(len(partitions))):
        raise ReplayError("observation_partition_vector_incomplete")
    if list(capture.rows) != sorted(capture.rows, key=lambda r: r.key):
        raise ReplayError("observation_capture_rows_not_ordered")
    coordinates = [(receipt.partition, receipt.offset) for receipt in capture.receipts]
    if coordinates != sorted(set(coordinates)):
        raise ReplayError("observation_capture_receipts_not_ordered")
    if sum(boundary.next_offset for boundary in capture.boundaries) != len(capture.receipts):
        raise ReplayError("observation_capture_offset_gap")
    state = ObservationHistory(capture.stream, partitions=len(partitions))
    facts = {digest(row): row for row in capture.rows}
    if len(facts) != len(capture.rows):
        raise ReplayError("observation_capture_duplicate_fact")
    for receipt in capture.receipts:
        row = facts.get(receipt.fact_sha256)
        if row is None:
            raise ReplayError("observation_capture_unbound_receipt")
        envelope = Envelope(
            source_authority_id=capture.stream.source_authority_id,
            event_id=receipt.event_id,
            fact=row,
        )
        if digest(envelope) != receipt.event_sha256:
            raise ReplayError("observation_capture_envelope_mismatch")
        state.process(
            Record(partition=receipt.partition, offset=receipt.offset, envelope=envelope),
            stream=capture.stream,
        )
    if state.boundaries != capture.boundaries or state.rows != capture.rows:
        raise ReplayError("observation_capture_rows_or_offsets_unbound")
    check_history(state.rows)


class ObservationHistory:
    """In-memory bounded candidate; callers must not ACK based on this state.

    Every position from zero is represented by a receipt. Corrections may arrive
    out of order, but an incomplete version chain cannot be read or captured.
    Invalid input stops the candidate; this module has no DLQ/skip/commit path.
    """

    def __init__(self, stream: Stream, *, partitions: int) -> None:
        if type(partitions) is not int or not 1 <= partitions <= 32:
            raise ReplayError("observation_invalid_partition_count")
        self._stream = stream
        self._next = {partition: 0 for partition in range(partitions)}
        self._rows: dict[tuple[str, int], ObservationVersion] = {}
        self._ids: dict[str, tuple[str, int]] = {}
        self._grains: dict[tuple[object, ...], str] = {}
        self._observation_grains: dict[str, tuple[object, ...]] = {}
        self._events: dict[str, str] = {}
        self._receipts: dict[tuple[int, int], Receipt] = {}

    @property
    def rows(self) -> tuple[ObservationVersion, ...]:
        return tuple(self._rows[key] for key in sorted(self._rows))

    @property
    def boundaries(self) -> tuple[Boundary, ...]:
        return tuple(Boundary(partition=p, next_offset=n) for p, n in sorted(self._next.items()))

    @property
    def receipts(self) -> tuple[Receipt, ...]:
        return tuple(self._receipts[key] for key in sorted(self._receipts))

    def process(self, record: Record, *, stream: Stream) -> Literal["inserted", "duplicate"]:
        if stream != self._stream:
            raise ReplayError("observation_stream_identity_changed")
        if record.partition not in self._next:
            raise ReplayError("observation_partition_unknown")
        envelope, row = record.envelope, record.envelope.fact
        if envelope.source_authority_id != self._stream.source_authority_id:
            raise ReplayError("observation_source_authority_changed")
        receipt = Receipt(
            partition=record.partition,
            offset=record.offset,
            event_id=envelope.event_id,
            event_sha256=digest(envelope),
            fact_sha256=digest(row),
        )
        coordinate = (record.partition, record.offset)
        next_offset = self._next[record.partition]
        if record.offset < next_offset:
            if self._receipts.get(coordinate) != receipt:
                raise ReplayError("observation_overlap_receipt_mismatch")
            return "duplicate"
        if record.offset > next_offset:
            raise ReplayError("observation_transport_offset_gap")
        if (
            envelope.event_id in self._events
            and self._events[envelope.event_id] != receipt.event_sha256
        ):
            raise ReplayError("observation_event_identity_collision")
        previous = self._rows.get(row.key)
        if previous is not None and previous != row:
            raise ReplayError("observation_fact_version_collision")
        if row.id in self._ids and self._ids[row.id] != row.key:
            raise ReplayError("observation_row_identity_collision")
        if row.grain in self._grains and self._grains[row.grain] != row.observation_id:
            raise ReplayError("observation_natural_grain_collision")
        if (
            row.observation_id in self._observation_grains
            and self._observation_grains[row.observation_id] != row.grain
        ):
            raise ReplayError("observation_history_grain_changed")
        for revision in (row.version - 1, row.version + 1):
            neighbor = self._rows.get((row.observation_id, revision))
            if neighbor is not None and (
                (revision < row.version and neighbor.available_at > row.available_at)
                or (revision > row.version and neighbor.available_at < row.available_at)
            ):
                raise ReplayError("observation_history_availability_regression")
        if len(self._receipts) >= MAX_RECEIPTS or (
            previous is None and len(self._rows) >= MAX_FACTS
        ):
            raise ReplayError("observation_candidate_size_limit")
        # No mutation before all transport, semantic, collision and bound checks.
        self._rows[row.key] = row
        self._ids[row.id] = row.key
        self._grains[row.grain] = row.observation_id
        self._observation_grains[row.observation_id] = row.grain
        self._events[envelope.event_id] = receipt.event_sha256
        self._receipts[coordinate] = receipt
        self._next[record.partition] = record.offset + 1
        return "inserted" if previous is None else "duplicate"

    def apply_batch(self, records: Iterable[Record], *, stream: Stream) -> ObservationHistory:
        """Return a new candidate. Any failure leaves this object unchanged."""
        candidate = ObservationHistory(self._stream, partitions=len(self._next))
        candidate._next = self._next.copy()
        candidate._rows = self._rows.copy()
        candidate._ids = self._ids.copy()
        candidate._grains = self._grains.copy()
        candidate._observation_grains = self._observation_grains.copy()
        candidate._events = self._events.copy()
        candidate._receipts = self._receipts.copy()
        for count, record in enumerate(records, start=1):
            if count > 2 * MAX_RECEIPTS:
                raise ReplayError("observation_replay_batch_size_limit")
            candidate.process(record, stream=stream)
        check_history(candidate.rows)
        return candidate

    def capture(self) -> Capture:
        check_history(self.rows)
        # model_construct is confined to computing the digest before full validation.
        unsigned = Capture.model_construct(
            stream=self._stream,
            boundaries=self.boundaries,
            rows=self.rows,
            receipts=self.receipts,
            capture_id="",
        )
        capture_id = (
            "observation-capture-sha256-"
            + hashlib.sha256(canonical(unsigned, exclude={"capture_id"})).hexdigest()
        )
        return Capture(
            stream=self._stream,
            boundaries=self.boundaries,
            rows=self.rows,
            receipts=self.receipts,
            capture_id=capture_id,
        )

    @classmethod
    def restore(cls, raw: bytes, *, stream: Stream) -> ObservationHistory:
        if len(raw) > MAX_CAPTURE_BYTES:
            raise ReplayError("observation_capture_size_limit")
        try:
            # Reject duplicate JSON keys/nonfinite numbers before typed parsing.
            from retailops_ai.source_snapshot.files import nonfinite, unique_keys

            decoded = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=nonfinite)
            if not isinstance(decoded, dict):
                raise ReplayError("observation_capture_object_required")
            capture = Capture.model_validate_json(raw)
        except (ValueError, ValidationError, RecursionError):
            raise ReplayError("observation_capture_rejected") from None
        if capture.stream != stream:
            raise ReplayError("observation_stream_identity_changed")
        candidate = cls(stream, partitions=len(capture.boundaries))
        facts = {digest(row): row for row in capture.rows}
        for receipt in capture.receipts:
            candidate.process(
                Record(
                    partition=receipt.partition,
                    offset=receipt.offset,
                    envelope=Envelope(
                        source_authority_id=stream.source_authority_id,
                        event_id=receipt.event_id,
                        fact=facts[receipt.fact_sha256],
                    ),
                ),
                stream=stream,
            )
        return candidate

    def as_of(self, origin: datetime) -> tuple[ObservationVersion, ...]:
        if origin.tzinfo is None or origin.utcoffset() != UTC.utcoffset(origin):
            raise ReplayError("observation_origin_requires_utc")
        check_history(self.rows)
        latest: dict[tuple[object, ...], ObservationVersion] = {}
        for row in self._rows.values():
            if row.available_at <= origin and row.business_date <= origin.date():
                previous = latest.get(row.grain)
                if previous is None or row.version > previous.version:
                    latest[row.grain] = row
        return tuple(sorted(latest.values(), key=lambda row: row.grain))

    def quantity_total(self, origin: datetime) -> int:
        rows = self.as_of(origin)
        if any(row.observed_units is None for row in rows):
            raise ReplayError("observation_quantity_unknown")
        return sum(row.observed_units or 0 for row in rows if row.observation_status != "closed")
