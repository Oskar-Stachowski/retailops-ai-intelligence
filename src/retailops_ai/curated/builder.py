"""Seal imported facts, normalize/quarantine and atomically publish a curated ID."""

from __future__ import annotations

import hashlib
import sys
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from retailops_ai.curated import VERSION
from retailops_ai.curated.contract import (
    CANONICAL_VERSION,
    QUARANTINE_COLUMNS,
    Config,
    Digest,
    cell,
    columns_for,
    decoded,
    descriptor_id,
    encoded,
    record_sha,
    schema_for,
)
from retailops_ai.curated.transform import Index, Reject, transform
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    file_hash,
    inventory,
    json_sha256,
    read_bytes,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.importer import (
    copy_snapshot,
    verify_import,
    verify_snapshot,
    write_private,
)
from retailops_ai.source_snapshot.protocol import (
    Limits,
    Snapshot,
    contract_document,
    resource_bytes,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

DEFAULT_LIMITS = Limits()
DEFAULT_CONFIG = Config()


@dataclass(frozen=True)
class Result:
    status: str
    directory: Path
    manifest: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "curated_dataset_id": self.manifest["curated_dataset_id"],
            "parent_source_dataset_id": self.manifest["descriptor"]["parent_source_dataset_id"],
            "destination": str(self.directory),
            "tables": len(self.manifest["tables"]),
            "accepted_rows": sum(t["row_count"] for t in self.manifest["tables"]),
            "rejected_rows": self.manifest["quarantine"]["row_count"],
            "readiness": self.manifest["readiness"],
        }


def iter_rows(root: Path, files: list[dict[str, Any]], batch_rows: int) -> Any:
    for ref in files:
        with regular_file(root, ref["path"]) as stream:
            parquet = pq.ParquetFile(
                stream,
                pre_buffer=False,
                arrow_extensions_enabled=False,
                thrift_string_size_limit=4 * 1024**2,
                thrift_container_size_limit=1000000,
            )
            try:
                for batch in parquet.iter_batches(batch_size=batch_rows, use_threads=False):
                    if batch.nbytes > 64 * 1024**2:
                        raise SnapshotError("curated_batch_size_limit")
                    yield from batch.to_pylist()
            finally:
                parquet.close()


def implementation() -> dict[str, Any]:
    package = Path(__file__).parent
    files = {
        "curated/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(package.glob("*.py"))
    }
    for p in sorted((package.parent / "source_snapshot").glob("*.py")):
        files["source_snapshot/" + p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    files["contracts/curated/v1/curated_manifest.schema.json"] = hashlib.sha256(
        manifest_schema_bytes()
    ).hexdigest()
    return {
        "version": VERSION,
        "code_files": files,
        "code_sha256": json_sha256(files),
        "dependency_sha256": hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        "handoff_sha256": hashlib.sha256(resource_bytes("contract.json")).hexdigest(),
        "pyarrow_version": pa.__version__,
        "python_version": sys.version.split()[0],
    }


def manifest_schema_bytes() -> bytes:
    schema = files("retailops_ai.curated").joinpath("curated_manifest.schema.json")
    return (
        schema.read_bytes()
        if schema.is_file()
        else (
            Path(__file__).resolve().parents[3]
            / "contracts/curated/v1/curated_manifest.schema.json"
        ).read_bytes()
    )


def write_parts(
    root: Path, name: str, digest: Digest, limits: Limits, budget: dict[str, int]
) -> list[dict[str, Any]]:
    directory = root / name
    directory.mkdir(parents=True, mode=0o700)
    batch: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    buffered_bytes = 0

    def flush() -> None:
        path = directory / f"part-{len(files):06d}.parquet"
        arrow = pa.Table.from_pylist(batch, schema=schema_for(digest.columns))
        if arrow.nbytes > 64 * 1024**2:
            raise SnapshotError("curated_write_batch_size_limit")
        pq.write_table(
            arrow,
            path,
            compression="zstd",
            row_group_size=limits.batch_rows,
            version="2.6",
        )
        path.chmod(0o600)
        size, sha = file_hash(root, path.relative_to(root).as_posix())
        budget["bytes"] += size
        budget["files"] += 1
        if budget["bytes"] > limits.max_bytes or budget["files"] + 2 > limits.max_files:
            raise SnapshotError("curated_output_resource_limit")
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": sha,
                "row_count": len(batch),
            }
        )
        batch.clear()

    for row in digest.ordered():
        row_bytes = len(encoded(row))
        if batch and buffered_bytes + row_bytes > 32 * 1024**2:
            flush()
            buffered_bytes = 0
        batch.append(row)
        buffered_bytes += row_bytes
        if len(batch) == limits.batch_rows:
            flush()
            buffered_bytes = 0
    if batch or not files:
        flush()
    return files


def derive(
    snapshot_root: Path,
    snapshot: Snapshot,
    payload: Path,
    scratch: Path,
    config: Config,
    limits: Limits,
) -> dict[str, Any]:
    facts = sorted(
        (t for t in snapshot.manifest["tables"] if t["data_class"] != "simulation_truth"),
        key=lambda t: t["table"],
    )
    index = Index(scratch / "index.sqlite")
    quarantine = Digest(scratch / "quarantine.sqlite", QUARANTINE_COLUMNS, [])
    tables: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    budget = {"bytes": 0, "files": 0}
    try:
        for table in facts:
            for row in iter_rows(snapshot_root, table["files"], limits.batch_rows):
                index.add(table["table"], row)
        index.db.commit()
        for source in facts:
            name = source["table"]
            digest = Digest(scratch / (name + ".sqlite"), columns_for(name), source["grain"])
            rejected = 0
            try:
                for row in iter_rows(snapshot_root, source["files"], limits.batch_rows):
                    try:
                        normalized = transform(name, row, source["grain"], index, config)
                    except Reject as exc:
                        reason = str(exc)
                        quarantine.add(
                            {
                                "source_table": name,
                                "source_grain_json": canonical_json(
                                    [cell(row[k]) for k in source["grain"]]
                                ).decode(),
                                "source_record_sha256": record_sha(row),
                                "reason": reason,
                                "row_json": encoded(row).decode(),
                            }
                        )
                        reasons[reason] = reasons.get(reason, 0) + 1
                        rejected += 1
                    else:
                        digest.add(normalized)
                tables.append(
                    {
                        "table": name,
                        "classification": "curated",
                        "source_data_class": source["data_class"],
                        "schema": digest.columns,
                        "grain": source["grain"],
                        "source_rows": source["row_count"],
                        "rejected_rows": rejected,
                        **digest.summary(),
                        "files": write_parts(payload, "curated/" + name, digest, limits, budget),
                    }
                )
            finally:
                digest.close()
        q = {
            "schema": QUARANTINE_COLUMNS,
            "grain": [],
            "reasons": reasons,
            **quarantine.summary(),
            "files": write_parts(payload, "quarantine", quarantine, limits, budget),
        }
    finally:
        quarantine.close()
        index.close()
    logical = [{k: v for k, v in table.items() if k != "files"} for table in tables]
    descriptor = {
        "schema_version": VERSION,
        "role": "curated",
        "canonicalization_version": CANONICAL_VERSION,
        "classification": "curated",
        "parent_source_dataset_id": snapshot.source_id,
        "parent_snapshot_id": snapshot.snapshot_id,
        "source_schema_version": "2.6.0",
        "source_parameters": snapshot.manifest["source"]["descriptor"]["resolved_parameters"],
        "watermarks": snapshot.manifest["source"]["watermarks"],
        "config": config.document(),
        "config_sha256": json_sha256(config.document()),
        "transform": implementation(),
        "tables": logical,
        "quarantine": {k: v for k, v in q.items() if k != "files"},
    }
    readiness = {
        "forecast_source": "passed" if not q["row_count"] else "failed",
        "forecast_model": "not_ready",
        "anomaly": "not_ready",
        "stockout": "not_ready",
        "replay": "not_ready",
        "rag": "not_applicable",
        "inventory_ready": False,
    }
    return {
        "schema_version": VERSION,
        "curated_dataset_id": descriptor_id(descriptor),
        "descriptor": descriptor,
        "tables": tables,
        "quarantine": q,
        "readiness": readiness,
        "watermarks": snapshot.manifest["source"]["watermarks"],
        "time_semantics": {
            "business_timezone": "UTC",
            "effective_interval": "half_open",
            "history_table": "daily_demand_versions",
            "as_of": "curated_available_at<=origin",
        },
        "evaluation_truth": {"included": False, "access": "separate_parent_import_only"},
    }


def verify_curated(
    root: Path, *, require_ready: bool = True, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    root = checked_directory(root)
    document = read_json(root, "curated_manifest.json")
    from retailops_ai.source_snapshot.files import decode_json

    try:
        Draft202012Validator(decode_json(manifest_schema_bytes())).validate(document)
    except ValidationError as exc:
        raise SnapshotError("invalid_curated_manifest_schema") from exc
    _, manifest_sha = file_hash(root, "curated_manifest.json")
    if read_bytes(root, "manifest.sha256", 128) != (manifest_sha + "\n").encode():
        raise SnapshotError("curated_manifest_checksum_mismatch")
    descriptor = document["descriptor"]
    if (
        document["schema_version"] != VERSION
        or descriptor["schema_version"] != VERSION
        or descriptor["role"] != "curated"
        or descriptor["classification"] != "curated"
        or descriptor["canonicalization_version"] != CANONICAL_VERSION
        or document["curated_dataset_id"] != descriptor_id(descriptor)
        or document["watermarks"] != descriptor["watermarks"]
        or descriptor["config_sha256"] != json_sha256(descriptor["config"])
        or descriptor["transform"]["code_sha256"]
        != json_sha256(descriptor["transform"]["code_files"])
        or descriptor["transform"]["handoff_sha256"]
        != hashlib.sha256(resource_bytes("contract.json")).hexdigest()
        or document["evaluation_truth"]
        != {"included": False, "access": "separate_parent_import_only"}
    ):
        raise SnapshotError("curated_identity_or_policy_mismatch")
    specs = contract_document()["fact_tables"]
    if [t["table"] for t in document["tables"]] != sorted(specs):
        raise SnapshotError("curated_table_allowlist_mismatch")
    logical = [{k: v for k, v in t.items() if k != "files"} for t in document["tables"]]
    if descriptor["tables"] != logical or descriptor["quarantine"] != {
        k: v for k, v in document["quarantine"].items() if k != "files"
    }:
        raise SnapshotError("curated_descriptor_projection_mismatch")
    names = {"curated_manifest.json", "manifest.sha256"}
    refs = [f for t in [*document["tables"], document["quarantine"]] for f in t["files"]]
    if (
        len({r["path"] for r in refs}) != len(refs)
        or len(refs) + 2 > limits.max_files
        or sum(r["bytes"] for r in refs) > limits.max_bytes
        or sum(r["row_count"] for r in refs) > limits.max_rows
    ):
        raise SnapshotError("curated_inventory_or_resource_limit")
    names.update(r["path"] for r in refs)
    inventory(root, names)
    rejected = 0
    actual_reasons: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="curated-verify-") as tmp:
        for table in [*document["tables"], document["quarantine"]]:
            is_q = "table" not in table
            table_name = "quarantine" if is_q else table["table"]
            columns = QUARANTINE_COLUMNS if is_q else columns_for(table_name)
            grain = [] if is_q else specs[table_name]["grain"]
            if table["schema"] != columns or table["grain"] != grain:
                raise SnapshotError("curated_declared_schema_mismatch")
            if not is_q:
                rejected += table["rejected_rows"]
                if (
                    table["source_rows"] != table["row_count"] + table["rejected_rows"]
                    or table["source_data_class"] != specs[table_name]["data_class"]
                    or table["classification"] != "curated"
                ):
                    raise SnapshotError("curated_row_reconciliation_mismatch")
            digest = Digest(Path(tmp) / (table_name + ".sqlite"), columns, grain)
            try:
                for number, ref in enumerate(table["files"]):
                    expected = (
                        "quarantine" if is_q else "curated/" + table_name
                    ) + f"/part-{number:06d}.parquet"
                    if ref["path"] != expected or file_hash(root, expected) != (
                        ref["bytes"],
                        ref["sha256"],
                    ):
                        raise SnapshotError("curated_file_checksum_or_path_mismatch")
                    with regular_file(root, expected) as stream:
                        parquet = pq.ParquetFile(
                            stream,
                            pre_buffer=False,
                            arrow_extensions_enabled=False,
                            thrift_string_size_limit=4 * 1024**2,
                            thrift_container_size_limit=1000000,
                        )
                        try:
                            if (
                                not parquet.schema_arrow.equals(
                                    schema_for(columns), check_metadata=True
                                )
                                or parquet.metadata.num_rows != ref["row_count"]
                            ):
                                raise SnapshotError("curated_parquet_schema_or_count_mismatch")
                            if any(
                                parquet.metadata.row_group(i).total_byte_size > 128 * 1024**2
                                for i in range(parquet.num_row_groups)
                            ):
                                raise SnapshotError("curated_row_group_size_limit")
                        finally:
                            parquet.close()
                    for row in iter_rows(root, [ref], limits.batch_rows):
                        if is_q:
                            name = row["source_table"]
                            if name not in specs:
                                raise SnapshotError("invalid_quarantine_source_table")
                            source_row = decoded(row["row_json"], specs[name]["schema"])
                            if (
                                row["source_record_sha256"] != record_sha(source_row)
                                or row["source_grain_json"]
                                != canonical_json(
                                    [cell(source_row[k]) for k in specs[name]["grain"]]
                                ).decode()
                            ):
                                raise SnapshotError("quarantine_lineage_mismatch")
                            actual_reasons[row["reason"]] = actual_reasons.get(row["reason"], 0) + 1
                        digest.add(row)
                if any(table[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("curated_typed_content_mismatch")
            finally:
                digest.close()
    q = document["quarantine"]
    if (
        rejected != q["row_count"]
        or actual_reasons != q["reasons"]
        or sum(q["reasons"].values()) != rejected
        or document["readiness"]
        != {
            "forecast_source": "passed" if not rejected else "failed",
            "forecast_model": "not_ready",
            "anomaly": "not_ready",
            "stockout": "not_ready",
            "replay": "not_ready",
            "rag": "not_applicable",
            "inventory_ready": False,
        }
    ):
        raise SnapshotError("curated_readiness_or_quarantine_mismatch")
    if require_ready and rejected:
        raise SnapshotError("curated_not_ready")
    return document


def build_curated(
    import_root: Path,
    generated_root: Path = Path("data/generated"),
    *,
    config: Config = DEFAULT_CONFIG,
    allow_evaluation_truth: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Result:
    import_root = checked_directory(import_root)
    root = generated_root.absolute()
    if (
        ".." in root.parts
        or root.parts[-2:] != ("data", "generated")
        or root.is_relative_to(import_root)
    ):
        raise SnapshotError("curated_requires_separate_data_generated_root")
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise SnapshotError("symlink_curated_output_directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(root)
    if any(import_root.is_relative_to(root / name) for name in ("curated", "curated-rejected")):
        raise SnapshotError("input_inside_curated_publication_root")
    initial = verify_import(
        import_root, allow_evaluation_truth=allow_evaluation_truth, limits=limits
    )
    with tempfile.TemporaryDirectory(prefix=".curated-build-", dir=root) as tmp:
        stage = Path(tmp)
        # Seal a bounded private input to prevent input mutation during transform.
        copy_snapshot(import_root / "snapshot", stage / "input", initial, limits)
        verified = verify_snapshot(
            stage / "input",
            allow_evaluation_truth=allow_evaluation_truth,
            limits=limits,
            scratch=stage,
        )
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        document = derive(stage / "input", verified, payload, stage, config, limits)
        raw = canonical_json(document) + b"\n"
        write_private(payload / "curated_manifest.json", raw)
        write_private(
            payload / "manifest.sha256", (hashlib.sha256(raw).hexdigest() + "\n").encode()
        )
        verify_curated(payload, require_ready=False, limits=limits)
        ready = document["readiness"]["forecast_source"] == "passed"
        parent = root / ("curated" if ready else "curated-rejected")
        parent.mkdir(exist_ok=True, mode=0o700)
        checked_directory(parent)
        destination = parent / document["curated_dataset_id"]
        fsync_tree(payload)
        try:
            publish_noreplace(payload, destination)
        except FileExistsError:
            existing = verify_curated(destination, require_ready=False, limits=limits)
            if existing["descriptor"] != document["descriptor"]:
                raise SnapshotError("immutable_curated_id_conflict") from None
            return Result("reused" if ready else "quarantined", destination, existing)
        return Result("published" if ready else "quarantined", destination, document)
