"""Bound complete forecast lineage metadata without enlarging Source worker inputs."""

from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import decode_json, read_bytes

MAX_PARENT_METADATA_BYTES = 16 * 1024**2


def read(path: Path) -> dict[str, Any]:
    return decode_json(
        read_bytes(path.parent, path.name, MAX_PARENT_METADATA_BYTES),
        limit=MAX_PARENT_METADATA_BYTES,
    )
