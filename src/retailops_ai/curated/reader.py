"""Bounded as-of queries use version history, never the final observation quantity."""

from __future__ import annotations

import sqlite3
import tempfile
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import DEFAULT_LIMITS, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, cell, columns_for, decoded, encoded
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.protocol import Limits

PLAN_KEYS = {
    "channel_assignments": "assignment_key",
    "fulfillment_routes": "route_key",
    "assortment": "assortment_key",
    "price_plans": "plan_key",
    "promotion_plans": "promotion_key",
}


def _rows_as_of(
    root: Path,
    document: dict[str, Any],
    origin: datetime,
    *,
    table: str = "daily_demand_versions",
    business_date: date | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[dict[str, Any]]:
    if origin.tzinfo is None or origin.utcoffset() != UTC.utcoffset(origin):
        raise SnapshotError("as_of_origin_requires_utc")
    version = document["schema_version"]
    inventory = version in {"1.1.0", "1.2.0"} and table in {
        "inventory_daily_snapshots",
        "delivery_plan_versions",
        "inventory_ledger",
        "inventory_sales",
        "inventory_returns",
        "replenishment_orders",
        "replenishment_receipts",
        "inventory_history_coverage",
        "inventory_fulfillment_routes",
        "product_suppliers",
        "suppliers",
    }
    if (
        not inventory
        and table != "daily_demand_versions"
        and (table not in PLAN_KEYS or business_date is None)
    ):
        raise SnapshotError("as_of_requires_history_or_plan_and_effective_date")
    spec = next(t for t in document["tables"] if t["table"] == table)
    with tempfile.TemporaryDirectory(prefix="curated-as-of-") as tmp:
        digest = Digest(Path(tmp) / "content.sqlite", columns_for(table, version), spec["grain"])
        db = sqlite3.connect(Path(tmp) / "eligible.sqlite")
        try:
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE eligible (key BLOB, version INTEGER, available TEXT, body BLOB)"
            )
            db.execute("CREATE INDEX eligible_latest ON eligible(key,version DESC,available DESC)")
            for row in iter_rows(root, spec["files"], limits.batch_rows):
                digest.add(row)
                available = row["curated_available_at"]
                if available is None or available > origin:
                    continue
                revision = row.get("version", 1)
                if inventory:
                    if table == "inventory_daily_snapshots":
                        if row["snapshot_at"] > origin or (
                            business_date is not None and row["business_date"] != business_date
                        ):
                            continue
                        key = [row["product_id"], row["stock_location_id"]]
                        revision = int(row["snapshot_at"].timestamp() * 1000000)
                    elif table == "delivery_plan_versions":
                        key = [row["replenishment_order_id"]]
                    elif table in {"inventory_fulfillment_routes", "product_suppliers"}:
                        if business_date is None:
                            raise SnapshotError("inventory_plan_requires_effective_date")
                        if not row["effective_from"] <= business_date < row["effective_to"]:
                            continue
                        key = (
                            [row["selling_location_id"], row["channel"]]
                            if table == "inventory_fulfillment_routes"
                            else [row["product_supplier_id"]]
                        )
                    else:
                        if any(
                            row.get(k) is not None and row[k] > origin
                            for k in (
                                "occurred_at",
                                "sold_at",
                                "returned_at",
                                "received_at",
                                "ordered_at",
                                "covered_through_at",
                            )
                        ):
                            continue
                        key = [row[k] for k in spec["grain"]]
                elif table == "daily_demand_versions":
                    if row["business_date"] > origin.date() or (
                        business_date is not None and row["business_date"] != business_date
                    ):
                        continue
                    key = [
                        row[k]
                        for k in ("business_date", "product_id", "selling_location_id", "channel")
                    ]
                else:
                    if (
                        business_date is None
                        or not row["effective_from"] <= business_date < row["effective_to"]
                    ):
                        continue
                    key = [row[PLAN_KEYS[table]]]
                db.execute(
                    "INSERT INTO eligible VALUES (?,?,?,?)",
                    (
                        canonical_json([cell(v) for v in key]),
                        revision,
                        cell(available),
                        encoded(row),
                    ),
                )
            if any(spec[k] != v for k, v in digest.summary().items()):
                raise SnapshotError("curated_changed_during_as_of_read")
            ambiguous = db.execute(
                "SELECT e.key FROM eligible e JOIN (SELECT key,MAX(version) AS latest FROM eligible GROUP BY key) v ON e.key=v.key AND e.version=v.latest GROUP BY e.key HAVING COUNT(*)>1 LIMIT 1"
            ).fetchone()
            if ambiguous is not None:
                raise SnapshotError("ambiguous_as_of_version")
            query = "SELECT body FROM (SELECT key,body,ROW_NUMBER() OVER (PARTITION BY key ORDER BY version DESC,available DESC) AS rank FROM eligible) WHERE rank=1 ORDER BY key"
            for (body,) in db.execute(query):
                yield decoded(body, columns_for(table, version))
        finally:
            digest.close()
            db.close()


class CuratedReader:
    """Verify the immutable view once; check selected typed content on every query.

    Metadata is retained privately. A replaced table must match the original logical
    digest, grain and count before any selected row is returned. Other files are not
    consulted by queries, so changing them cannot change this verified view.
    """

    def __init__(self, root: Path, *, limits: Limits = DEFAULT_LIMITS) -> None:
        self._root = root
        self._limits = limits
        self._document = verify_curated(root, limits=limits)

    @property
    def manifest(self) -> dict[str, Any]:
        return deepcopy(self._document)

    def rows(
        self,
        origin: datetime,
        *,
        table: str = "daily_demand_versions",
        business_date: date | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from _rows_as_of(
            self._root,
            self._document,
            origin,
            table=table,
            business_date=business_date,
            limits=self._limits,
        )


def rows_as_of(
    root: Path,
    origin: datetime,
    *,
    table: str = "daily_demand_versions",
    business_date: date | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[dict[str, Any]]:
    if origin.tzinfo is None or origin.utcoffset() != UTC.utcoffset(origin):
        raise SnapshotError("as_of_origin_requires_utc")
    yield from CuratedReader(root, limits=limits).rows(
        origin, table=table, business_date=business_date
    )
