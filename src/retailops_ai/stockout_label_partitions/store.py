"""Verify one private parent, seal streamed facts, and select one physical ledger."""

import hashlib
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt

from retailops_ai.data_contracts.common import Contract, utc_time
from retailops_ai.source_snapshot.canonical import RowDigest, canonical_cell
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    json_sha256,
    read_bytes,
)
from retailops_ai.source_snapshot.importer import verify_snapshot
from retailops_ai.source_snapshot.inventory_projection import native_row, rows
from retailops_ai.source_snapshot.inventory_protocol import read_windows
from retailops_ai.source_snapshot.protocol import Limits, Snapshot
from retailops_ai.stockout.contract import LabelPolicy, LedgerMovement
from retailops_ai.stockout.dataset import INPUT_LIMITS, QUALIFIED_WINDOWS

TABLES = ("inventory_ledger", "inventory_daily_snapshots", "inventory_history_coverage")
MAX_DB_BYTES = 128 * 1024**2
MAX_SELECTED_BYTES = 16 * 1024**2
MAX_SELECTED_ROWS = 20000


class LabelStoragePolicy(Contract):
    version: Literal["stockout-label-disk-1.0.0"] = "stockout-label-disk-1.0.0"
    batch_rows: Annotated[StrictInt, Field(ge=1, le=512)] = 256
    cache_kib: Literal[8192] = 8192
    max_db_bytes: Annotated[StrictInt, Field(ge=4096, le=MAX_DB_BYTES)] = MAX_DB_BYTES
    max_selected_rows: Annotated[StrictInt, Field(ge=1, le=MAX_SELECTED_ROWS)] = MAX_SELECTED_ROWS
    max_selected_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_SELECTED_BYTES)] = (
        MAX_SELECTED_BYTES
    )


DEFAULT_STORAGE_POLICY = LabelStoragePolicy()


def window_keys(row: dict[str, Any]) -> tuple[str, str, str]:
    origin = utc_time(datetime.fromisoformat(row["origin"]))
    return row["product_id"], row["stock_location_id"], origin.isoformat(timespec="microseconds")


def index_keys(name: str, row: dict[str, Any]) -> tuple[str, str, str | None]:
    stamp = row.get("occurred_at" if name == "inventory_ledger" else "snapshot_at")
    return (
        row["product_id"],
        row["stock_location_id"],
        stamp.isoformat(timespec="microseconds") if stamp is not None else None,
    )


class LabelFacts:
    """Ephemeral, private and single-use; no persisted index is trusted on retry."""

    def __init__(
        self,
        source: Path,
        label_policy: LabelPolicy,
        *,
        allow_evaluation_truth: bool = False,
        storage_policy: LabelStoragePolicy = DEFAULT_STORAGE_POLICY,
        scratch: Path | None = None,
    ) -> None:
        self.source, self.label_policy = source, label_policy
        self.allow_truth, self.policy, self.scratch = (
            allow_evaluation_truth,
            storage_policy,
            scratch,
        )
        self._stack = ExitStack()
        self._db: sqlite3.Connection | None = None
        self._used = False
        self.snapshot: Snapshot
        self.tables: dict[str, Any] = {}
        self.seal: dict[str, Any] = {}
        self.stats = dict(
            stored_rows=0, maximum_selected_rows=0, maximum_selected_bytes=0, database_bytes=0
        )

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("stockout_label_facts_single_use")
        self._used = True
        try:
            self._open()
        except sqlite3.DatabaseError:
            self._stack.close()
            self._db = None
            raise SnapshotError("stockout_label_database_resource_or_write_failure") from None
        except BaseException:
            self._stack.close()
            self._db = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        self._stack.close()
        self._db = None

    def _connection(self) -> sqlite3.Connection:
        if self._db is None:
            raise SnapshotError("stockout_label_facts_closed")
        return self._db

    def _open(self) -> None:
        if not self.allow_truth:
            raise SnapshotError("stockout_labels_require_evaluation_truth_opt_in")
        root = checked_directory(self.source)
        parent = checked_directory(self.scratch or Path(tempfile.gettempdir()).resolve())
        if root == parent or root in parent.parents:
            raise SnapshotError("stockout_label_scratch_inside_input")
        initial = read_bytes(root, "snapshot_manifest.json")
        limits = Limits(
            max_rows=INPUT_LIMITS.max_rows,
            max_bytes=INPUT_LIMITS.max_bytes,
            batch_rows=self.policy.batch_rows,
        )
        snapshot = verify_snapshot(
            root,
            allow_evaluation_truth=True,
            required_use_cases=("inventory_source",),
            limits=limits,
            scratch=parent,
        )
        manifest = snapshot.manifest
        if decode_json(initial) != manifest:
            raise SnapshotError("stockout_label_parent_changed_during_verification")
        if (
            manifest["schema_version"] != "1.1.0"
            or not manifest["descriptor"]["include_evaluation_truth"]
        ):
            raise SnapshotError("stockout_labels_require_inventory_private_snapshot_11")
        projection = manifest["source"]["descriptor"]["context"]["projection"]
        if (
            any(
                projection[k] != getattr(self.label_policy, k)
                for k in ("stock_measure", "reservation_policy", "episode_policy")
            )
            or projection["diagnostic_horizon_days"] != self.label_policy.horizon_days
        ):
            raise SnapshotError("stockout_label_policy_source_mismatch")
        self.tables = {t["table"]: t for t in manifest["tables"]}
        if self.tables["inventory_ledger"]["row_count"] > self.label_policy.max_ledger_rows:
            raise SnapshotError("stockout_ledger_row_limit")
        qualified = read_windows(root, QUALIFIED_WINDOWS)
        if len(qualified) > self.label_policy.max_windows:
            raise SnapshotError("stockout_label_window_limit")
        if json_sha256(qualified) != manifest["descriptor"]["qualification"]["windows_sha256"]:
            raise SnapshotError("stockout_label_qualification_changed_during_read")
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".stockout-label-facts-", dir=parent)
            )
        )
        self.path = directory / "facts.sqlite"
        self.path.touch(mode=0o600, exist_ok=False)
        db = sqlite3.connect(self.path)
        self._db = db
        self._stack.callback(db.close)
        db.execute("PRAGMA page_size=4096")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA mmap_size=0")
        db.execute(f"PRAGMA max_page_count={self.policy.max_db_bytes // 4096}")  # noqa: S608 - validated integer
        db.execute(
            "CREATE TABLE facts(name TEXT, grain BLOB, position INTEGER, product TEXT, stock TEXT, stamp TEXT, payload BLOB, checksum TEXT, PRIMARY KEY(name,grain))"
        )
        db.execute("CREATE INDEX physical ON facts(name,product,stock,position)")
        db.execute("CREATE INDEX snapshot_lookup ON facts(name,product,stock,stamp)")
        db.execute(
            "CREATE TABLE windows(product TEXT, stock TEXT, origin TEXT, payload BLOB, checksum TEXT, PRIMARY KEY(product,stock,origin))"
        )
        for window in qualified:
            raw = canonical_json(window)
            db.execute(
                "INSERT INTO windows VALUES (?,?,?,?,?)",
                (
                    *window_keys(window),
                    raw,
                    hashlib.sha256(raw).hexdigest(),
                ),
            )
        del qualified
        summaries = []
        for name in TABLES:
            spec = self.tables[name]
            digest = RowDigest(
                directory / "seal.sqlite", [c["name"] for c in spec["schema"]], spec["grain"]
            )
            try:
                for position, row in enumerate(rows(root, spec, limits)):
                    digest.add(row)
                    raw = canonical_json(native_row(row))
                    if len(raw) > 65536:
                        raise SnapshotError("stockout_label_record_size_limit")
                    grain = canonical_json([canonical_cell(row[k]) for k in spec["grain"]])
                    db.execute(
                        "INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)",
                        (
                            name,
                            grain,
                            position,
                            *index_keys(name, row),
                            raw,
                            hashlib.sha256(raw).hexdigest(),
                        ),
                    )
                    self.stats["stored_rows"] += 1
                checksum = digest.digest()
                if digest.rows != spec["row_count"] or checksum != spec["content_sha256"]:
                    raise SnapshotError("stockout_label_facts_changed_during_read")
                summaries.append(dict(table=name, rows=digest.rows, content_sha256=checksum))
            finally:
                digest.close()
                (directory / "seal.sqlite").unlink()
        db.commit()
        inventory(root, snapshot.names)
        if (
            read_bytes(root, "snapshot_manifest.json") != initial
            or read_bytes(root, "manifest.sha256", 128)
            != (hashlib.sha256(initial).hexdigest() + "\n").encode()
        ):
            raise SnapshotError("stockout_label_parent_changed_during_read")
        for ref in snapshot.references:
            if file_hash(root, ref["path"]) != (ref["bytes"], ref["sha256"]):
                raise SnapshotError("stockout_label_parent_changed_during_read")
        self.snapshot = snapshot
        self.seal = dict(
            snapshot_manifest_sha256=hashlib.sha256(initial).hexdigest(),
            tables=summaries,
            windows_sha256=manifest["descriptor"]["qualification"]["windows_sha256"],
        )
        self.stats["database_bytes"] = self.path.stat().st_size
        db.execute("PRAGMA query_only=ON")

    def _decode(self, name: str, entry: tuple[Any, ...]) -> dict[str, Any]:
        grain, product, stock, stamp, raw, checksum = entry
        if len(raw) > self.policy.max_selected_bytes or hashlib.sha256(raw).hexdigest() != checksum:
            raise SnapshotError("stockout_label_row_resource_or_checksum_mismatch")
        row = decode_json(raw)
        for column in self.tables[name]["schema"]:
            value = row[column["name"]]
            if value is not None:
                if column["type"].startswith("timestamp"):
                    row[column["name"]] = datetime.fromisoformat(value)
                elif column["type"].startswith("date32"):
                    row[column["name"]] = date.fromisoformat(value)
        if (
            index_keys(name, row) != (product, stock, stamp)
            or canonical_json([canonical_cell(row[k]) for k in self.tables[name]["grain"]]) != grain
        ):
            raise SnapshotError("stockout_label_row_index_mismatch")
        return row

    def get(self, name: str, *key: Any) -> dict[str, Any] | None:
        grain = canonical_json([canonical_cell(k) for k in key])
        entry = (
            self._connection()
            .execute(
                "SELECT grain,product,stock,stamp,payload,checksum FROM facts WHERE name=? AND grain=?",
                (name, grain),
            )
            .fetchone()
        )
        return self._decode(name, entry) if entry is not None else None

    def ledger(self, product: str, stock: str) -> list[LedgerMovement]:
        result: list[LedgerMovement] = []
        size = 0
        for entry in self._connection().execute(
            "SELECT grain,product,stock,stamp,payload,checksum FROM facts WHERE name=? AND product=? AND stock=? ORDER BY position LIMIT ?",
            ("inventory_ledger", product, stock, self.policy.max_selected_rows + 1),
        ):
            size += len(entry[-2])
            if (
                len(result) >= self.policy.max_selected_rows
                or size > self.policy.max_selected_bytes
            ):
                raise SnapshotError("stockout_label_selected_resource_limit")
            row = self._decode("inventory_ledger", entry)
            result.append(
                LedgerMovement.model_validate(
                    {
                        **{
                            k: row[k]
                            for k in (
                                "product_id",
                                "stock_location_id",
                                "occurred_at",
                                "available_at",
                                "sequence",
                                "quantity_delta",
                                "movement_type",
                            )
                        },
                        "event_id": row["inventory_event_id"],
                    }
                )
            )
        self.stats["maximum_selected_rows"] = max(self.stats["maximum_selected_rows"], len(result))
        self.stats["maximum_selected_bytes"] = max(self.stats["maximum_selected_bytes"], size)
        return result

    def snapshot_at(self, product: str, stock: str, origin: datetime) -> dict[str, Any] | None:
        entries = (
            self._connection()
            .execute(
                "SELECT grain,product,stock,stamp,payload,checksum FROM facts WHERE name=? AND product=? AND stock=? AND stamp=? LIMIT 2",
                (
                    "inventory_daily_snapshots",
                    product,
                    stock,
                    origin.isoformat(timespec="microseconds"),
                ),
            )
            .fetchall()
        )
        if len(entries) > 1:
            raise SnapshotError("stockout_label_ambiguous_origin_snapshot")
        return self._decode("inventory_daily_snapshots", entries[0]) if entries else None

    def windows(self) -> Iterator[dict[str, Any]]:
        for product, stock, origin, raw, checksum in self._connection().execute(
            "SELECT product,stock,origin,payload,checksum FROM windows ORDER BY product,stock,origin"
        ):
            if len(raw) > self.policy.max_selected_bytes:
                raise SnapshotError("stockout_label_window_resource_limit")
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SnapshotError("stockout_label_window_checksum_mismatch")
            row = decode_json(raw)
            if window_keys(row) != (
                product,
                stock,
                origin,
            ):
                raise SnapshotError("stockout_label_window_index_mismatch")
            yield row
