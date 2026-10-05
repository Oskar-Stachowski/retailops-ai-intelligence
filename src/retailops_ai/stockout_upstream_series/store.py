"""Index sealed facts on disk; validate foreign ambiguities before selecting a series."""

import hashlib
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from retailops_ai.curated.contract import columns_for, decoded
from retailops_ai.data_contracts.common import utc_time
from retailops_ai.forecasting.features_contract import TABLES
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.upstream import UPSTREAM_TABLES
from retailops_ai.stockout_upstream_storage.store import (
    DEFAULT_STORAGE_POLICY as DEFAULT_STORAGE_POLICY,
)
from retailops_ai.stockout_upstream_storage.store import (
    UpstreamFacts,
    keys,
)
from retailops_ai.stockout_upstream_storage.store import (
    UpstreamStoragePolicy as UpstreamStoragePolicy,
)

LOGICAL_KEYS = {
    "product_catalog": ("id",),
    "channel_assignments": ("assignment_key", "effective_from", "effective_to"),
    "assortment": ("assortment_key", "effective_from", "effective_to"),
    "price_plans": ("plan_key", "effective_from", "effective_to"),
    "promotion_plans": ("promotion_key", "effective_from", "effective_to"),
    "daily_demand_versions": (
        "business_date",
        "product_id",
        "selling_location_id",
        "channel",
    ),
    "business_calendar": ("business_date", "selling_location_id", "channel"),
    "category_calendar": ("business_date", "category_id"),
    "fulfillment_routes": ("route_key",),
}
MAX_CACHE_ROWS = 1024
MAX_CACHE_BYTES = 1024**2


def indexed(table: str, row: dict[str, Any]) -> tuple[Any, ...]:
    """No availability or business-date truncation when indexing revisions."""
    return (
        canonical_json([str(row[k]) for k in LOGICAL_KEYS[table]]).decode(),
        row.get("version", 1),
        row["id"] if table == "product_catalog" else row.get("product_id"),
        row.get("selling_location_id"),
        row.get("channel"),
        row.get("category_id"),
        row.get("stock_location_id"),
        str(row["business_date"]) if row.get("business_date") is not None else None,
        str(row["effective_from"]) if row.get("effective_from") is not None else None,
        str(row["effective_to"]) if row.get("effective_to") is not None else None,
    )


class SeriesFacts(UpstreamFacts):
    """One single-use private context, no caller-created verification cache.

    The frozen store validates and seals the complete curated parent. This
    index keeps all revisions for global validation, but only selected payloads
    enter Python collections. The database remains private and query-only;
    its file identity/timestamps and query-only flag are checked on every use.
    """

    forecast_tables = TABLES

    def _open(self) -> None:
        super()._open()
        db = self._connection()
        db.execute("PRAGMA query_only=OFF")
        db.execute(
            "CREATE TABLE series_index(table_name TEXT, position INTEGER, ready TEXT, "
            "logical_key TEXT, version INTEGER, product TEXT, selling TEXT, channel TEXT, "
            "category TEXT, stock TEXT, business_date TEXT, effective_from TEXT, "
            "effective_to TEXT, PRIMARY KEY(table_name,position))"
        )
        digest = hashlib.sha256()
        self._columns = {table: columns_for(table, "1.1.0") for table in UPSTREAM_TABLES}
        for table in UPSTREAM_TABLES:
            columns = self._columns[table]
            for position, ready, raw, checksum in db.execute(
                "SELECT position,ready,payload,checksum FROM facts WHERE table_name=? ORDER BY position",
                (table,),
            ):
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise SnapshotError("stockout_upstream_series_row_checksum_mismatch")
                row = decoded(raw, columns)
                if keys(table, row) != (None, None, ready):
                    raise SnapshotError("stockout_upstream_series_row_index_mismatch")
                index = (table, position, ready, *indexed(table, row))
                db.execute("INSERT INTO series_index VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", index)
                digest.update(canonical_json([*index, checksum]) + b"\n")
        db.execute(
            "CREATE INDEX series_versions ON series_index(table_name,logical_key,ready,version)"
        )
        db.execute("CREATE INDEX series_products ON series_index(table_name,product,ready)")
        db.execute(
            "CREATE INDEX series_selling ON series_index(table_name,selling,channel,ready,business_date)"
        )
        db.execute(
            "CREATE INDEX series_routes ON series_index(table_name,stock,ready,effective_from,effective_to)"
        )
        db.commit()
        db.execute("PRAGMA query_only=ON")
        self.seal = {**self.seal, "series_index_sha256": digest.hexdigest()}
        self.stats["database_bytes"] = self.path.stat().st_size
        self.stats["globally_validated_origins"] = 0
        self.stats["series_reads"] = 0
        self.stats["decoded_cache_hits"] = 0
        self.stats["maximum_decoded_cache_rows"] = 0
        self.stats["maximum_decoded_cache_bytes"] = 0
        self._decoded_cache: OrderedDict[tuple[str, int], tuple[dict[str, Any], int]] = (
            OrderedDict()
        )
        self._decoded_cache_bytes = 0
        self._database_identity = self._identity()
        self._validated_origin: datetime | None = None

    def __exit__(self, *args: Any) -> None:
        super().__exit__(*args)
        if hasattr(self, "_decoded_cache"):
            self._decoded_cache.clear()
            self._decoded_cache_bytes = 0

    def _identity(self) -> tuple[int, int, int, int, int]:
        info = self.path.stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    def _sealed_connection(self) -> sqlite3.Connection:
        db = self._connection()
        if (
            self._identity() != self._database_identity
            or db.execute("PRAGMA query_only").fetchone()[0] != 1
        ):
            raise SnapshotError("stockout_upstream_series_database_changed")
        return db

    def known(self, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        # Never silently fall back to the frozen global Python panel.
        raise SnapshotError("stockout_upstream_series_physical_selection_required")

    def _decode_selected(
        self, table: str, position: int, raw: bytes, columns: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Cache decoding only, after availability/checksum checks, never selection.

        Curated cells are scalar typed values; a fresh dict prevents a caller
        modifying rows returned earlier from changing this private cache.
        Canonical bytes and row counts have separate fixed bounds; these do not
        purport to measure decoded Python RSS.
        """
        key = table, position
        if key in self._decoded_cache:
            row, _ = self._decoded_cache[key]
            self._decoded_cache.move_to_end(key)
            self.stats["decoded_cache_hits"] += 1
            return dict(row)
        row = decoded(raw, columns)
        if MAX_CACHE_ROWS and len(raw) <= MAX_CACHE_BYTES:
            while self._decoded_cache and (
                len(self._decoded_cache) >= MAX_CACHE_ROWS
                or self._decoded_cache_bytes + len(raw) > MAX_CACHE_BYTES
            ):
                _, (_, size) = self._decoded_cache.popitem(last=False)
                self._decoded_cache_bytes -= size
            self._decoded_cache[key] = dict(row), len(raw)
            self._decoded_cache_bytes += len(raw)
            self.stats["maximum_decoded_cache_rows"] = max(
                self.stats["maximum_decoded_cache_rows"], len(self._decoded_cache)
            )
            self.stats["maximum_decoded_cache_bytes"] = max(
                self.stats["maximum_decoded_cache_bytes"], self._decoded_cache_bytes
            )
        return row

    def validate_origin(self, as_of: datetime) -> None:
        """Check all known logical keys, including foreign products and old dates.

        Forecast latest rejects ties at the highest known version. Route latest
        rejects a tie at the running maximum in original input order, even if
        a later higher version exists. SQL preserves both frozen semantics.
        Effective route ambiguity is checked before the physical stock filter.
        """
        db = self._sealed_connection()
        as_of = utc_time(as_of)
        if self._validated_origin == as_of:
            return
        origin = as_of.isoformat(timespec="microseconds")
        day = as_of.date().isoformat()
        duplicate = db.execute(
            "WITH known AS (SELECT table_name,logical_key,version, "
            "MAX(version) OVER (PARTITION BY table_name,logical_key) AS highest "
            "FROM series_index WHERE table_name!='fulfillment_routes' AND ready<=?) "
            "SELECT 1 FROM known WHERE version=highest GROUP BY table_name,logical_key "
            "HAVING COUNT(*)>1 LIMIT 1",
            (origin,),
        ).fetchone()
        if duplicate:
            raise SnapshotError("ambiguous_forecast_known_version")
        route_tie = db.execute(
            "WITH routes AS (SELECT version, MAX(version) OVER (PARTITION BY logical_key "
            "ORDER BY position ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous "
            "FROM series_index WHERE table_name='fulfillment_routes' AND ready<=? "
            "AND effective_from<=? AND effective_to>?) "
            "SELECT 1 FROM routes WHERE version=previous LIMIT 1",
            (origin, day, day),
        ).fetchone()
        if route_tie:
            raise ValueError("ambiguous_stockout_as_of_version")
        route_overlap = db.execute(
            "WITH routes AS (SELECT selling,channel,version, "
            "MAX(version) OVER (PARTITION BY logical_key) AS highest "
            "FROM series_index WHERE table_name='fulfillment_routes' AND ready<=? "
            "AND effective_from<=? AND effective_to>?) "
            "SELECT 1 FROM routes WHERE version=highest GROUP BY selling,channel "
            "HAVING COUNT(*)>1 LIMIT 1",
            (origin, day, day),
        ).fetchone()
        if route_overlap:
            raise SnapshotError("stockout_upstream_ambiguous_physical_routing")
        self._validated_origin = as_of
        self.stats["globally_validated_origins"] += 1

    def known_series(
        self, product: str, stock: str, as_of: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        """Preserve all known revisions of the selected series and all target inputs.

        The 28-day history and all 14 target days are retained: the frozen
        projection constructs 14 targets before its seven-day forecast filter.
        Plans/assortment are never clipped by availability of their business date.
        """
        self.validate_origin(as_of)
        db = self._sealed_connection()
        as_of = utc_time(as_of)
        cutoff = as_of.isoformat(timespec="microseconds")
        day = as_of.date().isoformat()
        first = (as_of.date() - timedelta(days=27)).isoformat()
        last = (as_of.date() + timedelta(days=14)).isoformat()
        result: dict[str, list[dict[str, Any]]] = {table: [] for table in UPSTREAM_TABLES}
        count = size = 0

        def select(table: str, predicate: str, parameters: tuple[Any, ...]) -> None:
            nonlocal count, size
            columns = self._columns[table]
            # Predicate comes only from fixed literals in this method. Every
            # external value, including product/location/channel, is a parameter.
            sql = (
                "SELECT i.position,i.ready,i.logical_key,i.version,i.product,i.selling,"  # noqa: S608 - fixed internal predicates; parameterized values
                "i.channel,i.category,i.stock,i.business_date,i.effective_from,i.effective_to,"
                "f.payload,f.checksum FROM series_index i JOIN facts f "
                "ON f.table_name=i.table_name AND f.position=i.position "
                "WHERE i.table_name=? AND i.ready<=? AND "
                + predicate
                + " ORDER BY i.position LIMIT ?"
            )
            for record in db.execute(
                sql, (table, cutoff, *parameters, self.policy.max_selected_rows - count + 1)
            ):
                position, ready, *remaining = record
                raw, checksum = remaining[-2:]
                count += 1
                size += len(raw)
                if count > self.policy.max_selected_rows or size > self.policy.max_selected_bytes:
                    raise SnapshotError("stockout_upstream_series_selected_resource_limit")
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise SnapshotError("stockout_upstream_series_row_checksum_mismatch")
                row = self._decode_selected(table, position, raw, columns)
                if tuple(remaining[:-2]) != indexed(table, row) or keys(table, row) != (
                    None,
                    None,
                    ready,
                ):
                    raise SnapshotError("stockout_upstream_series_row_index_mismatch")
                selected[table].append((position, row))

        selected: dict[str, list[tuple[int, dict[str, Any]]]] = {
            table: [] for table in UPSTREAM_TABLES
        }
        select(
            "fulfillment_routes",
            "i.stock=? AND i.effective_from<=? AND i.effective_to>? AND NOT EXISTS "
            "(SELECT 1 FROM series_index r WHERE r.table_name=i.table_name "
            "AND r.logical_key=i.logical_key AND r.ready<=? AND r.effective_from<=? "
            "AND r.effective_to>? AND r.version>i.version)",
            (stock, day, day, cutoff, day, day),
        )
        select("product_catalog", "i.product=?", (product,))
        for table in ("price_plans", "promotion_plans"):
            select(table, "i.product=?", (product,))
        pairs = sorted(
            {(r["selling_location_id"], r["channel"]) for _, r in selected["fulfillment_routes"]}
        )
        for selling, channel in pairs:
            select("channel_assignments", "i.selling=? AND i.channel=?", (selling, channel))
            select(
                "assortment",
                "i.product=? AND i.selling=? AND i.channel=?",
                (product, selling, channel),
            )
            select(
                "daily_demand_versions",
                "i.product=? AND i.selling=? AND i.channel=? AND i.business_date BETWEEN ? AND ?",
                (product, selling, channel, first, day),
            )
            select(
                "business_calendar",
                "i.selling=? AND i.channel=? AND i.business_date BETWEEN ? AND ?",
                (selling, channel, first, last),
            )
        catalog = selected["product_catalog"]
        if catalog:
            current = max((r for _, r in catalog), key=lambda r: r.get("version", 1))
            select(
                "category_calendar",
                "i.category=? AND i.business_date BETWEEN ? AND ?",
                (current["category_id"], first, last),
            )
        for table, rows in selected.items():
            result[table] = [r for _, r in sorted(rows, key=lambda item: item[0])]
        self.stats["series_reads"] += 1
        self.stats["maximum_selected_rows"] = max(self.stats["maximum_selected_rows"], count)
        self.stats["maximum_selected_bytes"] = max(self.stats["maximum_selected_bytes"], size)
        self._sealed_connection()
        return result
