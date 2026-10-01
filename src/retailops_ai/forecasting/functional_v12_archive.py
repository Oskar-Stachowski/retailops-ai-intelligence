"""Verified compact checkpoints for new cohorts; never regenerates source data.

Only temporary working directories owned by the caller may be discarded after a
checkpoint and its replay pass. This module never deletes input files.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    file_hash,
    inventory,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

MAX_FILES = 20000
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_FILE_BYTES = 1024**3
MAX_MANIFEST_BYTES = 16 * 1024**2
CHUNK_BYTES = 1024**2


def _safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or "\\" in name
        or "\x00" in name
        or path.as_posix() != name
    ):
        raise SnapshotError("checkpoint_unsafe_member_name")


def _files(roots: dict[str, Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    total = 0
    for namespace, root in sorted(roots.items()):
        _safe_name(namespace)
        if "/" in namespace or not stat.S_ISDIR(root.lstat().st_mode):
            raise SnapshotError("checkpoint_invalid_root")
        for base, directories, names in os.walk(root, followlinks=False):
            for entry in (*directories, *names):
                path = Path(base) / entry
                mode = path.lstat().st_mode
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise SnapshotError("checkpoint_special_file")
                if not stat.S_ISREG(mode):
                    continue
                relative = path.relative_to(root).as_posix()
                name = namespace + "/" + relative
                _safe_name(name)
                size, digest = file_hash(root, relative)
                total += size
                if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(result) >= MAX_FILES:
                    raise SnapshotError("checkpoint_input_budget")
                result[name] = {"size_bytes": size, "sha256": digest}
    if not result:
        raise SnapshotError("checkpoint_empty_inputs")
    return dict(sorted(result.items()))


def _add(archive: tarfile.TarFile, name: str, stream: BinaryIO, size: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    info.mtime = 0
    archive.addfile(info, stream)


def seal_checkpoint(roots: dict[str, Path], output: Path, *, lineage: dict[str, Any]) -> Path:
    """Archive all supplied trees, verify every payload, publish without replacement."""
    files = _files(roots)
    descriptor = {
        "version": "forecast-functional-checkpoint-1.0.0",
        "source_regeneration": "forbidden_replay_uses_retained_bytes",
        "qualification": "storage_receipt_only",
        "lineage": lineage,
        "files": files,
        "total_bytes": sum(value["size_bytes"] for value in files.values()),
    }
    checkpoint_id = "functional-checkpoint-sha256-" + canonical_sha256(descriptor)
    payload = canonical_bytes({"checkpoint_id": checkpoint_id, "descriptor": descriptor}) + b"\n"
    if len(payload) > MAX_MANIFEST_BYTES:
        raise SnapshotError("checkpoint_manifest_budget")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=output) as temporary:
        staging = Path(temporary)
        with (staging / "payload.tar.gz").open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw, mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w|", format=tarfile.GNU_FORMAT) as archive:
                    _add(archive, "checkpoint.json", io.BytesIO(payload), len(payload))
                    for name, receipt in files.items():
                        namespace, relative = name.split("/", 1)
                        with regular_file(roots[namespace], relative) as stream:
                            _add(archive, name, stream, receipt["size_bytes"])
        size, digest = file_hash(staging, "payload.tar.gz")
        manifest = json.loads(payload) | {"archive": {"size_bytes": size, "sha256": digest}}
        (staging / "checkpoint_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
        verify_checkpoint(staging)
        if _files(roots) != files:
            raise SnapshotError("checkpoint_inputs_changed_during_archive")
        fsync_tree(staging)
        destination = output / checkpoint_id
        publish_noreplace(staging, destination)
    return destination


def _manifest(root: Path) -> dict[str, Any]:
    inventory(root, {"payload.tar.gz", "checkpoint_manifest.json"})
    manifest = read_json(root, "checkpoint_manifest.json")
    descriptor = manifest["descriptor"]
    receipts = descriptor["files"]
    if (
        descriptor["version"] != "forecast-functional-checkpoint-1.0.0"
        or descriptor["source_regeneration"] != "forbidden_replay_uses_retained_bytes"
        or descriptor["qualification"] != "storage_receipt_only"
        or manifest["checkpoint_id"]
        != "functional-checkpoint-sha256-" + canonical_sha256(descriptor)
        or not receipts
        or len(receipts) > MAX_FILES
    ):
        raise SnapshotError("checkpoint_manifest_identity")
    total = 0
    for name, receipt in receipts.items():
        _safe_name(name)
        if name == "checkpoint.json" or "/" not in name:
            raise SnapshotError("checkpoint_member_namespace")
        size = receipt["size_bytes"]
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise SnapshotError("checkpoint_member_budget")
        total += size
    if total != descriptor["total_bytes"] or total > MAX_TOTAL_BYTES:
        raise SnapshotError("checkpoint_expanded_budget")
    receipt = manifest["archive"]
    if receipt["size_bytes"] > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES or file_hash(
        root, "payload.tar.gz"
    ) != (receipt["size_bytes"], receipt["sha256"]):
        raise SnapshotError("checkpoint_archive_checksum")
    return manifest


def _read_payload(root: Path, manifest: dict[str, Any], destination: Path | None) -> None:
    expected = manifest["descriptor"]["files"]
    seen: set[str] = set()
    with regular_file(root, "payload.tar.gz") as raw:
        with tarfile.open(fileobj=raw, mode="r|gz") as archive:
            first = True
            for member in archive:
                _safe_name(member.name)
                if not member.isfile() or member.linkname or member.pax_headers:
                    raise SnapshotError("checkpoint_non_regular_archive_member")
                stream = archive.extractfile(member)
                if stream is None:
                    raise SnapshotError("checkpoint_missing_member_payload")
                if first:
                    first = False
                    if member.name != "checkpoint.json" or member.size > MAX_MANIFEST_BYTES:
                        raise SnapshotError("checkpoint_embedded_manifest_missing")
                    expected_header = {k: manifest[k] for k in ("checkpoint_id", "descriptor")}
                    if json.loads(stream.read(MAX_MANIFEST_BYTES + 1)) != expected_header:
                        raise SnapshotError("checkpoint_embedded_manifest_mismatch")
                    continue
                if member.name in seen or member.name not in expected:
                    raise SnapshotError("checkpoint_duplicate_or_extra_member")
                receipt = expected[member.name]
                if member.size != receipt["size_bytes"]:
                    raise SnapshotError("checkpoint_member_size")
                seen.add(member.name)
                digest = hashlib.sha256()
                count = 0
                target = None
                try:
                    if destination is not None:
                        path = destination / member.name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        target = path.open("xb")
                    while block := stream.read(CHUNK_BYTES):
                        count += len(block)
                        if count > member.size:
                            raise SnapshotError("checkpoint_member_overflow")
                        digest.update(block)
                        if target is not None:
                            target.write(block)
                finally:
                    if target is not None:
                        target.close()
                if count != member.size or digest.hexdigest() != receipt["sha256"]:
                    raise SnapshotError("checkpoint_member_checksum")
    if seen != set(expected):
        raise SnapshotError("checkpoint_missing_members")


def verify_checkpoint(root: Path) -> dict[str, Any]:
    """Verify compressed and decompressed checksums; no source generator is imported."""
    manifest = _manifest(root)
    _read_payload(root, manifest, None)
    return manifest


def restore_checkpoint(root: Path, output: Path) -> Path:
    """Restore retained bytes atomically into a new, otherwise empty directory."""
    manifest = _manifest(root)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / str(manifest["checkpoint_id"])
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=output) as temporary:
        staging = Path(temporary)
        _read_payload(root, manifest, staging)
        fsync_tree(staging)
        publish_noreplace(staging, destination)
    return destination
