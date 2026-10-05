"""Lossless bounded parent restoration; no tar extraction or path rewriting."""

import hashlib
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import checked_directory, file_hash, relative_path
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout_temporal_storage.store import PartitionInputs


def partition_inputs(roots: dict[str, Path]) -> PartitionInputs:
    """The import package owns a nested snapshot; label replay reads that snapshot."""
    return PartitionInputs(
        roots["curated"],
        roots["private_import"] / "snapshot",
        roots["features"],
        roots["upstream"],
        roots["labels"],
    )


ROLES = {"facts_import", "private_import", "curated", "features", "labels", "upstream", "temporal"}
MAX_BYTES = 512 * 1024**2
MAX_FILES = 4096


def reserve() -> int:
    return (
        6 * 1024**3
        if (
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_OS") == "Linux"
            and os.environ.get("GITHUB_REPOSITORY") == "Oskar-Stachowski/retailops-ai-intelligence"
        )
        else 50 * 1024**3
    )


def parents(checkpoint: dict[str, Any]) -> dict[str, Any]:
    if checkpoint.get("schema_version") not in {
        "stockout-remote-checkpoint-1.0.0",
        "stockout-future-source-checkpoint-1.0.0",
    } or any(
        checkpoint.get(k) is not False
        for k in (
            "final_test_outcomes_evaluated",
            "independent_quality_accepted",
            "model_promoted",
            "ai08_ready",
        )
    ):
        raise ValueError("stockout_restore_unopened_checkpoint_required")
    p: dict[str, Any] = checkpoint["parents"]
    if not isinstance(p, dict):
        raise ValueError("stockout_restore_parent_receipt_required")
    if (
        p.get("verified_lossless") is not True
        or p.get("final_test_outcomes_evaluated") is not False
    ):
        raise ValueError("stockout_restore_parent_receipt_required")
    if p.get("archive_file") != "parents.tar.gz" or not 0 < p.get("archive_bytes", 0) <= MAX_BYTES:
        raise ValueError("stockout_restore_archive_boundary")
    files = p["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("stockout_restore_inventory_limit")
    total, roles = 0, set()
    for name, ref in files.items():
        parts = relative_path(name).parts
        roles.add(parts[0])
        if len(parts) < 2 or parts[0] not in ROLES or set(ref) != {"bytes", "mode", "sha256"}:
            raise ValueError("stockout_restore_parent_boundary")
        if type(ref["bytes"]) is not int or not 0 <= ref["bytes"] <= 16 * 1024**2:
            raise ValueError("stockout_restore_member_size")
        if ref["mode"] not in {0o600, 0o640, 0o644}:
            raise ValueError("stockout_restore_member_mode")
        if (
            not isinstance(ref["sha256"], str)
            or len(ref["sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in ref["sha256"])
        ):
            raise ValueError("stockout_restore_member_digest")
        total += ref["bytes"]
    if roles != ROLES or total != p["unpacked_bytes"] or total > MAX_BYTES:
        raise ValueError("stockout_restore_total_or_roles")
    if set(p["parent_roots"]) != {"facts_import", "private_import", "curated"}:
        raise ValueError("stockout_restore_nested_roots")
    for role, name in p["parent_roots"].items():
        parts = relative_path(name).parts
        if parts[0] != role or not any(n.startswith(name + "/") for n in files):
            raise ValueError("stockout_restore_nested_root_boundary")
    return p


def verified_tree(root: Path, files: dict[str, Any]) -> None:
    root = checked_directory(root)
    found = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("stockout_restore_unsafe_existing_tree")
        if path.is_file():
            name = path.relative_to(root).as_posix()
            found.add(name)
            ref = files.get(name)
            if (
                ref is None
                or file_hash(root, name) != (ref["bytes"], ref["sha256"])
                or path.stat().st_mode & 0o777 != ref["mode"]
            ):
                raise ValueError("stockout_restore_file_changed")
    if found != set(files):
        raise ValueError("stockout_restore_inventory_changed")


def restore(archive: Path, checkpoint: dict[str, Any], target: Path) -> dict[str, Path]:
    p = parents(checkpoint)
    root = checked_directory(target.parent)
    if target.name in {"", ".", ".."} or target.is_symlink() or ".." in target.parts:
        raise ValueError("stockout_restore_target_boundary")
    archive = archive.absolute()
    if archive == target or archive.is_relative_to(target):
        raise ValueError("stockout_restore_archive_target_overlap")
    if file_hash(archive.parent, archive.name) != (p["archive_bytes"], p["archive_sha256"]):
        raise ValueError("stockout_restore_archive_changed")
    destination = root / target.name
    if destination.exists():
        verified_tree(destination, p["files"])
    else:
        if shutil.disk_usage(root).free - p["unpacked_bytes"] < reserve():
            raise ValueError("stockout_restore_free_disk_reserve")
        with tempfile.TemporaryDirectory(prefix=".stockout-restore-", dir=root) as temporary:
            staged = Path(temporary) / "parents"
            staged.mkdir(mode=0o700)
            seen = set()
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    name = relative_path(member.name).as_posix()
                    ref = p["files"].get(name)
                    if (
                        ref is None
                        or name in seen
                        or not member.isfile()
                        or (member.size, member.mode) != (ref["bytes"], ref["mode"])
                    ):
                        raise ValueError("stockout_restore_tar_inventory_or_metadata")
                    seen.add(name)
                    if shutil.disk_usage(root).free - member.size < reserve():
                        raise ValueError("stockout_restore_free_disk_reserve")
                    stream = tar.extractfile(member)
                    if stream is None:
                        raise ValueError("stockout_restore_member_missing")
                    path = staged / name
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    digest, count = hashlib.sha256(), 0
                    with stream, path.open("xb") as output:
                        while chunk := stream.read(1024**2):
                            count += len(chunk)
                            if count > ref["bytes"]:
                                raise ValueError("stockout_restore_member_expansion")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    path.chmod(ref["mode"])
                    if count != ref["bytes"] or digest.hexdigest() != ref["sha256"]:
                        raise ValueError("stockout_restore_member_digest_changed")
            if seen != set(p["files"]):
                raise ValueError("stockout_restore_tar_missing_parent")
            verified_tree(staged, p["files"])
            if file_hash(archive.parent, archive.name) != (p["archive_bytes"], p["archive_sha256"]):
                raise ValueError("stockout_restore_archive_changed_during_read")
            fsync_tree(staged)
            publish_noreplace(staged, destination)
    return {
        **{n: destination / path for n, path in p["parent_roots"].items()},
        **{n: destination / n for n in ("features", "labels", "upstream", "temporal")},
    }
