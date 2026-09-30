"""Import a bounded downloaded Actions ZIP without regenerating any source data."""

from __future__ import annotations

import argparse
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecasting.functional_v12_archive import verify_checkpoint
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, file_hash
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

FILES = {"payload.tar.gz", "checkpoint_manifest.json"}


def import_checkpoint_zip(
    archive: Path,
    output: Path,
    *,
    zip_sha256: str,
    freeze_id: str,
    seed: int,
    maximum_bytes: int,
) -> dict[str, Any]:
    """Transport validation only; a partial checkpoint can never qualify a campaign."""
    if (
        archive.is_symlink()
        or not archive.is_file()
        or type(maximum_bytes) is not int
        or not 1 <= maximum_bytes <= 400 * 1024**2
        or archive.stat().st_size > maximum_bytes + 1024**2
        or not freeze_id.startswith("functional-v12-freeze-sha256-")
        or type(seed) is not int
    ):
        raise SnapshotError("cohort_zip_input_or_budget")
    if file_hash(archive.parent, archive.name)[1] != zip_sha256:
        raise SnapshotError("cohort_zip_download_checksum")
    output.mkdir(parents=True, exist_ok=True)
    checked_directory(output)
    with tempfile.TemporaryDirectory(prefix=".cohort-download-", dir=output) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as zipped:
            entries = zipped.infolist()
            payload = [entry for entry in entries if not entry.is_dir()]
            names = [PurePosixPath(entry.filename) for entry in entries]
            if (
                len(entries) > 3
                or len(payload) != 2
                or any(
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in str(name)
                    or "\x00" in str(name)
                    for name in names
                )
                or {PurePosixPath(entry.filename).name for entry in payload} != FILES
                or len({str(PurePosixPath(entry.filename).parent) for entry in payload}) != 1
                or sum(entry.file_size for entry in payload) > maximum_bytes
                or any(
                    entry.flag_bits & 1
                    or stat.S_ISLNK(entry.external_attr >> 16)
                    or entry.file_size < 0
                    for entry in entries
                )
            ):
                raise SnapshotError("cohort_zip_inventory_or_expansion_budget")
            written = 0
            for entry in payload:
                with (
                    zipped.open(entry) as source,
                    (root / PurePosixPath(entry.filename).name).open("xb") as target,
                ):
                    while chunk := source.read(1024**2):
                        written += len(chunk)
                        if written > maximum_bytes:
                            raise SnapshotError("cohort_zip_expansion_budget")
                        target.write(chunk)
        checkpoint = verify_checkpoint(root)
        lineage = checkpoint["descriptor"]["lineage"]
        if (
            lineage["freeze_id"] != freeze_id
            or lineage["seed"] != seed
            or lineage["scope"]
            not in {
                "cohort_preparation_only",
                "partial_preparation_not_qualified",
            }
        ):
            raise SnapshotError("cohort_zip_wrong_frozen_parent_or_seed")
        destination = output / checkpoint["checkpoint_id"]
        fsync_tree(root)
        if destination.exists():
            if verify_checkpoint(destination) != checkpoint:
                raise SnapshotError("cohort_zip_existing_checkpoint_differs")
        else:
            publish_noreplace(root, destination)
        return {
            "status": "passed",
            "scope": "transport_integrity_not_forecast_qualification",
            "checkpoint_id": checkpoint["checkpoint_id"],
            "directory": str(destination),
            "checkpoint_scope": lineage["scope"],
            "checkpoint_bytes": written,
            "zip_sha256": zip_sha256,
            "freeze_id": freeze_id,
            "seed": seed,
            "source_regenerated": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zip-sha256", required=True)
    parser.add_argument("--freeze-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--maximum-bytes", type=int, required=True)
    args = parser.parse_args()
    result = import_checkpoint_zip(
        args.zip,
        args.output,
        zip_sha256=args.zip_sha256,
        freeze_id=args.freeze_id,
        seed=args.seed,
        maximum_bytes=args.maximum_bytes,
    )
    print(canonical_bytes(result).decode())


if __name__ == "__main__":
    main()
