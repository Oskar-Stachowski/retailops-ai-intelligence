"""Bounded immutable artifacts shared by fitting and data-only batch scoring."""

from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import canonical_json, read_bytes
from retailops_ai.source_snapshot.importer import write_private


def immutable(path: Path, raw: bytes, *, maximum: int = 128 * 1024**2) -> None:
    if not 0 < len(raw) <= maximum:
        raise ValueError("anomaly_portfolio_artifact_budget")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        if read_bytes(path.parent, path.name, maximum) != raw:
            raise ValueError("anomaly_portfolio_immutable_conflict")
    else:
        write_private(path, raw)


def immutable_json(path: Path, value: Any) -> None:
    immutable(path, canonical_json(value) + b"\n")
