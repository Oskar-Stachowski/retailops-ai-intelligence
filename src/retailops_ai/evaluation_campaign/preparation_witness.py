"""Stdlib-only completion witness, loaded by the isolated native producer worker.

Call only after the named native validator returned successfully. This record
does not grant authority by itself: checkpoint reuse also requires the trusted
worker result, measurement and exact identity retained outside the archive.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

VERSION = "ai09-native-preparation-witness-1.0.0"
VALIDATORS = {
    "generation": "data.inventory.source_dataset_io.read_source_dataset",
    "qualification": "data.inventory.qualification_io.read_qualification",
    "export": "data.export.inventory_snapshot.verify_inventory_snapshot",
    "import": "retailops_ai.source_snapshot.importer.verify_snapshot",
    "curation": "retailops_ai.curated.builder.verify_curated",
}
MAX_FILES = 20000
MAX_BYTES = 4 * 1024**3
MAX_FILE_BYTES = 1024**3


def inventory(root: Path) -> dict[str, dict[str, Any]]:
    """Capture bounded complete output bytes without following links or loading data."""
    if (
        not root.is_absolute()
        or ".." in root.parts
        or not root.is_dir()
        or any(p.is_symlink() for p in (root, *root.parents))
    ):
        raise ValueError("preparation_witness_directory")
    result: dict[str, dict[str, Any]] = {}
    total = 0
    for base, directories, names in os.walk(root, followlinks=False):
        for name in (*directories, *names):
            path = Path(base) / name
            before = path.lstat()
            if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
                raise ValueError("preparation_witness_special_file")
            if stat.S_ISDIR(before.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            if any(c in relative for c in ("\\", ":", "\x00")):
                raise ValueError("preparation_witness_path")
            if (
                len(result) >= MAX_FILES
                or before.st_size > MAX_FILE_BYTES
                or total + before.st_size > MAX_BYTES
            ):
                raise ValueError("preparation_witness_inventory_budget")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                    raise ValueError("preparation_witness_file_changed")
                digest, count = hashlib.sha256(), 0
                while chunk := stream.read(1024**2):
                    digest.update(chunk)
                    count += len(chunk)
                    if count > before.st_size:
                        raise ValueError("preparation_witness_file_changed")
                after = os.fstat(stream.fileno())
            if count != before.st_size or (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ):
                raise ValueError("preparation_witness_file_changed")
            result[relative] = {"size_bytes": count, "sha256": digest.hexdigest()}
            total += count
    if not result:
        raise ValueError("preparation_witness_empty_output")
    return dict(sorted(result.items()))


def capture(
    phase: str,
    directory: Path,
    *,
    validator_code_sha256: str,
    identity_sha256: str,
    output_id: str,
    input_ids: list[str],
    report_paths: list[str],
) -> dict[str, Any]:
    """Retain the actual validator's complete output inventory and report references."""
    if phase not in VALIDATORS or not report_paths or len(set(report_paths)) != len(report_paths):
        raise ValueError("preparation_witness_phase_or_reports")
    files = inventory(directory)
    if any(path not in files for path in report_paths):
        raise ValueError("preparation_witness_report_missing")
    return {
        "version": VERSION,
        "phase": phase,
        "native_validator": VALIDATORS[phase],
        "validator_code_sha256": validator_code_sha256,
        "identity_sha256": identity_sha256,
        "output_id": output_id,
        "input_ids": input_ids,
        "native_validation_completed": True,
        "validation_reports": {path: files[path] for path in report_paths},
        "output_files": files,
        "project_generation_receipt": False,
        "final_test_authorized": False,
        "quality_qualified": False,
    }
