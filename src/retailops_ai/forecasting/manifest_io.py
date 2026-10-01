"""Bounded, typed table I/O and content publication shared by formal manifests."""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
from collections import Counter
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.manifest_contract import (
    CodePin,
    FileReceipt,
    LabelPoint,
    Membership,
    TableReceipt,
)
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, file_hash, regular_file
from retailops_ai.source_snapshot.protocol import resource_bytes

MAX_BYTES = 2 * 1024**3
MAX_ROWS = 10000000
KEYS = (
    "fold",
    "role",
    "forecast_origin",
    "product_id",
    "selling_location_id",
    "channel",
    "target_date",
)
Record = LabelPoint | Membership


def code_pin() -> CodePin:
    hashes = {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in (
            "manifest_contract.py",
            "manifest_io.py",
            "manifests.py",
            "splits.py",
            "preprocessing.py",
        )
    }
    return CodePin(
        version="forecast-manifests-1.0.0",
        code_files=hashes,
        code_sha256=canonical_sha256(hashes),
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
        pyarrow_version=pa.__version__,
    )


def table_schema(name: str) -> Any:
    return pa.schema(
        [
            *[
                pa.field(
                    k,
                    pa.timestamp("us", tz="UTC")
                    if k == "forecast_origin"
                    else pa.date32()
                    if k == "target_date"
                    else pa.string(),
                    False,
                )
                for k in KEYS
            ],
            *(
                [
                    pa.field("observed_sales_units", pa.int64(), True),
                    pa.field("label_available_at", pa.timestamp("us", tz="UTC"), True),
                ]
                if name == "labels"
                else [pa.field("eligible", pa.bool_(), False)]
            ),
            pa.field("body_json", pa.string(), False),
        ],
        metadata={b"retailops.forecast_manifests": b"1.0.0"},
    )


def physical(row: Record) -> dict[str, Any]:
    result = {k: getattr(row, k) for k in KEYS}
    result["body_json"] = canonical_bytes(row.model_dump(mode="json")).decode()
    if isinstance(row, LabelPoint):
        result.update(
            observed_sales_units=row.observed_sales_units, label_available_at=row.label_available_at
        )
    else:
        result["eligible"] = row.eligible
    return result


def key(row: Any) -> bytes:
    value = row.model_dump(mode="json")
    return canonical_bytes([value[k] for k in KEYS])


class TableWriter:
    def __init__(self, root: Path, name: str, db: sqlite3.Connection, budget: Counter[str]) -> None:
        self.root, self.name, self.db, self.budget = root, name, db, budget
        self.buffer: list[dict[str, Any]] = []
        self.buffer_bytes = 0
        self.count = 0
        self.receipts: list[FileReceipt] = []

    def add(self, row: Record) -> None:
        body = canonical_bytes(row.model_dump(mode="json"))
        self.budget["logical"] += len(body)
        self.count += 1
        if len(body) > 1024**2 or self.count > MAX_ROWS or self.budget["logical"] > MAX_BYTES:
            raise SnapshotError("forecast_manifest_table_resource_limit")
        self.db.execute("INSERT INTO rows VALUES (?,?,?)", (self.name, key(row), body))
        self.buffer.append(physical(row))
        self.buffer_bytes += len(body)
        if len(self.buffer) >= 256 or self.buffer_bytes >= 16 * 1024**2:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        path = self.root / self.name / f"part-{len(self.receipts):06d}.parquet"
        path.parent.mkdir(exist_ok=True)
        batch = pa.Table.from_pylist(self.buffer, schema=table_schema(self.name))
        if batch.nbytes > 64 * 1024**2:
            raise SnapshotError("forecast_manifest_arrow_batch_limit")
        pq.write_table(batch, path, compression="zstd", row_group_size=256)
        size, digest = file_hash(self.root, path.relative_to(self.root).as_posix())
        self.budget["physical"] += size
        self.budget["files"] += 1
        if self.budget["physical"] > MAX_BYTES or self.budget["files"] > 10000:
            raise SnapshotError("forecast_manifest_table_resource_limit")
        self.receipts.append(
            FileReceipt(
                path=path.relative_to(self.root).as_posix(),
                size_bytes=size,
                sha256=digest,
                row_count=len(self.buffer),
            )
        )
        self.buffer.clear()
        self.buffer_bytes = 0

    def summary(self) -> TableReceipt:
        self.flush()
        digest = hashlib.sha256()
        for (body,) in self.db.execute(
            "SELECT body FROM rows WHERE kind=? ORDER BY key", (self.name,)
        ):
            digest.update(body + b"\n")
        return TableReceipt(
            content_sha256=digest.hexdigest(), row_count=self.count, files=tuple(self.receipts)
        )


def iter_table(root: Path, name: str, spec: TableReceipt, budget: Counter[str]) -> Iterator[Record]:
    for ref in spec.files:
        if not ref.path.startswith(name + "/"):
            raise SnapshotError("forecast_manifest_table_path_mismatch")
        budget["physical"] += ref.size_bytes
        budget["files"] += 1
        if budget["physical"] > MAX_BYTES or budget["files"] > 10000:
            raise SnapshotError("forecast_manifest_table_resource_limit")
        with regular_file(root, ref.path) as stream:
            stream.seek(0, 2)
            if stream.tell() != ref.size_bytes:
                raise SnapshotError("forecast_manifest_table_size_mismatch")
        if file_hash(root, ref.path) != (ref.size_bytes, ref.sha256):
            raise SnapshotError("forecast_manifest_table_checksum_mismatch")
        count = 0
        with regular_file(root, ref.path) as stream:
            parquet = pq.ParquetFile(
                stream,
                pre_buffer=False,
                arrow_extensions_enabled=False,
                thrift_string_size_limit=4 * 1024**2,
                thrift_container_size_limit=1000000,
            )
            try:
                if not parquet.schema_arrow.equals(table_schema(name), check_metadata=True):
                    raise SnapshotError("forecast_manifest_table_schema_mismatch")
                for batch in parquet.iter_batches(batch_size=256, use_threads=False):
                    if batch.nbytes > 64 * 1024**2:
                        raise SnapshotError("forecast_manifest_arrow_batch_limit")
                    for payload in batch.to_pylist():
                        body = payload["body_json"]
                        budget["logical"] += len(body.encode())
                        budget["rows_" + name] += 1
                        if (
                            len(body.encode()) > 1024**2
                            or budget["logical"] > MAX_BYTES
                            or budget["rows_" + name] > MAX_ROWS
                        ):
                            raise SnapshotError("forecast_manifest_table_resource_limit")
                        decode_json(body.encode())
                        row = (
                            LabelPoint.model_validate_json(body)
                            if name == "labels"
                            else Membership.model_validate_json(body)
                        )
                        if physical(row) != payload:
                            raise SnapshotError("forecast_manifest_typed_body_mismatch")
                        count += 1
                        yield row
            finally:
                parquet.close()
        if count != ref.row_count:
            raise SnapshotError("forecast_manifest_file_count_mismatch")


def dump(path: Path, model: Any) -> None:
    path.write_text(json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
