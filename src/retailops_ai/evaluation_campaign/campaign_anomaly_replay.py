"""Disk-backed whole-capture replay using the unchanged native event kernel.

This component verifies an explicit capture and operational replay, not its
public Source ancestry or business-day completeness. The audited feature
adapter must verify those parents and reserve its operation before using it.
Closed AI 07 capture/binding limits and native implementations are unchanged.
"""

import hashlib
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.full_raw_dq.contract import MAX_RECORDS, Delivery, Progress, parse_capture
from retailops_ai.full_raw_dq.replay import GRAIN, Replay
from retailops_ai.full_raw_dq.source import Key, ParentFacts
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    json_sha256,
)

Row = dict[str, Any]
CAPTURE_VERSION = "ai09-project-raw-dq-capture-1.0.0"
MAX_PROJECT_RECORDS = 100000000


class ProjectDelivery(Delivery):
    contract_version: Literal["ai09-project-raw-dq-capture-1.0.0"]  # type: ignore[assignment]
    offset: Annotated[int, Field(ge=0, lt=MAX_PROJECT_RECORDS)]


class ProjectProgress(Progress):
    contract_version: Literal["ai09-project-raw-dq-capture-1.0.0"]  # type: ignore[assignment]
    after_offset: Annotated[int, Field(ge=-1, lt=MAX_PROJECT_RECORDS)]


_CAPTURE: TypeAdapter[ProjectDelivery | ProjectProgress] = TypeAdapter(
    Annotated[ProjectDelivery | ProjectProgress, Field(discriminator="kind")]
)


class CampaignAnomalyReplayPlan(Contract):
    version: Literal["ai09-anomaly-disk-replay-plan-1.0.0"] = "ai09-anomaly-disk-replay-plan-1.0.0"
    capture_sha256: Sha256
    capture_version: Literal["raw-dq-capture-2.0.0", "ai09-project-raw-dq-capture-1.0.0"] = (
        "ai09-project-raw-dq-capture-1.0.0"
    )
    capture_records: Annotated[int, Field(ge=1, le=MAX_PROJECT_RECORDS)]
    parent_events: Annotated[int, Field(ge=1, le=MAX_PROJECT_RECORDS)]
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)]
    max_group_rows: Annotated[int, Field(ge=1, le=MAX_PROJECT_RECORDS)] = 100000
    max_group_bytes: Annotated[int, Field(ge=1024, le=1024**3)] = 64 * 1024**2
    transaction_records: Annotated[int, Field(ge=1, le=4096)] = 256
    population: Literal["every_capture_record_global_offsets_and_identity_state"] = (
        "every_capture_record_global_offsets_and_identity_state"
    )

    @model_validator(mode="after")
    def legacy_capture_limit(self) -> Self:
        if self.capture_version == "raw-dq-capture-2.0.0" and self.capture_records > MAX_RECORDS:
            raise ValueError("campaign_anomaly_replay_legacy_record_budget")
        return self


class CampaignAnomalyReplayResourceError(RuntimeError):
    """Resource exhaustion fails the operation, never becomes a quarantined event."""


class CampaignAnomalyReplayStateError(RuntimeError):
    """Private-state corruption is fatal, never an operational quarantine."""


def _capture(value: Row, version: str) -> Delivery | Progress:
    if value.get("contract_version") != version:
        raise SnapshotError("campaign_anomaly_replay_capture_version")
    if version == "raw-dq-capture-2.0.0":
        # Legacy captures keep every legacy parsing/offset/body rule unchanged.
        return parse_capture(value)
    result = _CAPTURE.validate_json(canonical_json(value))
    normalized = result.model_dump(mode="json")
    if canonical_json(normalized) != canonical_json(value) or result.record_id != (
        "raw-record-sha256-"
        + json_sha256({k: v for k, v in normalized.items() if k != "record_id"})
    ):
        raise SnapshotError("campaign_anomaly_replay_capture_identity")
    if isinstance(result, ProjectDelivery):
        # The inherited native body budget is in characters; preserve its bytes guard too.
        from retailops_ai.raw_dq.contract import MAX_BODY_BYTES

        if len(result.body_utf8.encode()) > MAX_BODY_BYTES:
            raise SnapshotError("campaign_anomaly_replay_body_budget")
    elif stamp(result.complete_through) > stamp(result.received_at):
        raise SnapshotError("campaign_anomaly_replay_progress_before_frontier")
    return result


class _EventHashes(dict[str, str]):
    """Only the three dict operations used by the pinned native event kernel."""

    def __init__(self, database: sqlite3.Connection, trace: Any) -> None:
        self.database = database
        self.trace = trace
        self.count = 0

    def __contains__(self, key: object) -> bool:
        return (
            isinstance(key, str)
            and self.database.execute(
                "SELECT 1 FROM event_hashes WHERE identifier=?", (key,)
            ).fetchone()
            is not None
        )

    def __getitem__(self, key: str) -> str:
        row = self.database.execute(
            "SELECT digest FROM event_hashes WHERE identifier=?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return str(row[0])

    def __setitem__(self, key: str, value: str) -> None:
        # The native kernel checks membership before accepting an event ID and
        # never overwrites it. Retain an independent append-time trace: checking
        # receipts alone cannot authenticate this global deduplication state.
        sequence = self.count + 1
        try:
            self.database.execute(
                "INSERT INTO event_hashes(sequence,identifier,digest) VALUES(?,?,?)",
                (sequence, key, value),
            )
        except sqlite3.IntegrityError:
            raise CampaignAnomalyReplayStateError(
                "campaign_anomaly_replay_private_event_identity_changed"
            ) from None
        self.trace.update(canonical_json((sequence, key, value)) + b"\n")
        self.count = sequence


class _MatchedParent(ParentFacts):
    """Load one grain's prior state only after native wire/identity validation."""

    def __init__(self, parent: ParentFacts, matched: Callable[[Key, Row], None]) -> None:
        self._parent, self._matched = parent, matched

    def match(self, event: Row) -> tuple[Key, Row]:
        key, fact = self._parent.match(event)
        self._matched(key, fact)
        return key, fact


class CampaignAnomalyDiskReplay:
    """One complete capture; SQL state stays global across every native grain.

    The native kernel retains one prior grain and one current event in memory.
    This does not bound the caller's ParentFacts implementation: a full public
    Source adapter must supply independently verified, bounded parent access.
    Outputs become readable only after the exact preregistered capture hash and
    record count complete. Fatal failures permanently close this instance.
    """

    def __init__(self, parent: ParentFacts, plan: CampaignAnomalyReplayPlan, scratch: Path) -> None:
        self.parent = parent
        self.plan = CampaignAnomalyReplayPlan.model_validate_json(plan.model_dump_json())
        self.scratch = checked_directory(scratch)
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._database: sqlite3.Connection | None = None
        self._used = self._failed = self._complete = False
        self._calls = 0
        self._pending_records = 0
        self._trace = hashlib.sha256()
        self._outputs = {
            name: hashlib.sha256()
            for name in (
                "receipts",
                "facts",
                "revisions",
                "progress",
                "quarantine",
                "quarantine_captures",
                "event_hashes",
            )
        }
        self.maximum_index_bytes = self.maximum_group_rows = self.maximum_group_bytes = 0

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_anomaly_replay_single_use")
        self._used = True
        try:
            if len(self.parent.facts) != self.plan.parent_events:
                raise SnapshotError("campaign_anomaly_replay_parent_count_binding")
            self._temporary = tempfile.TemporaryDirectory(
                prefix=".ai09-anomaly-replay-", dir=self.scratch
            )
            self.path = Path(self._temporary.name) / "replay.sqlite"
            self.path.touch(mode=0o600, exist_ok=False)
            self._database = sqlite3.connect(self.path)
            self._database.execute("PRAGMA cache_size=-4096")
            self._database.execute("PRAGMA temp_store=FILE")
            self._database.execute("PRAGMA mmap_size=0")
            self._database.executescript(
                "CREATE TABLE event_hashes(sequence INTEGER PRIMARY KEY,"
                "identifier TEXT UNIQUE NOT NULL,digest TEXT NOT NULL);"
                "CREATE TABLE receipts(sequence INTEGER PRIMARY KEY,identifier TEXT UNIQUE,payload BLOB,digest TEXT);"
                "CREATE TABLE facts(sequence INTEGER PRIMARY KEY,event_type TEXT,business_id TEXT,"
                "grain BLOB,available_at TEXT,payload BLOB,digest TEXT,UNIQUE(event_type,business_id));"
                "CREATE INDEX fact_grain ON facts(grain,sequence);"
                "CREATE TABLE revisions(sequence INTEGER PRIMARY KEY,grain BLOB,payload BLOB,digest TEXT);"
                "CREATE INDEX revision_grain ON revisions(grain,sequence);"
                "CREATE TABLE progress(sequence INTEGER PRIMARY KEY,payload BLOB,digest TEXT);"
                "CREATE TABLE quarantine(sequence INTEGER PRIMARY KEY,payload BLOB,digest TEXT,capture BLOB,capture_digest TEXT);"
            )
            self._kernel = Replay(_MatchedParent(self.parent, self._matched))
            self._events = _EventHashes(self._database, self._outputs["event_hashes"])
            self._kernel.events = self._events
            self._budget()
        except BaseException:
            self._failed = True
            self.close()
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None and not self._complete:
                raise SnapshotError("campaign_anomaly_replay_capture_not_finished")
            if args[0] is None:
                self.verify_outputs()
        finally:
            self.close()

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
            self._database = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def _db(self, *, outputs: bool = False) -> sqlite3.Connection:
        if self._database is None or self._failed or outputs and not self._complete:
            raise SnapshotError("campaign_anomaly_replay_state_unavailable")
        return self._database

    def _budget(self) -> None:
        # Count both allocated files (including the rollback journal) and dirty
        # database pages that have not yet reached the filesystem.
        sizes = {
            path: max(info.st_size, info.st_blocks * 512)
            for path in self.path.parent.iterdir()
            if path.is_file()
            for info in (path.stat(),)
        }
        database = self._db()
        logical = (
            database.execute("PRAGMA page_count").fetchone()[0]
            * database.execute("PRAGMA page_size").fetchone()[0]
        )
        size = max(sizes.get(self.path, 0), logical) + sum(
            allocated for path, allocated in sizes.items() if path != self.path
        )
        self.maximum_index_bytes = max(self.maximum_index_bytes, size)
        if size > self.plan.max_index_bytes:
            raise CampaignAnomalyReplayResourceError("campaign_anomaly_replay_index_budget")

    @staticmethod
    def _checked(raw: bytes, digest: str) -> Row:
        if hashlib.sha256(raw).hexdigest() != digest:
            raise CampaignAnomalyReplayStateError("campaign_anomaly_replay_private_row_changed")
        try:
            value = decode_json(raw)
            if canonical_json(value) != raw:
                raise ValueError("not canonical")
        except ValueError:
            raise CampaignAnomalyReplayStateError(
                "campaign_anomaly_replay_private_row_invalid"
            ) from None
        return value

    def _record(self, table: str, raw: bytes) -> str:
        self._outputs[table].update(raw + b"\n")
        return hashlib.sha256(raw).hexdigest()

    def _matched(self, key: Key, fact: Row) -> None:
        database = self._db()
        grain = tuple(fact[k] for k in GRAIN)
        raw_grain = canonical_json(grain)
        rows: list[Row] = []
        size = 0
        for raw, digest in database.execute(
            "SELECT payload,digest FROM facts WHERE grain=? ORDER BY sequence", (raw_grain,)
        ):
            size += len(raw)
            if len(rows) + 1 > self.plan.max_group_rows or size > self.plan.max_group_bytes:
                raise CampaignAnomalyReplayResourceError(
                    "campaign_anomaly_replay_native_group_budget"
                )
            rows.append(self._checked(raw, digest))
        self.maximum_group_rows = max(self.maximum_group_rows, len(rows))
        self.maximum_group_bytes = max(self.maximum_group_bytes, size)
        self._kernel.groups.clear()
        self._kernel.groups[grain] = rows
        self._kernel.latest.clear()
        previous = database.execute(
            "SELECT payload,digest FROM revisions WHERE grain=? ORDER BY sequence DESC LIMIT 1",
            (raw_grain,),
        ).fetchone()
        if previous is not None:
            self._kernel.latest[grain] = self._checked(previous[0], previous[1])
        self._kernel.business = (
            {key}
            if database.execute(
                "SELECT 1 FROM facts WHERE event_type=? AND business_id=?", key
            ).fetchone()
            is not None
            else set()
        )

    def consume(self, payload: Row) -> Row:
        database = self._db()
        if self._complete:
            raise SnapshotError("campaign_anomaly_replay_already_finished")
        try:
            if self._calls >= self.plan.capture_records:
                raise SnapshotError("campaign_anomaly_replay_extra_capture_record")
            record = _capture(payload, self.plan.capture_version)
            if self._pending_records == 0:
                database.execute("BEGIN")
            prior = database.execute(
                "SELECT payload,digest FROM receipts WHERE identifier=?", (record.record_id,)
            ).fetchone()
            if prior is not None:
                receipt = self._checked(prior[0], prior[1])
            else:
                receipt = self._consume_new(record)
                raw = canonical_json(receipt)
                database.execute(
                    "INSERT INTO receipts(identifier,payload,digest) VALUES(?,?,?)",
                    (record.record_id, raw, self._record("receipts", raw)),
                )
            self._budget()
            self._pending_records += 1
            if self._pending_records == self.plan.transaction_records:
                database.commit()
                self._pending_records = 0
                self._budget()
            self._trace.update(canonical_json(payload) + b"\n")
            self._calls += 1
            return receipt
        except BaseException:
            database.rollback()
            self._failed = True
            raise

    def _consume_new(self, record: Delivery | Progress) -> Row:
        kernel, database = self._kernel, self._db()
        if kernel.last_received is not None and stamp(record.received_at) < stamp(
            kernel.last_received
        ):
            raise SnapshotError("full_dq_delivery_time_regressed")
        kernel.facts, kernel.revisions, kernel.quarantine = [], [], []
        kernel.groups.clear()
        kernel.latest.clear()
        kernel.business.clear()
        if isinstance(record, Progress):
            if record.after_offset != kernel.next_offset - 1:
                raise SnapshotError("full_dq_progress_position_mismatch")
            if kernel.watermark is not None and stamp(record.complete_through) <= stamp(
                kernel.watermark
            ):
                raise SnapshotError("full_dq_frontier_not_advanced")
            kernel.watermark = record.complete_through
            progress = {
                "raw_ref": record.record_id,
                "known_at": record.received_at,
                "complete_through": record.complete_through,
                "after_offset": record.after_offset,
                "scope": record.scope,
            }
            raw = canonical_json(progress)
            database.execute(
                "INSERT INTO progress(payload,digest) VALUES(?,?)",
                (raw, self._record("progress", raw)),
            )
            action, reason = "progress", "declared_parent_stream_frontier"
        else:
            if record.offset != kernel.next_offset:
                raise SnapshotError("full_dq_noncontiguous_offset")
            action, reason = kernel._event(record)
            for group in kernel.groups.values():
                size = sum(len(canonical_json(fact)) for fact in group)
                if len(group) > self.plan.max_group_rows or size > self.plan.max_group_bytes:
                    raise CampaignAnomalyReplayResourceError(
                        "campaign_anomaly_replay_native_group_budget"
                    )
                self.maximum_group_rows = max(self.maximum_group_rows, len(group))
                self.maximum_group_bytes = max(self.maximum_group_bytes, size)
            kernel.next_offset += 1
            for fact in kernel.facts:
                raw = canonical_json(fact)
                database.execute(
                    "INSERT INTO facts(event_type,business_id,grain,available_at,payload,digest) VALUES(?,?,?,?,?,?)",
                    (
                        fact["event_type"],
                        fact["business_id"],
                        canonical_json(tuple(fact[k] for k in GRAIN)),
                        fact["available_at"],
                        raw,
                        self._record("facts", raw),
                    ),
                )
            for revision in kernel.revisions:
                raw = canonical_json(revision)
                database.execute(
                    "INSERT INTO revisions(grain,payload,digest) VALUES(?,?,?)",
                    (
                        canonical_json(tuple(revision[k] for k in GRAIN)),
                        raw,
                        self._record("revisions", raw),
                    ),
                )
            for row in kernel.quarantine:
                raw = canonical_json(row)
                capture = canonical_json(record.model_dump(mode="json"))
                database.execute(
                    "INSERT INTO quarantine(payload,digest,capture,capture_digest) VALUES(?,?,?,?)",
                    (
                        raw,
                        self._record("quarantine", raw),
                        capture,
                        self._record("quarantine_captures", capture),
                    ),
                )
        kernel.last_received = record.received_at
        return {"raw_ref": record.record_id, "action": action, "reason": reason}

    def finish(self) -> dict[str, Any]:
        database = self._db()
        if self._complete:
            raise SnapshotError("campaign_anomaly_replay_already_finished")
        if (
            self._calls != self.plan.capture_records
            or self._trace.hexdigest() != self.plan.capture_sha256
        ):
            self._failed = True
            raise SnapshotError("campaign_anomaly_replay_capture_extent_or_hash")
        try:
            self._budget()
            self.verify_outputs()
            database.commit()
            self._pending_records = 0
            self._budget()
        except BaseException:
            database.rollback()
            self._failed = True
            raise
        counts: dict[str, int] = {}
        for (raw,) in database.execute("SELECT payload FROM receipts ORDER BY sequence"):
            action = decode_json(raw)["action"]
            counts[action] = counts.get(action, 0) + 1
        if self._kernel.next_offset != sum(
            counts.get(k, 0)
            for k in ("accepted", "duplicate_event", "duplicate_business", "quarantined")
        ):
            self._failed = True
            raise SnapshotError("full_dq_accounting_mismatch")
        accepted = database.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        self._complete = True
        return {
            "version": "ai09-native-disk-replay-result-1.0.0",
            "capture_records": self._calls,
            "unique_receipts": sum(counts.values()),
            "capture_sha256": self._trace.hexdigest(),
            "raw_events": self._kernel.next_offset,
            "actions": counts,
            "accepted_parent_facts": accepted,
            "missing_parent_facts": self.plan.parent_events - accepted,
            "declared_source_watermark": self._kernel.watermark,
            "max_accepted_event_time": self._kernel.max_event_time,
            "maximum_index_bytes": self.maximum_index_bytes,
            "maximum_native_group_rows": self.maximum_group_rows,
            "maximum_native_group_bytes": self.maximum_group_bytes,
            "output_sha256": {name: trace.hexdigest() for name, trace in self._outputs.items()},
            "source_parent_verified": False,
            "business_event_day_completeness": "not_qualified",
            "transport_durability_proven": False,
            "quality_qualified": False,
            "stage_ready": False,
        }

    def rows(
        self, table: Literal["receipts", "facts", "revisions", "progress", "quarantine"]
    ) -> Iterator[Row]:
        database = self._db(outputs=True)
        if table not in {"receipts", "facts", "revisions", "progress", "quarantine"}:
            raise SnapshotError("campaign_anomaly_replay_output_table_invalid")
        queries = {
            "receipts": "SELECT payload,digest FROM receipts ORDER BY sequence",
            "facts": "SELECT payload,digest FROM facts ORDER BY sequence",
            "revisions": "SELECT payload,digest FROM revisions ORDER BY sequence",
            "progress": "SELECT payload,digest FROM progress ORDER BY sequence",
            "quarantine": "SELECT payload,digest FROM quarantine ORDER BY sequence",
        }
        for raw, digest in database.execute(queries[table]):
            self._db(outputs=True)
            yield self._checked(raw, digest)

    def verify_outputs(self) -> None:
        """Recheck every append-only output against the actual consume-time trace."""
        database = self._db()
        hashes = {name: hashlib.sha256() for name in self._outputs}
        try:
            count = 0
            for row in database.execute(
                "SELECT sequence,identifier,digest FROM event_hashes ORDER BY sequence"
            ):
                count += 1
                if row[0] != count:
                    raise ValueError("event identity sequence")
                hashes["event_hashes"].update(canonical_json(row) + b"\n")
            if count != self._events.count:
                raise ValueError("event identity extent")
            for table in ("receipts", "facts", "revisions", "progress", "quarantine"):
                queries = {
                    "receipts": "SELECT identifier,payload,digest FROM receipts ORDER BY sequence",
                    "facts": "SELECT event_type,business_id,grain,available_at,payload,digest FROM facts ORDER BY sequence",
                    "revisions": "SELECT grain,payload,digest FROM revisions ORDER BY sequence",
                    "progress": "SELECT payload,digest FROM progress ORDER BY sequence",
                    "quarantine": "SELECT capture,capture_digest,payload,digest FROM quarantine ORDER BY sequence",
                }
                for *header, raw, digest in database.execute(queries[table]):
                    value = self._checked(raw, digest)
                    if table == "receipts" and header[0] != value["raw_ref"]:
                        raise ValueError("receipt key")
                    if table == "facts" and header != [
                        value["event_type"],
                        value["business_id"],
                        canonical_json(tuple(value[k] for k in GRAIN)),
                        value["available_at"],
                    ]:
                        raise ValueError("fact key")
                    if table == "revisions" and header[0] != canonical_json(
                        tuple(value[k] for k in GRAIN)
                    ):
                        raise ValueError("revision key")
                    if table == "quarantine":
                        self._quarantine_capture(value, header[0], header[1])
                        hashes["quarantine_captures"].update(header[0] + b"\n")
                    hashes[table].update(raw + b"\n")
            if any(
                hashes[name].hexdigest() != trace.hexdigest()
                for name, trace in self._outputs.items()
            ):
                raise ValueError("output trace")
        except (ValueError, TypeError, KeyError, ArithmeticError):
            self._failed = True
            raise CampaignAnomalyReplayStateError(
                "campaign_anomaly_replay_private_output_changed"
            ) from None
        except CampaignAnomalyReplayStateError:
            self._failed = True
            raise

    def _quarantine_capture(self, row: Row, raw: bytes, digest: str) -> Row:
        value = self._checked(raw, digest)
        try:
            record = _capture(value, self.plan.capture_version)
            if not isinstance(record, Delivery) or any(
                row[key] != expected
                for key, expected in {
                    "raw_ref": record.record_id,
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "received_at": record.received_at,
                    "body_sha256": hashlib.sha256(record.body_utf8.encode()).hexdigest(),
                }.items()
            ):
                raise ValueError("capture binding")
        except (ValueError, TypeError, KeyError):
            raise CampaignAnomalyReplayStateError(
                "campaign_anomaly_replay_private_capture_changed"
            ) from None
        return value

    def quarantined_captures(self) -> Iterator[tuple[Row, Row]]:
        for raw, digest, capture, capture_digest in self._db(outputs=True).execute(
            "SELECT payload,digest,capture,capture_digest FROM quarantine ORDER BY sequence"
        ):
            self._db(outputs=True)
            row = self._checked(raw, digest)
            yield row, self._quarantine_capture(row, capture, capture_digest)

    def fact(self, event_type: str, business_id: str) -> Row | None:
        row = (
            self._db(outputs=True)
            .execute(
                "SELECT event_type,business_id,grain,available_at,payload,digest FROM facts WHERE event_type=? AND business_id=?",
                (event_type, business_id),
            )
            .fetchone()
        )
        if row is None:
            return None
        value = self._checked(row[4], row[5])
        if list(row[:4]) != [
            value["event_type"],
            value["business_id"],
            canonical_json(tuple(value[k] for k in GRAIN)),
            value["available_at"],
        ]:
            raise CampaignAnomalyReplayStateError(
                "campaign_anomaly_replay_private_fact_key_changed"
            )
        return value

    def accepted_fact(self, event_type: str, business_id: str, as_of: str) -> Row | None:
        self._db(outputs=True)
        try:
            cutoff = stamp(as_of)
        except ValueError:
            raise SnapshotError("campaign_anomaly_replay_utc_cutoff_required") from None
        if cutoff.utcoffset() != timedelta(0):
            raise SnapshotError("campaign_anomaly_replay_utc_cutoff_required")
        value = self.fact(event_type, business_id)
        return value if value is not None and stamp(value["available_at"]) <= cutoff else None
