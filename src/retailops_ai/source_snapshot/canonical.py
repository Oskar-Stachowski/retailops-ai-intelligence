"""Typed NFC/UTC multiset identity with a bounded disk sort, version 1.6.0."""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json


def canonical_cell(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SnapshotError("nonfinite_decimal")
        if not value:
            return "0"
        with localcontext() as context:
            context.prec = 28
            return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise SnapshotError("non_utc_timestamp")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (bool, int)):
        return value
    raise SnapshotError("unsupported_canonical_type")


class RowDigest:
    """Canonical duplicate rows are retained; grain uniqueness is checked separately."""

    def __init__(self, database: Path, columns: list[str], grain: list[str]) -> None:
        self.connection = sqlite3.connect(database)
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute("CREATE TABLE records (value BLOB NOT NULL)")
        self.connection.execute("CREATE TABLE grains (value BLOB PRIMARY KEY)")
        self.columns = columns
        self.grain = grain
        self.rows = 0
        self.ranges: dict[str, dict[str, Any]] = {}

    def add(self, row: dict[str, Any]) -> None:
        record = {key: canonical_cell(row[key]) for key in self.columns}
        if len(canonical_json(record)) > 64 * 1024:
            raise SnapshotError("canonical_record_size_limit")
        self.connection.execute("INSERT INTO records VALUES (?)", (canonical_json(record),))
        if self.grain:
            if any(record[key] is None for key in self.grain):
                raise SnapshotError("missing_grain_value")
            try:
                self.connection.execute(
                    "INSERT INTO grains VALUES (?)",
                    (canonical_json([record[k] for k in self.grain]),),
                )
            except sqlite3.IntegrityError as exc:
                raise SnapshotError("duplicate_grain") from exc
        for key, value in row.items():
            if isinstance(value, (date, datetime)):
                day = value.isoformat()[:10]
                previous = self.ranges.setdefault(
                    key, {"date_start": day, "date_end": day, "value_count": 0}
                )
                previous["date_start"] = min(previous["date_start"], day)
                previous["date_end"] = max(previous["date_end"], day)
                previous["value_count"] += 1
        self.rows += 1

    def digest(self) -> str:
        digest = hashlib.sha256(canonical_json(self.columns) + b"\n")
        for (value,) in self.connection.execute("SELECT value FROM records ORDER BY value"):
            digest.update(value + b"\n")
        return digest.hexdigest()

    def date_range(self) -> dict[str, Any]:
        populated = list(self.ranges.values())
        return {
            "date_start": min((r["date_start"] for r in populated), default=None),
            "date_end": max((r["date_end"] for r in populated), default=None),
            "value_count": sum(r["value_count"] for r in populated),
        }

    def close(self) -> None:
        self.connection.close()


def multiset_digest(rows: Iterable[dict[str, Any]], columns: list[str], database: Path) -> str:
    digest = RowDigest(database, columns, [])
    try:
        for row in rows:
            digest.add(row)
        return digest.digest()
    finally:
        digest.close()
