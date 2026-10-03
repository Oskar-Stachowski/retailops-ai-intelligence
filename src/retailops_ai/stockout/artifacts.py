"""Atomic immutable private JSON publication with an explicit per-role byte bound."""

import tempfile
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_bytes
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import publish_noreplace


def write_artifact(document: dict[str, Any], target: Path, *, max_bytes: int) -> str:
    raw = canonical_json(document) + b"\n"
    if len(raw) > max_bytes:
        raise SnapshotError("stockout_artifact_output_byte_limit")
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise SnapshotError("stockout_artifact_symlink_output")
    if ".." in target.parts:
        raise SnapshotError("stockout_artifact_unsafe_output_path")
    if target.exists():
        if read_bytes(target.parent, target.name, max_bytes) != raw:
            raise SnapshotError("stockout_artifact_immutable_output_conflict")
        return "reused"
    with tempfile.TemporaryDirectory(prefix=".stockout-artifact-", dir=target.parent) as tmp:
        staged = Path(tmp) / "artifact.json"
        write_private(staged, raw)
        try:
            publish_noreplace(staged, target)
        except FileExistsError:
            if read_bytes(target.parent, target.name, max_bytes) != raw:
                raise SnapshotError("stockout_artifact_immutable_output_conflict") from None
            return "reused"
    return "published"
