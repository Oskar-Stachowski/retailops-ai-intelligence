"""Exact Arrow schemas, typed canonical content, ranges, grain and partitions."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.canonical import RowDigest
from retailops_ai.source_snapshot.files import SnapshotError, read_json, regular_file
from retailops_ai.source_snapshot.protocol import Limits, Snapshot, contract_document

PARTITIONS = {
    "sales": "sold_at",
    "orders": "ordered_at",
    "return_events": "returned_at",
    "order_items": "orders.ordered_at",
}


def arrow_type(name: str) -> Any:
    simple = {
        "string": pa.string(),
        "int64": pa.int64(),
        "bool": pa.bool_(),
        "date32[day]": pa.date32(),
        "timestamp[us, tz=UTC]": pa.timestamp("us", tz="UTC"),
    }
    if name in simple:
        return simple[name]
    decimal = re.fullmatch(r"decimal(128|256)\((\d+), (\d+)\)", name)
    if decimal:
        function = pa.decimal128 if decimal[1] == "128" else pa.decimal256
        return function(int(decimal[2]), int(decimal[3]))
    raise SnapshotError("unsupported_arrow_type")


def arrow_schema(columns: list[dict[str, Any]], metadata: dict[str, str] | None = None) -> Any:
    metadata = contract_document()["arrow_metadata"] if metadata is None else metadata
    return pa.schema(
        [pa.field(c["name"], arrow_type(c["type"]), nullable=c["nullable"]) for c in columns],
        metadata={k.encode(): v.encode() for k, v in metadata.items()},
    )


def partition_field(table: dict[str, Any]) -> str | None:
    field = table["partition_source_field"]
    known = (
        "business_date"
        if "business_date" in [c["name"] for c in table["schema"]]
        else PARTITIONS.get(table["table"])
    )
    if field is not None and field != known:
        raise SnapshotError("invalid_partition_source_field")
    return str(field) if field is not None else None


def part_day(row: dict[str, Any], field: str, dates: sqlite3.Connection) -> str:
    if field == "orders.ordered_at":
        result = dates.execute("SELECT day FROM orders WHERE id=?", (row["order_id"],)).fetchone()
        if result is None:
            raise SnapshotError("missing_order_partition_mapping")
        return str(result[0])
    value = row[field]
    if value is None:
        return "unknown"
    return str(value.isoformat()[:10])


def verify_table(
    root: Path,
    table: dict[str, Any],
    scratch: Path,
    dates: sqlite3.Connection,
    limits: Limits,
    metadata: dict[str, str] | None = None,
) -> None:
    schema = arrow_schema(table["schema"], metadata)
    if read_json(root, "schemas/" + table["table"] + ".arrow.json") != {
        "table": table["table"],
        "schema": table["schema"],
    }:
        raise SnapshotError("arrow_schema_file_mismatch")
    field = partition_field(table)
    namespace = "evaluation_truth" if table["data_class"] == "simulation_truth" else "facts"
    prefix = namespace + "/" + table["table"] + "/"
    digest = RowDigest(
        scratch / (table["table"] + ".sqlite"),
        schema.names,
        table["grain"],
        [f.name for f in schema if pa.types.is_date(f.type) or pa.types.is_timestamp(f.type)],
    )
    try:
        for ref in table["files"]:
            suffix = ref["path"].removeprefix(prefix)
            pattern = (
                r"business_date=(\d{4}-\d{2}-\d{2})/part-\d{6}\.parquet"
                if field
                else r"part-\d{6}\.parquet"
            )
            match = re.fullmatch(pattern, suffix)
            if not ref["path"].startswith(prefix) or not match:
                raise SnapshotError("parquet_namespace_or_partition_path_mismatch")
            count = 0
            with regular_file(root, ref["path"]) as stream:
                parquet = pq.ParquetFile(
                    stream,
                    pre_buffer=False,
                    thrift_string_size_limit=4 * 1024 * 1024,
                    thrift_container_size_limit=1000000,
                    page_checksum_verification=True,
                    arrow_extensions_enabled=False,
                )
                if not parquet.schema_arrow.equals(schema, check_metadata=True):
                    raise SnapshotError("typed_arrow_schema_mismatch")
                if parquet.metadata.num_rows != ref["row_count"]:
                    raise SnapshotError("parquet_file_row_count_mismatch")
                if any(
                    parquet.metadata.row_group(i).total_byte_size > 128 * 1024 * 1024
                    for i in range(parquet.num_row_groups)
                ):
                    raise SnapshotError("parquet_row_group_size_limit")
                for batch in parquet.iter_batches(batch_size=limits.batch_rows, use_threads=False):
                    if batch.nbytes > 64 * 1024 * 1024:
                        raise SnapshotError("parquet_batch_size_limit")
                    for row in batch.to_pylist():
                        if any(
                            row[c["name"]] in (None, "")
                            for c in table["schema"]
                            if not c["nullable"]
                        ):
                            raise SnapshotError("nonnullable_value_missing")
                        if field and part_day(row, field, dates) != match[1]:
                            raise SnapshotError("partition_row_mismatch")
                        if table["table"] == "orders":
                            try:
                                dates.execute(
                                    "INSERT INTO orders VALUES (?,?)",
                                    (row["id"], row["ordered_at"].date().isoformat()),
                                )
                            except sqlite3.IntegrityError as exc:
                                raise SnapshotError("duplicate_order_mapping") from exc
                        digest.add(row)
                        count += 1
                parquet.close()
            if count != ref["row_count"]:
                raise SnapshotError("parquet_decoded_count_mismatch")
        if digest.rows != table["row_count"] or digest.digest() != table["content_sha256"]:
            raise SnapshotError("typed_logical_content_mismatch")
        if digest.ranges != table["field_ranges"] or digest.date_range() != table["date_range"]:
            raise SnapshotError("typed_date_range_mismatch")
    finally:
        digest.close()


def verify_tables(root: Path, snapshot: Snapshot, scratch: Path, limits: Limits) -> None:
    dates = sqlite3.connect(scratch / "order_dates.sqlite")
    try:
        dates.execute("PRAGMA cache_size=-2048")
        dates.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, day TEXT NOT NULL)")
        ordered = sorted(snapshot.manifest["tables"], key=lambda t: t["table"] != "orders")
        metadata = None
        if snapshot.manifest["schema_version"] == "1.1.0":
            from retailops_ai.source_snapshot.inventory_protocol import (
                contract_document as inventory_contract,
            )

            metadata = inventory_contract()["arrow_metadata"]
        for table in ordered:
            verify_table(root, table, scratch, dates, limits, metadata)
    finally:
        dates.close()
