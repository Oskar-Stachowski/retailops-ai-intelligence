"""Bounded verified source index and typed, immutable draft inputs for AI 04.2."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import tempfile
import zlib
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, cell, columns_for, decoded, encoded
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.contract import CalendarManifest, Origin, Parent, make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import (
    FEATURE_TYPES,
    TABLES,
    HistoryContext,
    InputRow,
    PanelPolicy,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_ORIGIN_INPUT_ROWS = 250_000
MAX_OUTPUT_ROWS = 10_000_000
MAX_OUTPUT_BYTES = 2 * 1024**3
# Repeated provenance expands far beyond the compressed Parquet/index footprint.
# Keep physical output and temporary index bounded at 2 GiB independently.
MAX_LOGICAL_OUTPUT_BYTES = 5 * 1024**3
KEYS = ("forecast_origin", "product_id", "selling_location_id", "channel")


class SourceIndex:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def origin_tables(self, origin: Origin) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        count = 0
        start = origin.origin_date - timedelta(days=27)
        end = origin.origin_date + timedelta(days=14)
        for table in TABLES:
            columns = columns_for(table)
            query = "SELECT body FROM source WHERE kind=? AND available<=?"
            params: list[Any] = [table, cell(origin.availability_cutoff)]
            if table in {"daily_demand_versions", "business_calendar", "category_calendar"}:
                query += " AND business_date>=? AND business_date<=?"
                params.extend(
                    [
                        start.isoformat(),
                        (
                            origin.origin_date if table == "daily_demand_versions" else end
                        ).isoformat(),
                    ]
                )
            rows = []
            for (body,) in self.db.execute(query + " ORDER BY identifier", params):
                count += 1
                if count > MAX_ORIGIN_INPUT_ROWS:
                    raise SnapshotError("forecast_origin_input_row_limit")
                rows.append(decoded(body, columns))
            result[table] = rows
        return result


@contextmanager
def source_index(curated_dir: Path, calendar: CalendarManifest) -> Iterator[SourceIndex]:
    document = verify_curated(curated_dir)
    descriptor = document["descriptor"]
    parent = Parent(
        source_dataset_id=descriptor["parent_source_dataset_id"],
        curated_dataset_id=document["curated_dataset_id"],
        snapshot_id=descriptor["parent_snapshot_id"],
        curated_descriptor_sha256=canonical_sha256(descriptor),
        business_timezone=descriptor["config"]["business_timezone"],
        forecast_source_status=document["readiness"]["forecast_source"],
    )
    if parent != calendar.descriptor.parent:
        raise SnapshotError("forecast_inputs_parent_mismatch")
    with tempfile.TemporaryDirectory(prefix="forecast-source-index-") as temporary:
        workspace = Path(temporary)
        db = sqlite3.connect(workspace / "source.sqlite")
        try:
            db.execute("PRAGMA cache_size=-4096")
            db.execute("PRAGMA temp_store=FILE")
            db.execute(
                "CREATE TABLE source (kind TEXT, identifier TEXT, available TEXT, business_date TEXT, body BLOB)"
            )
            db.execute("CREATE INDEX origin_lookup ON source(kind,available,business_date)")
            db.execute("CREATE INDEX date_lookup ON source(kind,business_date,available)")
            indexed_bytes = 0
            for table in TABLES:
                spec = next(t for t in document["tables"] if t["table"] == table)
                digest = Digest(workspace / (table + ".sqlite"), columns_for(table), spec["grain"])
                try:
                    for row in iter_rows(curated_dir, spec["files"], 8192):
                        digest.add(row)
                        body = encoded(row)
                        indexed_bytes += len(body)
                        if indexed_bytes > MAX_OUTPUT_BYTES:
                            raise SnapshotError("forecast_source_index_byte_limit")
                        db.execute(
                            "INSERT INTO source VALUES (?,?,?,?,?)",
                            (
                                table,
                                row["id"],
                                cell(row["curated_available_at"]),
                                cell(row.get("business_date")),
                                body,
                            ),
                        )
                    if any(spec[k] != v for k, v in digest.summary().items()):
                        raise SnapshotError("forecast_source_changed_during_indexing")
                finally:
                    digest.close()
            db.commit()
            yield SourceIndex(db)
        finally:
            db.close()


def implementation() -> dict[str, Any]:
    hashes = {
        "forecasting/" + name: hashlib.sha256(
            files("retailops_ai.forecasting").joinpath(name).read_bytes()
        ).hexdigest()
        for name in ("features.py", "features_contract.py", "features_store.py")
    }
    return {
        "version": "forecast-inputs-1.2.0",
        "logical_output_byte_limit": MAX_LOGICAL_OUTPUT_BYTES,
        "physical_output_byte_limit": MAX_OUTPUT_BYTES,
        "temporary_index_byte_limit": MAX_OUTPUT_BYTES,
        "temporary_index_encoding": "zlib_per_record_canonical_bytes_builder_and_verifier",
        "code_files": hashes,
        "code_sha256": canonical_sha256(hashes),
        "dependency_lock_sha256": hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        "python_version": platform.python_version(),
        "pyarrow_version": pa.__version__,
    }


def schema(artifact: str) -> Any:
    base = [
        pa.field("forecast_origin", pa.timestamp("us", tz="UTC"), False),
        *[pa.field(name, pa.string(), False) for name in KEYS[1:]],
    ]
    if artifact == "history":
        return pa.schema(
            [
                *base,
                pa.field("history_context_sha256", pa.string(), False),
                pa.field("body_json", pa.string(), False),
            ],
            metadata={b"retailops.forecast_inputs": b"1.0.0"},
        )
    kinds = {"int": pa.int64(), "float": pa.float64(), "bool": pa.bool_(), "str": pa.string()}
    return pa.schema(
        [
            *base,
            pa.field("target_date", pa.date32(), False),
            pa.field("horizon_days", pa.int64(), False),
            *[
                pa.field(
                    name,
                    pa.decimal128(38, 0)
                    if name == "planned_regular_price_minor_units"
                    else kinds[kind],
                    True,
                )
                for name, kind in FEATURE_TYPES.items()
                if name not in KEYS
            ],
            pa.field("body_json", pa.string(), False),
        ],
        metadata={b"retailops.forecast_inputs": b"1.0.0"},
    )


def physical_row(model: HistoryContext | InputRow) -> dict[str, Any]:
    row = {k: getattr(model, k) for k in KEYS}
    row["body_json"] = model.model_dump_json()
    if isinstance(model, HistoryContext):
        row["history_context_sha256"] = model.content_sha256()
    else:
        row.update(target_date=model.target_date, horizon_days=model.horizon_days)
        row.update({v.name: v.value for v in model.values})
        if row["planned_regular_price_minor_units"] is not None:
            row["planned_regular_price_minor_units"] = Decimal(
                row["planned_regular_price_minor_units"]
            )
    return row


class Writer:
    def __init__(
        self, root: Path, artifact: str, db: sqlite3.Connection, budget: Counter[str]
    ) -> None:
        self.root, self.artifact, self.db = root, artifact, db
        self.buffer: list[dict[str, Any]] = []
        self.buffer_bytes = 0
        self.count = 0
        self.refs: list[dict[str, Any]] = []
        self.budget = budget

    def add(self, model: HistoryContext | InputRow) -> None:
        logical = model.model_dump(mode="json")
        key_names = (*KEYS, "target_date") if isinstance(model, InputRow) else KEYS
        raw = canonical_bytes(logical)
        self.budget["logical_bytes"] += len(raw)
        if self.budget["logical_bytes"] > MAX_LOGICAL_OUTPUT_BYTES:
            raise SnapshotError("forecast_output_logical_byte_limit")
        key = canonical_bytes([logical[k] for k in key_names])
        try:
            self.db.execute(
                "INSERT INTO output VALUES (?,?,?)",
                (self.artifact, key, zlib.compress(raw, level=1)),
            )
        except sqlite3.IntegrityError as exc:
            raise SnapshotError("duplicate_forecast_input_key") from exc
        self.count += 1
        if self.count > MAX_OUTPUT_ROWS or len(raw) > 1024**2:
            raise SnapshotError("forecast_output_row_limit")
        index_bytes = self.db.execute(
            "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
        ).fetchone()[0]
        if index_bytes > MAX_OUTPUT_BYTES:
            raise SnapshotError("forecast_output_index_byte_limit")
        self.buffer.append(physical_row(model))
        self.buffer_bytes += len(raw)
        if len(self.buffer) >= 256 or self.buffer_bytes >= 16 * 1024**2:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        path = self.root / self.artifact / f"part-{len(self.refs):06d}.parquet"
        path.parent.mkdir(exist_ok=True)
        batch = pa.Table.from_pylist(self.buffer, schema=schema(self.artifact))
        if batch.nbytes > 64 * 1024**2:
            raise SnapshotError("forecast_output_batch_limit")
        pq.write_table(batch, path, compression="zstd", row_group_size=256)
        size, digest = file_hash(self.root, path.relative_to(self.root).as_posix())
        self.budget["physical_bytes"] += size
        self.budget["files"] += 1
        if self.budget["physical_bytes"] > MAX_OUTPUT_BYTES or self.budget["files"] > 10000:
            raise SnapshotError("forecast_output_file_limit")
        self.refs.append(
            {
                "path": path.relative_to(self.root).as_posix(),
                "size_bytes": size,
                "sha256": digest,
                "row_count": len(self.buffer),
            }
        )
        self.buffer.clear()
        self.buffer_bytes = 0

    def summary(self) -> dict[str, Any]:
        self.flush()
        digest = hashlib.sha256()
        for (body,) in self.db.execute(
            "SELECT body FROM output WHERE artifact=? ORDER BY key", (self.artifact,)
        ):
            digest.update(zlib.decompress(body) + b"\n")
        return {"row_count": self.count, "content_sha256": digest.hexdigest(), "files": self.refs}


def build_inputs(curated_dir: Path, calendar: CalendarManifest, output_root: Path) -> Path:
    calendar = CalendarManifest.model_validate_json(calendar.model_dump_json())
    root = output_root.absolute()
    if root.is_relative_to(curated_dir.absolute()):
        raise SnapshotError("forecast_output_inside_immutable_input")
    root.mkdir(parents=True, exist_ok=True)
    checked_directory(root)
    stats: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix=".forecast-inputs-", dir=root) as temporary:
        staging = Path(temporary)
        with tempfile.TemporaryDirectory(prefix="forecast-output-index-") as index_dir:
            db = sqlite3.connect(Path(index_dir) / "output.sqlite")
            try:
                db.execute("PRAGMA cache_size=-4096")
                db.execute("PRAGMA temp_store=FILE")
                db.execute(
                    "CREATE TABLE output (artifact TEXT, key BLOB, body BLOB, PRIMARY KEY(artifact,key))"
                )
                budget: Counter[str] = Counter()
                writers = {
                    name: Writer(staging, name, db, budget) for name in ("history", "features")
                }
                with source_index(curated_dir, calendar) as source:
                    for origin in calendar.origins:
                        view = OriginFeatures(source.origin_tables(origin), origin)
                        stats["potential_history_points"] += (
                            len(view.catalog) * len(view.assignments) * 28
                        )
                        stats["potential_target_rows"] += (
                            len(view.catalog) * len(view.assignments) * 14
                        )
                        if len(view.assortment) > 10000:
                            raise SnapshotError("forecast_series_limit")
                        for series in sorted(view.assortment):
                            history = view.history(series)
                            targets = view.targets(history)
                            if not history.points and not targets:
                                continue
                            writers["history"].add(history)
                            stats["active_history_points"] += len(history.points)
                            for point in history.points:
                                stats["history_" + point.status] += 1
                            for row in targets:
                                writers["features"].add(row)
                                stats["active_target_rows"] += 1
                                stats["target_calendar_eligible"] += int(
                                    row.target_calendar_eligible
                                )
                                stats["insufficient_history_target_rows"] += int(
                                    row.insufficient_history
                                )
                                stats["target_closed"] += int(
                                    any(
                                        v.name == "target_location_open" and v.value is False
                                        for v in row.values
                                    )
                                )
                                for val in row.values:
                                    if val.status == "missing":
                                        stats["missing_" + val.name] += 1
                tables = {name: writer.summary() for name, writer in writers.items()}
            finally:
                db.close()
        if not tables["features"]["row_count"]:
            raise SnapshotError("forecast_inputs_no_known_active_targets")
        descriptor = {
            "schema_version": "1.0.0",
            "role": "forecast_inputs_draft",
            "calendar_id": calendar.calendar_id,
            "parent": calendar.descriptor.parent.model_dump(mode="json"),
            "policy": PanelPolicy().model_dump(mode="json"),
            "feature_types": FEATURE_TYPES,
            "implementation": implementation(),
            "stats": {**dict(stats), "logical_output_bytes": budget["logical_bytes"]},
            "tables": {
                name: {k: v for k, v in spec.items() if k != "files"}
                for name, spec in tables.items()
            },
        }
        identifier = "forecast-inputs-sha256-" + canonical_sha256(descriptor)
        calendar_path = staging / "calendar_manifest.json"
        calendar_path.write_text(calendar.model_dump_json(indent=2) + "\n")
        size, digest = file_hash(staging, calendar_path.name)
        manifest = {
            "schema_version": "1.0.0",
            "inputs_id": identifier,
            "descriptor": descriptor,
            "tables": tables,
            "calendar_file": {"path": calendar_path.name, "size_bytes": size, "sha256": digest},
            "generated_at": datetime.now(UTC).isoformat(),
            "forecast_model_status": "not_ready",
        }
        (staging / "inputs_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        verify_inputs(staging)
        fsync_tree(staging)
        destination = root / identifier
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            old = verify_inputs(destination)
            if old["descriptor"] != descriptor:
                raise SnapshotError("forecast_inputs_publication_conflict") from None
        return destination


def verify_inputs(root: Path) -> dict[str, Any]:
    checked_directory(root)
    manifest = read_json(root, "inputs_manifest.json")
    if (
        set(manifest)
        != {
            "schema_version",
            "inputs_id",
            "descriptor",
            "tables",
            "calendar_file",
            "generated_at",
            "forecast_model_status",
        }
        or manifest["schema_version"] != "1.0.0"
        or manifest["forecast_model_status"] != "not_ready"
    ):
        raise SnapshotError("unsupported_forecast_inputs_manifest")
    descriptor = manifest["descriptor"]
    if (
        set(descriptor)
        != {
            "schema_version",
            "role",
            "calendar_id",
            "parent",
            "policy",
            "feature_types",
            "implementation",
            "stats",
            "tables",
        }
        or descriptor["schema_version"] != "1.0.0"
        or descriptor["role"] != "forecast_inputs_draft"
        or descriptor["implementation"]["code_sha256"]
        != canonical_sha256(descriptor["implementation"]["code_files"])
    ):
        raise SnapshotError("unsupported_forecast_inputs_descriptor")
    if (
        manifest["inputs_id"] != "forecast-inputs-sha256-" + canonical_sha256(descriptor)
        or descriptor["policy"] != PanelPolicy().model_dump(mode="json")
        or descriptor["feature_types"] != FEATURE_TYPES
    ):
        raise SnapshotError("forecast_inputs_identity_or_policy_mismatch")
    calendar_ref = manifest["calendar_file"]
    if calendar_ref["path"] != "calendar_manifest.json" or file_hash(
        root, calendar_ref["path"]
    ) != (calendar_ref["size_bytes"], calendar_ref["sha256"]):
        raise SnapshotError("forecast_inputs_calendar_checksum_mismatch")
    calendar = load_calendar(root / calendar_ref["path"])
    if (
        calendar.calendar_id != descriptor["calendar_id"]
        or calendar.descriptor.parent.model_dump(mode="json") != descriptor["parent"]
    ):
        raise SnapshotError("forecast_inputs_calendar_parent_mismatch")
    names = {"inputs_manifest.json", calendar_ref["path"]}
    total_bytes = 0
    logical_bytes = 0
    input_code = descriptor["implementation"]
    logical_limit = {
        "forecast-inputs-1.0.0": 2 * 1024**3,
        "forecast-inputs-1.1.0": 4 * 1024**3,
        "forecast-inputs-1.2.0": MAX_LOGICAL_OUTPUT_BYTES,
    }.get(input_code["version"])
    if (
        logical_limit is None
        or input_code.get("logical_output_byte_limit", logical_limit) != logical_limit
    ):
        raise SnapshotError("forecast_inputs_unsupported_logical_budget")
    if set(manifest["tables"]) != {"history", "features"}:
        raise SnapshotError("forecast_inputs_tables_mismatch")
    allowed_origins = {origin.forecast_origin for origin in calendar.origins}
    with tempfile.TemporaryDirectory(prefix="forecast-inputs-verify-") as temporary:
        db = sqlite3.connect(Path(temporary) / "rows.sqlite")
        try:
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-4096")
            db.execute(
                "CREATE TABLE output (artifact TEXT, key BLOB, body BLOB, PRIMARY KEY(artifact,key))"
            )
            for name, spec in manifest["tables"].items():
                count = 0
                for ref in spec["files"]:
                    if type(ref["size_bytes"]) is not int or ref["size_bytes"] < 1:
                        raise SnapshotError("forecast_inputs_file_size_mismatch")
                    total_bytes += ref["size_bytes"]
                    if total_bytes > MAX_OUTPUT_BYTES or len(names) > 10000:
                        raise SnapshotError("forecast_inputs_file_limit")
                    with regular_file(root, ref["path"]) as stream:
                        if os.fstat(stream.fileno()).st_size != ref["size_bytes"]:
                            raise SnapshotError("forecast_inputs_file_size_mismatch")
                    if ref["path"] in names or file_hash(root, ref["path"]) != (
                        ref["size_bytes"],
                        ref["sha256"],
                    ):
                        raise SnapshotError("forecast_inputs_file_checksum_mismatch")
                    names.add(ref["path"])
                    with regular_file(root, ref["path"]) as stream:
                        parquet = pq.ParquetFile(
                            stream,
                            pre_buffer=False,
                            arrow_extensions_enabled=False,
                            thrift_string_size_limit=4 * 1024**2,
                            thrift_container_size_limit=1000000,
                        )
                        try:
                            if not parquet.schema_arrow.equals(schema(name), check_metadata=True):
                                raise SnapshotError("forecast_inputs_arrow_schema_mismatch")
                            file_rows = 0
                            for batch in parquet.iter_batches(batch_size=256, use_threads=False):
                                if batch.nbytes > 64 * 1024**2:
                                    raise SnapshotError("forecast_inputs_arrow_batch_limit")
                                for physical in batch.to_pylist():
                                    model = (
                                        HistoryContext.model_validate_json(physical["body_json"])
                                        if name == "history"
                                        else InputRow.model_validate_json(physical["body_json"])
                                    )
                                    if physical_row(model) != physical:
                                        raise SnapshotError("forecast_inputs_typed_body_mismatch")
                                    if model.forecast_origin not in allowed_origins:
                                        raise SnapshotError(
                                            "forecast_inputs_origin_outside_calendar"
                                        )
                                    raw = canonical_bytes(model.model_dump(mode="json"))
                                    logical_bytes += len(raw)
                                    if logical_bytes > logical_limit or len(raw) > 1024**2:
                                        raise SnapshotError("forecast_inputs_logical_byte_limit")
                                    logical = model.model_dump(mode="json")
                                    key_names = (
                                        (*KEYS, "target_date")
                                        if isinstance(model, InputRow)
                                        else KEYS
                                    )
                                    db.execute(
                                        "INSERT INTO output VALUES (?,?,?)",
                                        (
                                            name,
                                            canonical_bytes([logical[k] for k in key_names]),
                                            zlib.compress(raw, level=1),
                                        ),
                                    )
                                    if (
                                        db.execute(
                                            "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
                                        ).fetchone()[0]
                                        > MAX_OUTPUT_BYTES
                                    ):
                                        raise SnapshotError(
                                            "forecast_inputs_verification_index_byte_limit"
                                        )
                                    count += 1
                                    file_rows += 1
                                    if count > MAX_OUTPUT_ROWS:
                                        raise SnapshotError("forecast_inputs_row_limit")
                            if file_rows != ref["row_count"]:
                                raise SnapshotError("forecast_inputs_file_count_mismatch")
                        finally:
                            parquet.close()
                digest = hashlib.sha256()
                for (body,) in db.execute(
                    "SELECT body FROM output WHERE artifact=? ORDER BY key", (name,)
                ):
                    digest.update(zlib.decompress(body) + b"\n")
                logical_spec = {"row_count": count, "content_sha256": digest.hexdigest()}
                if (
                    descriptor["tables"][name] != logical_spec
                    or {k: v for k, v in spec.items() if k != "files"} != logical_spec
                ):
                    raise SnapshotError("forecast_inputs_logical_content_mismatch")
            db.execute("CREATE TABLE history_contexts (hash TEXT PRIMARY KEY, body BLOB)")
            for (body,) in db.execute("SELECT body FROM output WHERE artifact='history'"):
                context = HistoryContext.model_validate_json(zlib.decompress(body))
                db.execute(
                    "INSERT INTO history_contexts VALUES (?,?)", (context.content_sha256(), body)
                )
                if (
                    db.execute(
                        "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
                    ).fetchone()[0]
                    > MAX_OUTPUT_BYTES
                ):
                    raise SnapshotError("forecast_inputs_verification_index_byte_limit")

            @lru_cache(maxsize=128)
            def linked_history(identifier: str) -> tuple[HistoryContext, dict[str, Any]]:
                record = db.execute(
                    "SELECT body FROM history_contexts WHERE hash=?", (identifier,)
                ).fetchone()
                if record is None:
                    raise SnapshotError("forecast_inputs_missing_history_context")
                context = HistoryContext.model_validate_json(zlib.decompress(record[0]))
                view = OriginFeatures(
                    {t: [] for t in TABLES}, make_origin(context.forecast_origin.date())
                )
                return context, view.historical_values(context)

            for (body,) in db.execute("SELECT body FROM output WHERE artifact='features'"):
                row = InputRow.model_validate_json(zlib.decompress(body))
                context, observed = linked_history(row.history_context_sha256)
                if (
                    any(getattr(context, k) != getattr(row, k) for k in KEYS)
                    or row.history_active_days != len(context.points)
                    or row.history_known_days != sum(p.status != "missing" for p in context.points)
                    or row.history_closed_days != sum(p.status == "closed" for p in context.points)
                    or {v.name: v for v in row.values if v.kind == "observed"} != observed
                ):
                    raise SnapshotError("forecast_inputs_history_or_statistics_mismatch")
            if (
                input_code["version"] == "forecast-inputs-1.2.0"
                and descriptor["stats"].get("logical_output_bytes") != logical_bytes
            ):
                raise SnapshotError("forecast_inputs_logical_size_receipt_mismatch")
        except sqlite3.IntegrityError as exc:
            raise SnapshotError("duplicate_forecast_input_key") from exc
        finally:
            db.close()
    inventory(root, names)
    return manifest
