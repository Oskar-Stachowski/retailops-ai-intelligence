"""Full public Source replay and bounded native canonical anomaly parents.

The enclosing campaign controller must reserve the entire Source read first.
This internal physical adapter does not grant journal/final/fit permission or
prove generation ancestry. It performs actual complete snapshot/curated replay,
not verification of a caller-supplied parent digest alone.
"""

import hashlib
import sqlite3
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal, Inexact, localcontext
from pathlib import Path
from typing import Annotated, Any, Literal, Self, overload

from pydantic import Field

from retailops_ai.curated.builder import iter_rows
from retailops_ai.curated.contract import Digest, columns_for, decoded, encoded
from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.full_raw_dq.source import TABLES, Key, ParentFacts, business_key, projection
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
)
from retailops_ai.source_snapshot.protocol import inspect_snapshot, verify_metadata
from retailops_ai.source_snapshot.tables import arrow_type

Row = dict[str, Any]


class CampaignAnomalyParentStateError(RuntimeError):
    """Private-state failure must never become a native event quarantine."""


class CampaignAnomalyParentPlan(Contract):
    version: Literal["ai09-anomaly-public-parent-plan-1.0.0"] = (
        "ai09-anomaly-public-parent-plan-1.0.0"
    )
    source: PhysicalSourceSpec
    runtime: PreparationRuntime
    parent_events: Annotated[int, Field(ge=1, le=20000000)]
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)]
    max_selected_bytes: Annotated[int, Field(ge=4096, le=2 * 1024**2)] = 512 * 1024
    max_record_bytes: Annotated[int, Field(ge=1024, le=65536)] = 65536
    sqlite_cache_kib: Literal[4096] = 4096
    population: Literal["all_canonical_sales_and_native_return_claims"] = (
        "all_canonical_sales_and_native_return_claims"
    )


class _ParentRows(Mapping[Key, Row]):
    def __init__(self, owner: "CampaignAnomalyPublicParent", kind: Literal["event", "fact"]):
        self.owner, self.kind = owner, kind

    def __len__(self) -> int:
        self.owner._db()
        return self.owner.plan.parent_events

    def __iter__(self) -> Iterator[Key]:
        for row in self.owner._db().execute(
            "SELECT event_type,business_id FROM parents ORDER BY sequence"
        ):
            self.owner._db()
            yield str(row[0]), str(row[1])

    def __getitem__(self, key: Key) -> Row:
        # Match dict membership used by the native quarantine classifier:
        # unhashable wire keys raise TypeError; other unsupported keys miss.
        hash(key)
        if (
            not isinstance(key, tuple)
            or len(key) != 2
            or not all(isinstance(part, str) for part in key)
        ):
            raise KeyError(key)
        queries = {
            "event": "SELECT event,event_sha256 FROM parents WHERE event_type=? AND business_id=?",
            "fact": "SELECT fact,fact_sha256 FROM parents WHERE event_type=? AND business_id=?",
        }
        row = self.owner._db().execute(queries[self.kind], key).fetchone()
        if row is None:
            raise KeyError(key)
        value = self.owner._checked(row[0], row[1])
        actual = (
            business_key(value)
            if self.kind == "event"
            else (value["event_type"], value["business_id"])
        )
        if actual != key:
            raise CampaignAnomalyParentStateError("campaign_anomaly_parent_private_key_changed")
        return value


class _ParentIDs(Mapping[str, Key]):
    def __init__(self, owner: "CampaignAnomalyPublicParent") -> None:
        self.owner = owner

    def __len__(self) -> int:
        self.owner._db()
        return self.owner.plan.parent_events

    def __iter__(self) -> Iterator[str]:
        for (identifier,) in self.owner._db().execute(
            "SELECT identifier FROM parents ORDER BY sequence"
        ):
            self.owner._db()
            yield str(identifier)

    def __getitem__(self, identifier: str) -> Key:
        hash(identifier)
        if not isinstance(identifier, str):
            raise KeyError(identifier)
        row = (
            self.owner._db()
            .execute(
                "SELECT event_type,business_id,event,event_sha256 FROM parents WHERE identifier=?",
                (identifier,),
            )
            .fetchone()
        )
        if row is None:
            raise KeyError(identifier)
        if self.owner._checked(row[2], row[3])["event_id"] != identifier:
            raise CampaignAnomalyParentStateError(
                "campaign_anomaly_parent_private_identifier_changed"
            )
        return str(row[0]), str(row[1])


class _ParentEvents(Sequence[Row]):
    def __init__(self, owner: "CampaignAnomalyPublicParent") -> None:
        self.owner = owner

    def __len__(self) -> int:
        self.owner._db()
        return self.owner.plan.parent_events

    def __iter__(self) -> Iterator[Row]:
        for raw, checksum in self.owner._db().execute(
            "SELECT event,event_sha256 FROM parents ORDER BY occurred,event_type,business_id"
        ):
            self.owner._db()
            yield self.owner._checked(raw, checksum)

    @overload
    def __getitem__(self, index: int) -> Row: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[Row]: ...

    def __getitem__(self, index: int | slice) -> Row | Sequence[Row]:
        if isinstance(index, slice):
            # A whole-source slice must not silently materialize its full list.
            raise SnapshotError("campaign_anomaly_parent_stream_instead_of_slice")
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        row = (
            self.owner._db()
            .execute(
                "SELECT event,event_sha256 FROM parents ORDER BY occurred,event_type,business_id LIMIT 1 OFFSET ?",
                (index,),
            )
            .fetchone()
        )
        return self.owner._checked(row[0], row[1])


class _NativeDiskParent(ParentFacts):
    """Read-only lazy containers satisfy the unchanged native match operations."""

    def __init__(self, owner: "CampaignAnomalyPublicParent") -> None:
        # The native constructor deepcopies whole lists/dicts. These immutable
        # Mapping/Sequence implementations retain every entry on disk instead.
        self.events = _ParentEvents(owner)  # type: ignore[assignment]
        self.canonical = _ParentRows(owner, "event")  # type: ignore[assignment]
        self.ids = _ParentIDs(owner)  # type: ignore[assignment]
        self.facts = _ParentRows(owner, "fact")  # type: ignore[assignment]


class CampaignAnomalyPublicParent:
    """One full sealed Source, actual native projection, no parent sampling.

    Parent values are available inside this context after complete loading.
    The physical receipt becomes available only after successful context exit
    and final input/runtime rechecks. A caller must withhold its own artifacts
    until this exit and its encompassing journal operation both complete.
    """

    def __init__(
        self, snapshot: Path, curated: Path, plan: CampaignAnomalyParentPlan, scratch: Path
    ) -> None:
        self.snapshot, self.curated, self.scratch = snapshot, curated, scratch
        self.plan = CampaignAnomalyParentPlan.model_validate_json(plan.model_dump_json())
        self._stack = ExitStack()
        self._database: sqlite3.Connection | None = None
        self._used = self._failed = self._loaded = self._complete = False
        self._receipt: Row = {}
        self.stats = {
            "stored_source_rows": 0,
            "projected_parent_events": 0,
            "maximum_selected_bytes": 0,
            "maximum_index_bytes": 0,
        }

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("campaign_anomaly_parent_single_use")
        self._used = True
        try:
            self._open()
        except BaseException:
            self._failed = True
            self._stack.close()
            self._database = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        try:
            if args[0] is None and not self._failed:
                self._replay.check_parents()
                self._budget()
                if self._index_hashes() != (
                    self.native_events_sha256,
                    self.native_facts_trace_sha256,
                ):
                    raise CampaignAnomalyParentStateError(
                        "campaign_anomaly_parent_private_index_changed"
                    )
                self._receipt = {
                    "version": "ai09-anomaly-public-parent-receipt-1.0.0",
                    "source": self.plan.source.model_dump(mode="json"),
                    "runtime": self.plan.runtime.model_dump(mode="json"),
                    "plan_sha256": canonical_sha256(self.plan.model_dump(mode="json")),
                    "snapshot_inventory_sha256": self._replay.snapshot_inventory_sha256,
                    "curated_inventory_sha256": self._replay.curated_inventory_sha256,
                    "logical_curated_sha256": self._replay.logical_curated_sha256,
                    "native_events_sha256": self.native_events_sha256,
                    "native_facts_trace_sha256": self.native_facts_trace_sha256,
                    "source_tables": self._replay.source_tables,
                    "source_rows": self._replay.source_rows,
                    "stats": deepcopy(self.stats),
                    "physical_source_and_curated_replay_passed": True,
                    "native_metadata_use_cases_verified": list(self._metadata_use_cases),
                    "closed_ai07_anomaly_source_qualification_claimed": False,
                    "source_generation_ancestry_verified": False,
                    "audited_read_authorization_proven": False,
                    "business_event_day_completeness": "not_qualified",
                    "quality_qualified": False,
                    "stage_ready": False,
                }
        except BaseException:
            self._failed = True
            raise
        finally:
            try:
                # ExitStack propagates the real body error to the verifier;
                # no receipt is accepted when any final physical check fails.
                self._stack.__exit__(*args)
            except BaseException:
                self._failed = True
                raise
            finally:
                self._database = None
        self._complete = args[0] is None and not self._failed

    def receipt(self) -> Row:
        if not self._complete or self._failed:
            raise SnapshotError("campaign_anomaly_parent_not_completed")
        return deepcopy(self._receipt)

    def _db(self) -> sqlite3.Connection:
        if self._database is None or self._failed:
            raise CampaignAnomalyParentStateError("campaign_anomaly_parent_state_unavailable")
        return self._database

    def _checked(self, raw: bytes, checksum: str) -> Row:
        if hashlib.sha256(raw).hexdigest() != checksum:
            raise CampaignAnomalyParentStateError("campaign_anomaly_parent_private_row_changed")
        return decode_json(raw)

    def _budget(self, digest: Digest | None = None, digest_path: Path | None = None) -> None:
        sizes = {
            path: max(info.st_size, info.st_blocks * 512)
            for path in self.path.parent.iterdir()
            if path.is_file()
            for info in (path.stat(),)
        }
        databases = [(self.path, self._db())]
        if digest is not None and digest_path is not None:
            databases.append((digest_path, digest.db))
        for path, database in databases:
            logical = (
                database.execute("PRAGMA page_count").fetchone()[0]
                * database.execute("PRAGMA page_size").fetchone()[0]
            )
            sizes[path] = max(sizes.get(path, 0), logical)
        size = sum(sizes.values())
        self.stats["maximum_index_bytes"] = max(self.stats["maximum_index_bytes"], size)
        if size > self.plan.max_index_bytes:
            raise SnapshotError("campaign_anomaly_parent_combined_index_budget")

    def _open(self) -> None:
        specification = self.plan.source
        if specification.schema_version not in {"1.1.0", "1.2.0"}:
            raise SnapshotError("campaign_anomaly_parent_inventory_schema_required")
        scratch = checked_directory(self.scratch)
        for original in (self.snapshot.resolve(), self.curated.resolve()):
            if scratch == original or original in scratch.parents:
                raise SnapshotError("campaign_anomaly_parent_scratch_inside_source")
        limits = physical_limits(specification)
        replay = self._stack.enter_context(
            _open_verified_source_parent(
                self.snapshot, self.curated, specification, limits=limits, runtime=self.plan.runtime
            )
        )
        self._replay = replay
        # The common verifier privately copies these two sibling roots and
        # already verifies every snapshot byte, typed row and curated transform.
        private_snapshot = replay.curated.parent / "snapshot"
        source = inspect_snapshot(private_snapshot, False, limits)
        # Snapshot1.1 never declared the later AI07 anomaly_source gate. Its
        # complete public inventory facts receive a new native operational
        # projection here; the closed1.2 qualification is never borrowed.
        self._metadata_use_cases = (
            ("anomaly_source",)
            if specification.schema_version == "1.2.0"
            else ("forecast_source", "inventory_source")
        )
        verify_metadata(private_snapshot, source, self._metadata_use_cases)
        document = replay.manifest
        specs = {row["table"]: row for row in document["tables"]}
        if (
            not set(TABLES) <= set(specs)
            or not document["readiness"]["inventory_ready"]
            or document["evaluation_truth"]["included"]
            or specs["sales"]["row_count"] + specs["return_events"]["row_count"]
            != self.plan.parent_events
        ):
            raise SnapshotError("campaign_anomaly_parent_full_population_binding")
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".ai09-anomaly-parent-", dir=scratch)
            )
        )
        self.path = directory / "parents.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        database = sqlite3.connect(self.path)
        self._database = database
        self._stack.callback(database.close)
        database.execute("PRAGMA cache_size=-4096")
        database.execute("PRAGMA mmap_size=0")
        database.execute("PRAGMA temp_store=FILE")
        database.executescript(
            "CREATE TABLE source_rows(name TEXT,position INTEGER,natural_key TEXT,secondary_key TEXT,payload BLOB,PRIMARY KEY(name,position));"
            "CREATE INDEX source_natural ON source_rows(name,natural_key,position);"
            "CREATE INDEX source_secondary ON source_rows(name,secondary_key,position);"
            "CREATE TABLE parents(sequence INTEGER PRIMARY KEY,event_type TEXT,business_id TEXT,identifier TEXT UNIQUE,occurred TEXT,event BLOB,fact BLOB,event_sha256 TEXT,fact_sha256 TEXT,UNIQUE(event_type,business_id));"
            "CREATE INDEX parent_event_order ON parents(occurred,event_type,business_id);"
        )
        self._columns = {name: columns_for(name, specification.schema_version) for name in TABLES}
        self._budget()
        for name in TABLES:
            spec = specs[name]
            path = directory / "digest.sqlite"
            digest = Digest(path, self._columns[name], spec["grain"])
            try:
                # Avoid an unaccounted external sort in native Digest.summary.
                digest.db.execute("CREATE INDEX canonical_sort ON rows(record)")
                for position, row in enumerate(
                    iter_rows(replay.curated, spec["files"], limits.batch_rows)
                ):
                    raw = encoded(row)
                    if len(raw) > self.plan.max_record_bytes:
                        raise SnapshotError("campaign_anomaly_parent_source_record_budget")
                    digest.add(row)
                    key = (
                        row["sale_id"]
                        if name in {"sale_price_references", "inventory_sales"}
                        else row["id"]
                    )
                    secondary = row["order_reference"] if name == "orders" else None
                    database.execute(
                        "INSERT INTO source_rows VALUES(?,?,?,?,?)",
                        (name, position, key, secondary, raw),
                    )
                    self.stats["stored_source_rows"] += 1
                    if (position + 1) % limits.batch_rows == 0:
                        database.commit()
                        digest.db.commit()
                        self._budget(digest, path)
                if any(spec[key] != value for key, value in digest.summary().items()):
                    raise SnapshotError("campaign_anomaly_parent_table_changed_during_load")
                database.commit()
                self._budget(digest, path)
            finally:
                digest.close()
                path.unlink(missing_ok=True)
        for name in ("sales", "return_events"):
            for (raw,) in database.execute(
                "SELECT payload FROM source_rows WHERE name=? ORDER BY position", (name,)
            ):
                current = self._native_row(raw, name)
                pieces: dict[str, list[Row]] = {table: [] for table in TABLES}
                pieces[name] = [current]
                sale_id = current["id"] if name == "sales" else current["sale_id"]
                pieces["inventory_sales"] = [self._source_row("inventory_sales", sale_id)]
                pieces["orders"] = [
                    self._source_row(
                        "orders",
                        current["order_reference"] if name == "sales" else current["order_id"],
                        secondary=name == "sales",
                    )
                ]
                if name == "sales":
                    pieces["sale_price_references"] = [
                        self._source_row("sale_price_references", sale_id)
                    ]
                    pieces["product_catalog"] = [
                        self._source_row("product_catalog", current["product_id"])
                    ]
                size = sum(len(encoded(row)) for rows in pieces.values() for row in rows)
                self.stats["maximum_selected_bytes"] = max(
                    self.stats["maximum_selected_bytes"], size
                )
                if size > self.plan.max_selected_bytes:
                    raise SnapshotError("campaign_anomaly_parent_projection_piece_budget")
                native = projection(pieces, document["descriptor"]["source_parameters"]["seed"])
                event = native.events[0]
                key = business_key(event)
                fact = native.facts[key]
                event_raw, fact_raw = canonical_json(event), canonical_json(fact)
                database.execute(
                    "INSERT INTO parents(event_type,business_id,identifier,occurred,event,fact,event_sha256,fact_sha256) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        *key,
                        event["event_id"],
                        event["occurred_at"],
                        event_raw,
                        fact_raw,
                        hashlib.sha256(event_raw).hexdigest(),
                        hashlib.sha256(fact_raw).hexdigest(),
                    ),
                )
                self.stats["projected_parent_events"] += 1
                if self.stats["projected_parent_events"] % limits.batch_rows == 0:
                    database.commit()
                    self._budget()
        database.commit()
        if self.stats["projected_parent_events"] != self.plan.parent_events:
            raise SnapshotError("campaign_anomaly_parent_projection_extent")
        self._budget()
        self.native_events_sha256, self.native_facts_trace_sha256 = self._index_hashes()
        replay.check_parents()
        self.parent = _NativeDiskParent(self)
        self._loaded = True

    def _index_hashes(self) -> tuple[str, str]:
        events = hashlib.sha256(b"[")
        count = 0
        for index, (kind, business, identifier, occurred, raw, checksum) in enumerate(
            self._db().execute(
                "SELECT event_type,business_id,identifier,occurred,event,event_sha256 FROM parents ORDER BY occurred,event_type,business_id"
            )
        ):
            event = self._checked(raw, checksum)
            if (
                business_key(event) != (kind, business)
                or event["event_id"] != identifier
                or event["occurred_at"] != occurred
            ):
                raise CampaignAnomalyParentStateError("campaign_anomaly_parent_private_key_changed")
            events.update((b"," if index else b"") + raw)
            count += 1
        if count != self.plan.parent_events:
            raise CampaignAnomalyParentStateError("campaign_anomaly_parent_private_extent_changed")
        events.update(b"]")
        facts = hashlib.sha256()
        for kind, business, raw, checksum in self._db().execute(
            "SELECT event_type,business_id,fact,fact_sha256 FROM parents ORDER BY sequence"
        ):
            fact = self._checked(raw, checksum)
            if (fact["event_type"], fact["business_id"]) != (kind, business):
                raise CampaignAnomalyParentStateError("campaign_anomaly_parent_private_key_changed")
            facts.update(canonical_json([(kind, business), fact]) + b"\n")
        return events.hexdigest(), facts.hexdigest()

    def _source_row(self, name: str, key: str, *, secondary: bool = False) -> Row:
        queries = {
            False: "SELECT payload FROM source_rows WHERE name=? AND natural_key=? ORDER BY position DESC LIMIT 1",
            True: "SELECT payload FROM source_rows WHERE name=? AND secondary_key=? ORDER BY position DESC LIMIT 1",
        }
        row = self._db().execute(queries[secondary], (name, key)).fetchone()
        if row is None:
            raise SnapshotError("campaign_anomaly_parent_missing_native_dependency")
        return self._native_row(row[0], name)

    def _native_row(self, raw: bytes, name: str) -> Row:
        row = decoded(raw, self._columns[name])
        # Curated canonical storage normalizes trailing decimal zeroes. The
        # native operational wire preserves the declared Arrow decimal scale,
        # which also contributes to its UUID and business-version identity.
        for column in self._columns[name]:
            key, kind = column["name"], column["type"]
            if row[key] is not None and kind.startswith("decimal"):
                datatype = arrow_type(kind)
                with localcontext() as context:
                    context.prec = max(context.prec, datatype.precision)
                    context.traps[Inexact] = True
                    row[key] = row[key].quantize(Decimal(1).scaleb(-datatype.scale))
        return row
