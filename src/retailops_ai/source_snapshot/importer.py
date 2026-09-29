"""Seal and validate a frozen input, then publish or reuse an immutable local copy."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from retailops_ai.source_snapshot import IMPORT_VERSION
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    file_hash,
    json_sha256,
    read_bytes,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.protocol import (
    Limits,
    Snapshot,
    inspect_snapshot,
    resource_bytes,
    verify_metadata,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.source_snapshot.tables import verify_tables

DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class ImportResult:
    status: str
    directory: Path
    snapshot: Snapshot

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_dataset_id": self.snapshot.source_id,
            "snapshot_id": self.snapshot.snapshot_id,
            "destination": str(self.directory),
            "tables": len(self.snapshot.manifest["tables"]),
            "rows": sum(t["row_count"] for t in self.snapshot.manifest["tables"]),
            "evaluation_truth": self.snapshot.manifest["descriptor"]["include_evaluation_truth"],
            "typed_canonical_parity": "passed",
            "curated": "not_performed_by_this_command",
        }


def verify_snapshot(
    root: Path,
    *,
    allow_evaluation_truth: bool = False,
    required_use_cases: tuple[str, ...] = ("forecast_source",),
    limits: Limits = DEFAULT_LIMITS,
    scratch: Path | None = None,
) -> Snapshot:
    root = checked_directory(root)
    snapshot = inspect_snapshot(root, allow_evaluation_truth, limits)
    for ref in snapshot.references:
        if file_hash(root, ref["path"]) != (ref["bytes"], ref["sha256"]):
            raise SnapshotError("byte_checksum_or_size_mismatch")
    verify_metadata(root, snapshot, required_use_cases)
    with tempfile.TemporaryDirectory(prefix=".typed-verify-", dir=scratch) as temporary:
        verify_tables(root, snapshot, Path(temporary), limits)
        if snapshot.manifest["schema_version"] == "1.1.0":
            from retailops_ai.source_snapshot.inventory_projection import verify_projection

            verify_projection(root, snapshot, Path(temporary), limits)
    return snapshot


def prepare_generated(root: Path) -> Path:
    root = root.absolute()
    if ".." in root.parts or root.parts[-2:] != ("data", "generated"):
        raise SnapshotError("output_requires_data_generated_root")
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise SnapshotError("symlink_output_directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(root)
    snapshots = root / "snapshots"
    snapshots.mkdir(exist_ok=True, mode=0o700)
    return checked_directory(snapshots)


def write_private(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())


def copy_snapshot(source: Path, target: Path, snapshot: Snapshot, limits: Limits) -> None:
    target.mkdir(mode=0o700)
    expected = {ref["path"]: (ref["bytes"], ref["sha256"]) for ref in snapshot.references}
    expected["snapshot_manifest.json"] = (
        len(read_bytes(source, "snapshot_manifest.json")),
        snapshot.manifest_sha256,
    )
    checksum = (snapshot.manifest_sha256 + "\n").encode("ascii")
    expected["manifest.sha256"] = (len(checksum), hashlib.sha256(checksum).hexdigest())
    total = 0
    for name in sorted(snapshot.names):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        size, digest = 0, hashlib.sha256()
        descriptor = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as output, regular_file(source, name) as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                if size > expected[name][0] or total > limits.max_bytes:
                    raise SnapshotError("copy_size_limit_or_source_changed")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if (size, digest.hexdigest()) != expected[name]:
            raise SnapshotError("byte_checksum_or_source_changed")
    for path in target.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)


def receipt(snapshot: Snapshot) -> dict[str, Any]:
    if snapshot.manifest["schema_version"] == "1.1.0":
        from retailops_ai.source_snapshot.inventory_protocol import resource_bytes as handoff_bytes
    else:
        handoff_bytes = resource_bytes
    code = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    return {
        "schema_version": IMPORT_VERSION,
        "source_dataset_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_manifest_sha256": snapshot.manifest_sha256,
        "files": [
            {"path": name, "bytes": ref["bytes"], "sha256": ref["sha256"]}
            for name, ref in sorted((r["path"], r) for r in snapshot.references)
        ],
        "verification": {
            "byte_checksums": "passed",
            "typed_canonical_hashes": "passed",
            "schema_grain_ranges_partitions": "passed",
            "source_hard_gates": "passed",
            "required_use_cases": snapshot.manifest["descriptor"]["required_use_cases"],
        },
        "importer": {
            "version": IMPORT_VERSION,
            "code_files": code,
            "code_sha256": json_sha256(code),
            "dependency_sha256": hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
            "handoff_sha256": hashlib.sha256(handoff_bytes("contract.json")).hexdigest(),
            "pyarrow_version": pa.__version__,
        },
    }


def verify_import(
    root: Path,
    *,
    allow_evaluation_truth: bool = False,
    required_use_cases: tuple[str, ...] = ("forecast_source",),
    limits: Limits = DEFAULT_LIMITS,
) -> Snapshot:
    root = checked_directory(root)
    if {p.name for p in root.iterdir()} != {
        "snapshot",
        "import_manifest.json",
        "import_manifest.sha256",
    }:
        raise SnapshotError("invalid_import_inventory")
    document = read_json(root, "import_manifest.json")
    _, digest = file_hash(root, "import_manifest.json")
    if read_bytes(root, "import_manifest.sha256", 128) != (digest + "\n").encode():
        raise SnapshotError("import_manifest_checksum_mismatch")
    snapshot = verify_snapshot(
        root / "snapshot",
        allow_evaluation_truth=allow_evaluation_truth,
        required_use_cases=required_use_cases,
        limits=limits,
    )
    implementation = document.get("importer", {})
    if (
        document.get("schema_version") != IMPORT_VERSION
        or implementation.get("version") != IMPORT_VERSION
        or implementation.get("code_sha256") != json_sha256(implementation.get("code_files"))
        or implementation.get("handoff_sha256") != receipt(snapshot)["importer"]["handoff_sha256"]
        or any(
            document.get(k) != v
            for k, v in {
                "source_dataset_id": snapshot.source_id,
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_manifest_sha256": snapshot.manifest_sha256,
            }.items()
        )
        or document.get("files")
        != [
            {"path": r["path"], "bytes": r["bytes"], "sha256": r["sha256"]}
            for r in sorted(snapshot.references, key=lambda r: r["path"])
        ]
        or document.get("verification") != receipt(snapshot)["verification"]
    ):
        raise SnapshotError("import_receipt_mismatch")
    return snapshot


def import_snapshot(
    source: Path,
    generated_root: Path = Path("data/generated"),
    *,
    allow_evaluation_truth: bool = False,
    required_use_cases: tuple[str, ...] = ("forecast_source",),
    limits: Limits = DEFAULT_LIMITS,
) -> ImportResult:
    source = checked_directory(source)
    initial = inspect_snapshot(source, allow_evaluation_truth, limits)
    if generated_root.absolute().is_relative_to(source):
        raise SnapshotError("output_must_be_outside_snapshot_input")
    parent = prepare_generated(generated_root)
    if parent == source or source.is_relative_to(parent):
        raise SnapshotError("input_must_be_outside_import_publication_root")
    destination = parent / initial.source_id
    with tempfile.TemporaryDirectory(prefix=".snapshot-import-", dir=parent) as temporary:
        stage = Path(temporary)
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        copy_snapshot(source, payload / "snapshot", initial, limits)
        verified = verify_snapshot(
            payload / "snapshot",
            allow_evaluation_truth=allow_evaluation_truth,
            required_use_cases=required_use_cases,
            limits=limits,
            scratch=stage,
        )
        if verified.manifest_sha256 != initial.manifest_sha256:
            raise SnapshotError("source_changed_during_copy")
        raw = canonical_json(receipt(verified)) + b"\n"
        write_private(payload / "import_manifest.json", raw)
        write_private(
            payload / "import_manifest.sha256", (hashlib.sha256(raw).hexdigest() + "\n").encode()
        )
        fsync_tree(payload)
        try:
            publish_noreplace(payload, destination)
        except FileExistsError:
            existing = verify_import(
                destination,
                allow_evaluation_truth=allow_evaluation_truth,
                required_use_cases=required_use_cases,
                limits=limits,
            )
            if (
                existing.source_id != verified.source_id
                or existing.snapshot_id != verified.snapshot_id
            ):
                raise SnapshotError("immutable_source_id_conflict") from None
            return ImportResult("reused", destination, existing)
        return ImportResult("published", destination, verified)
