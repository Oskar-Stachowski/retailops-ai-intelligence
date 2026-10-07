"""Bounded typed reads after full source → curated → raw-DQ verification."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, encoded
from retailops_ai.day_qualification.contract import MAX_BYTES, TABLES, Coverage, Day
from retailops_ai.day_qualification.projection import declarations
from retailops_ai.full_raw_dq.source import TABLES as FULL_TABLES
from retailops_ai.full_raw_dq.source import ParentFacts, projection
from retailops_ai.full_raw_dq.store import Manifest as FullManifest
from retailops_ai.full_raw_dq.store import verify_replay
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    inventory,
    json_sha256,
    regular_file,
)
from retailops_ai.source_snapshot.importer import verify_import

Row = dict[str, Any]
COVERAGE_FILES = {"days.jsonl", "coverage_manifest.json", "manifest.sha256"}


def read_artifact(root: Path, name: str, limit: int) -> bytes:
    """Reject aliases and changes using the same bounded file descriptor."""
    with regular_file(root, name) as stream:
        before = os.fstat(stream.fileno())
        if before.st_nlink != 1 or before.st_size > limit:
            raise SnapshotError("day_qualification_hardlink_or_size_limit")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        if len(raw) != before.st_size or (
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_nlink,
        ) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink):
            raise SnapshotError("day_qualification_artifact_changed_during_read")
        return raw


def load_tables(curated: Path, document: Row) -> dict[str, list[Row]]:
    tables: dict[str, list[Row]] = {}
    count = size = 0
    with tempfile.TemporaryDirectory(prefix="day-parent-") as tmp:
        for name in sorted(set(TABLES) | set(FULL_TABLES)):
            spec = next(t for t in document["tables"] if t["table"] == name)
            digest = Digest(
                Path(tmp) / (name + ".sqlite"), columns_for(name, "1.2.0"), spec["grain"]
            )
            tables[name] = []
            try:
                for row in iter_rows(curated, spec["files"], 256):
                    count += 1
                    size += len(encoded(row))
                    if count > 100000 or size > 128 * 1024**2:
                        raise SnapshotError("day_qualification_parent_limit")
                    digest.add(row)
                    tables[name].append(row)
                if any(spec[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("day_qualification_curated_changed_during_load")
            finally:
                digest.close()
    return tables


def verify_coverage(root: Path, source: Row, expected: list[Day]) -> Coverage:
    root = checked_directory(root)
    inventory(root, COVERAGE_FILES)
    document = read_artifact(root, "coverage_manifest.json", 1024**2)
    coverage = Coverage.model_validate_json(canonical_json(decode_json(document)))
    descriptor = coverage.descriptor
    if (
        document != canonical_json(coverage.model_dump(mode="json")) + b"\n"
        or read_artifact(root, "manifest.sha256", 128)
        != (hashlib.sha256(document).hexdigest() + "\n").encode()
        or coverage.coverage_id
        != "day-coverage-sha256-" + json_sha256(descriptor.model_dump(mode="json"))
    ):
        raise SnapshotError("day_coverage_identity_or_seal_mismatch")
    if (
        descriptor.source_dataset_id != "source-sha256-" + json_sha256(source)
        or descriptor.source_descriptor_sha256 != json_sha256(source)
        or descriptor.model_dump(mode="json")["source_tables"]
        != {name: source["tables"][name] for name in TABLES}
        or source["schema_version"] != "2.8.0"
    ):
        raise SnapshotError("day_coverage_source_binding_mismatch")
    rows = read_artifact(root, "days.jsonl", MAX_BYTES)
    expected_raw = b"".join(canonical_json(d.model_dump(mode="json")) + b"\n" for d in expected)
    if (
        rows != expected_raw
        or descriptor.rows_sha256 != hashlib.sha256(rows).hexdigest()
        or descriptor.row_count != len(expected)
    ):
        raise SnapshotError("day_coverage_native_semantics_mismatch")
    return coverage


def parents(
    replay_dir: Path, coverage_dir: Path, curated_dir: Path, import_dir: Path
) -> tuple[FullManifest, Coverage, list[Day], Row, bytes, ParentFacts]:
    full = verify_replay(replay_dir, curated_dir, import_dir)
    imported = verify_import(import_dir, required_use_cases=("anomaly_source",))
    document = verify_curated(curated_dir)
    desc = document["descriptor"]
    if (
        document["schema_version"] != "1.2.0"
        or document["curated_dataset_id"] != full.descriptor.parent.curated_dataset_id
        or json_sha256(desc) != full.descriptor.parent.curated_descriptor_sha256
    ):
        raise SnapshotError("day_qualification_parent_changed")
    source = imported.manifest["source"]["descriptor"]
    tables = load_tables(curated_dir, document)
    expected = declarations(
        {name: tables[name] for name in TABLES}, source["resolved_parameters"]["start_date"]
    )
    coverage = verify_coverage(coverage_dir, source, expected)
    raw = read_artifact(replay_dir, "raw/events.jsonl", MAX_BYTES)
    replay_raw = read_artifact(replay_dir, "replay.json", MAX_BYTES)
    if (
        hashlib.sha256(raw).hexdigest() != full.descriptor.raw_sha256
        or hashlib.sha256(replay_raw).hexdigest() != full.descriptor.replay_sha256
    ):
        raise SnapshotError("day_qualification_replay_changed_during_load")
    return (
        full,
        coverage,
        expected,
        json.loads(replay_raw),
        raw,
        projection(tables, desc["source_parameters"]["seed"]),
    )
