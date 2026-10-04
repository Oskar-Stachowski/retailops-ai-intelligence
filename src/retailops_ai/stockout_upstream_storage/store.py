"""Stream sealed upstream facts to a disposable SQLite database; bound each global origin."""

import hashlib
import sqlite3
import tempfile
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, StrictInt

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, decoded, encoded
from retailops_ai.data_contracts.common import Contract, utc_time
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
)
from retailops_ai.source_snapshot.protocol import Limits
from retailops_ai.stockout.dataset import INPUT_LIMITS
from retailops_ai.stockout.upstream import UPSTREAM_TABLES

MAX_DB_BYTES = 128 * 1024**2
MAX_SELECTED_BYTES = 16 * 1024**2
MAX_SELECTED_ROWS = 20000


class UpstreamStoragePolicy(Contract):
    version: Literal["stockout-upstream-disk-1.0.0"] = "stockout-upstream-disk-1.0.0"
    batch_rows: Annotated[StrictInt, Field(ge=1, le=512)] = 256
    cache_kib: Literal[8192] = 8192
    max_db_bytes: Annotated[StrictInt, Field(ge=4096, le=MAX_DB_BYTES)] = MAX_DB_BYTES
    max_selected_rows: Annotated[StrictInt, Field(ge=1, le=MAX_SELECTED_ROWS)] = MAX_SELECTED_ROWS
    max_selected_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_SELECTED_BYTES)] = (
        MAX_SELECTED_BYTES
    )


DEFAULT_STORAGE_POLICY = UpstreamStoragePolicy()


def keys(table: str, row: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    # Keep known future calendars/plans; business dates are not availability gates.
    available = row["curated_available_at"]
    return None, None, utc_time(available).isoformat(timespec="microseconds") if available else None


class UpstreamFacts:
    """No persistent cache is accepted. Every context verifies and seals its own input."""

    def __init__(
        self,
        curated: Path,
        *,
        policy: UpstreamStoragePolicy = DEFAULT_STORAGE_POLICY,
        scratch: Path | None = None,
    ) -> None:
        self.curated, self.policy, self.scratch = curated, policy, scratch
        self._stack = ExitStack()
        self._db: sqlite3.Connection | None = None
        self._used = False
        self.document: dict[str, Any] = {}
        self.seal: dict[str, Any] = {}
        self.stats = {
            "stored_rows": 0,
            "payload_bytes": 0,
            "maximum_selected_rows": 0,
            "maximum_selected_bytes": 0,
            "database_bytes": 0,
        }

    def __enter__(self) -> Self:
        if self._used:
            raise SnapshotError("stockout_upstream_disk_facts_single_use")
        self._used = True
        try:
            self._open()
        except sqlite3.DatabaseError:
            self._stack.close()
            self._db = None
            raise SnapshotError(
                "stockout_upstream_disk_database_resource_or_write_failure"
            ) from None
        except BaseException:
            self._stack.close()
            self._db = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        self._stack.close()
        self._db = None

    def _open(self) -> None:
        root = checked_directory(self.curated)
        parent = checked_directory(
            self.scratch if self.scratch is not None else Path(tempfile.gettempdir()).resolve()
        )
        if root == parent or root in parent.parents:
            raise SnapshotError("stockout_upstream_disk_scratch_inside_input")
        initial = read_bytes(root, "curated_manifest.json")
        limits = Limits(
            max_rows=INPUT_LIMITS.max_rows,
            max_bytes=INPUT_LIMITS.max_bytes,
            batch_rows=self.policy.batch_rows,
        )
        document = verify_curated(root, limits=limits)
        if decode_json(initial) != document:
            raise SnapshotError("stockout_upstream_disk_parent_changed_during_verification")
        if document["schema_version"] != "1.1.0" or not document["readiness"]["inventory_ready"]:
            raise SnapshotError("stockout_features_require_ready_inventory_curated_11")
        directory = Path(
            self._stack.enter_context(
                tempfile.TemporaryDirectory(prefix=".stockout-upstream-facts-", dir=parent)
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
            "CREATE TABLE facts(table_name TEXT, position INTEGER, product TEXT, stock TEXT, ready TEXT, payload BLOB, checksum TEXT, PRIMARY KEY(table_name, position))"
        )
        db.execute(
            "CREATE INDEX facts_lookup ON facts(table_name, product, stock, ready, position)"
        )
        summaries = []
        order_digest = hashlib.sha256()
        specs = {table["table"]: table for table in document["tables"]}
        try:
            for name in UPSTREAM_TABLES:
                spec = specs[name]
                digest = Digest(
                    directory / "table-seal.sqlite", columns_for(name, "1.1.0"), spec["grain"]
                )
                try:
                    for position, row in enumerate(
                        iter_rows(root, spec["files"], self.policy.batch_rows)
                    ):
                        digest.add(row)
                        raw = encoded(row)
                        product, stock, ready = keys(name, row)
                        db.execute(
                            "INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                            (
                                name,
                                position,
                                product,
                                stock,
                                ready,
                                raw,
                                hashlib.sha256(raw).hexdigest(),
                            ),
                        )
                        order_digest.update(
                            canonical_json([name, position, hashlib.sha256(raw).hexdigest()])
                            + b"\n"
                        )
                        self.stats["stored_rows"] += 1
                        self.stats["payload_bytes"] += len(raw)
                    summary = digest.summary()
                    if any(spec[k] != v for k, v in summary.items()):
                        raise SnapshotError("stockout_curated_changed_during_read")
                    summaries.append({"table": name, **summary})
                finally:
                    digest.close()
                    (directory / "table-seal.sqlite").unlink()
            db.commit()
        except sqlite3.DatabaseError:
            raise SnapshotError(
                "stockout_upstream_disk_database_resource_or_write_failure"
            ) from None
        # Bind the complete original parent, including facts outside the projection.
        refs = [
            file
            for table in [*document["tables"], document["quarantine"]]
            for file in table["files"]
        ]
        inventory(root, {"curated_manifest.json", "manifest.sha256"} | {r["path"] for r in refs})
        if (
            read_bytes(root, "curated_manifest.json") != initial
            or read_bytes(root, "manifest.sha256", 128)
            != (hashlib.sha256(initial).hexdigest() + "\n").encode()
        ):
            raise SnapshotError("stockout_upstream_disk_parent_changed_during_read")
        for ref in refs:
            if file_hash(root, ref["path"]) != (ref["bytes"], ref["sha256"]):
                raise SnapshotError("stockout_upstream_disk_parent_changed_during_read")
        self.document = document
        self.seal = {
            "curated_dataset_id": document["curated_dataset_id"],
            "tables": summaries,
            "ordered_rows_sha256": order_digest.hexdigest(),
        }
        self.stats["database_bytes"] = self.path.stat().st_size
        db.execute("PRAGMA query_only=ON")

    def _connection(self) -> sqlite3.Connection:
        if self._db is None or not self.document:
            raise SnapshotError("stockout_upstream_disk_facts_closed_or_unsealed")
        return self._db

    def known(self, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        db = self._connection()
        origin = utc_time(as_of).isoformat(timespec="microseconds")
        result = {}
        count = size = 0
        for table in UPSTREAM_TABLES:
            selected = []
            columns = columns_for(table, "1.1.0")
            for p in (None,):
                for s in (None,):
                    for position, ready, raw, checksum in db.execute(
                        "SELECT position,ready,payload,checksum FROM facts WHERE table_name=? AND product IS ? AND stock IS ? AND ready<=? ORDER BY ready,position LIMIT ?",
                        (table, p, s, origin, self.policy.max_selected_rows - count + 1),
                    ):
                        count += 1
                        size += len(raw)
                        if (
                            count > self.policy.max_selected_rows
                            or size > self.policy.max_selected_bytes
                        ):
                            raise SnapshotError("stockout_upstream_disk_selected_resource_limit")
                        if hashlib.sha256(raw).hexdigest() != checksum:
                            raise SnapshotError("stockout_upstream_disk_row_checksum_mismatch")
                        row = decoded(raw, columns)
                        if keys(table, row) != (p, s, ready):
                            raise SnapshotError("stockout_upstream_disk_row_index_mismatch")
                        selected.append((position, row))
            result[table] = [r for _, r in sorted(selected, key=lambda r: r[0])]
        self.stats["maximum_selected_rows"] = max(self.stats["maximum_selected_rows"], count)
        self.stats["maximum_selected_bytes"] = max(self.stats["maximum_selected_bytes"], size)
        return result
