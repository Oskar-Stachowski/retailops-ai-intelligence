"""Curated v1 uses exact decimal strings, NFC and microsecond UTC identity."""

from __future__ import annotations

import hashlib
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from retailops_ai.curated import VERSION
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256
from retailops_ai.source_snapshot.protocol import contract_document
from retailops_ai.source_snapshot.tables import arrow_type

CANONICAL_VERSION = "retailops-curated-exact-1.0.0"
EXTRA_COLUMNS = [
    {"name": name, "type": kind, "nullable": nullable}
    for name, kind, nullable in (
        ("source_record_sha256", "string", False),
        ("source_grain_json", "string", False),
        ("curated_available_at", "timestamp[us, tz=UTC]", True),
        ("availability_status", "string", False),
        ("curated_business_date", "date32[day]", True),
        ("mapped_product_id", "string", True),
        ("mapped_selling_location_id", "string", True),
        ("mapped_stock_location_id", "string", True),
        ("mapped_channel", "string", True),
        ("mapping_reference_ids", "string", False),
        ("quantity_unit", "string", True),
        ("scoring_eligible", "bool", False),
    )
]
QUARANTINE_COLUMNS = [
    {"name": name, "type": "string", "nullable": False}
    for name in ("source_table", "source_grain_json", "source_record_sha256", "reason", "row_json")
]


@dataclass(frozen=True)
class Config:
    currencies: tuple[str, ...] = ("EUR", "PLN")
    business_timezone: str = "UTC"
    dictionary_version: str = "retailops-curated-dictionaries-1.0.0"

    def __post_init__(self) -> None:
        if (
            self.business_timezone != "UTC"
            or self.dictionary_version != "retailops-curated-dictionaries-1.0.0"
            or not self.currencies
            or tuple(sorted(set(self.currencies))) != self.currencies
            or any(
                len(c) != 3 or not c.isascii() or not c.isalpha() or not c.isupper()
                for c in self.currencies
            )
        ):
            raise SnapshotError("unsupported_curated_config")

    def document(self) -> dict[str, Any]:
        return {
            "currencies": list(self.currencies),
            "business_timezone": self.business_timezone,
            "dictionary_version": self.dictionary_version,
            "quantity_policy": "saleable_pcs_no_pack_or_fx_conversion",
            "mapping_policy": "effective_half_open_available_at_v1",
            "quarantine_policy": "any_rejection_blocks_ready_publication",
        }


def cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise SnapshotError("nonfinite_curated_decimal")
        text = format(value, "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise SnapshotError("curated_requires_utc")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, (bool, int)):
        return value
    raise SnapshotError("unsupported_curated_cell")


def encoded(row: dict[str, Any]) -> bytes:
    return canonical_json({k: cell(v) for k, v in row.items()})


def decoded(raw: str | bytes, columns: list[dict[str, Any]]) -> dict[str, Any]:
    import json

    row = json.loads(raw)
    for c in columns:
        value = row[c["name"]]
        if value is not None:
            kind = c["type"]
            if kind.startswith("decimal"):
                row[c["name"]] = Decimal(value)
            elif kind.startswith("timestamp"):
                row[c["name"]] = datetime.fromisoformat(value)
            elif kind.startswith("date32"):
                row[c["name"]] = date.fromisoformat(value)
    return dict(row)


def columns_for(table: str, version: str = VERSION) -> list[dict[str, Any]]:
    return list(source_contract(version)["fact_tables"][table]["schema"]) + EXTRA_COLUMNS


def schema_for(columns: list[dict[str, Any]], version: str = VERSION) -> Any:
    return pa.schema(
        [pa.field(c["name"], arrow_type(c["type"]), nullable=c["nullable"]) for c in columns],
        metadata={
            b"retailops.curated_schema": version.encode(),
            b"retailops.canonical": CANONICAL_VERSION.encode(),
        },
    )


class Digest:
    def __init__(self, path: Path, columns: list[dict[str, Any]], grain: list[str]) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute("CREATE TABLE rows (record BLOB NOT NULL, grain BLOB UNIQUE)")
        self.columns, self.grain = columns, grain
        self.rows = 0
        self.ranges: dict[str, dict[str, Any]] = {}

    def add(self, row: dict[str, Any]) -> None:
        if set(row) != {c["name"] for c in self.columns} or any(
            row[c["name"]] is None for c in self.columns if not c["nullable"]
        ):
            raise SnapshotError("curated_row_schema_mismatch")
        raw = encoded(row)
        if len(raw) > 65536 or any(row[k] in (None, "") for k in self.grain):
            raise SnapshotError("curated_record_size_or_grain")
        try:
            self.db.execute(
                "INSERT INTO rows VALUES (?,?)",
                (raw, canonical_json([cell(row[k]) for k in self.grain]) if self.grain else None),
            )
        except sqlite3.IntegrityError as exc:
            raise SnapshotError("duplicate_curated_grain") from exc
        for k, v in row.items():
            if isinstance(v, (date, datetime)):
                day = v.isoformat()[:10]
                r = self.ranges.setdefault(
                    k, {"date_start": day, "date_end": day, "value_count": 0}
                )
                r["date_start"], r["date_end"] = min(r["date_start"], day), max(r["date_end"], day)
                r["value_count"] += 1
        self.rows += 1

    def summary(self) -> dict[str, Any]:
        digest = hashlib.sha256(canonical_json([c["name"] for c in self.columns]) + b"\n")
        for (raw,) in self.db.execute("SELECT record FROM rows ORDER BY record"):
            digest.update(raw + b"\n")
        return {
            "row_count": self.rows,
            "content_sha256": digest.hexdigest(),
            "field_ranges": self.ranges,
        }

    def ordered(self) -> Any:
        for (raw,) in self.db.execute("SELECT record FROM rows ORDER BY record"):
            yield decoded(raw, self.columns)

    def close(self) -> None:
        self.db.close()


def record_sha(row: dict[str, Any]) -> str:
    return hashlib.sha256(encoded(row)).hexdigest()


def descriptor_id(descriptor: dict[str, Any]) -> str:
    return "curated-sha256-" + json_sha256(descriptor)


def source_contract(version: str = VERSION) -> dict[str, Any]:
    if version == "1.1.0":
        from retailops_ai.source_snapshot.inventory_protocol import contract_document as inventory

        return inventory()
    if version != VERSION:
        raise SnapshotError("unsupported_curated_version")
    return contract_document()


def handoff_bytes(version: str = VERSION) -> bytes:
    from retailops_ai.source_snapshot.protocol import resource_bytes

    if version == "1.1.0":
        from retailops_ai.source_snapshot.inventory_protocol import resource_bytes as inventory

        return inventory("contract.json")
    return resource_bytes("contract.json")


def watermarks(snapshot: Any) -> dict[str, Any]:
    if snapshot.manifest["schema_version"] == "1.1.0":
        parent = snapshot.manifest["source"]["descriptor"]
        context = parent["context"]
        return {
            "inventory": context["projection"],
            "source_context": context,
            **(parent.get("forecast_watermarks") or {}),
        }
    return dict(snapshot.manifest["source"]["watermarks"])
