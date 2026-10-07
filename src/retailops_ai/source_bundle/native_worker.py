"""Run the approved native importer in a fresh process with its own module resolution."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read(65537))
        # Fixed owned package path, independent of PYTHONPATH. The parent/model
        # process keeps the complete original frozen source_snapshot package.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        package = importlib.import_module("retailops_ai.source_snapshot")
        native = Path(__file__).resolve().parents[1] / "source_snapshot_native"
        package.__path__ = [str(native), *package.__path__]
        # All ten owner modules resolve here; schemas/locks remain resources of
        # the original package. The native importer records its own directory.
        from retailops_ai.source_snapshot.importer import import_snapshot
        from retailops_ai.source_snapshot.protocol import Limits, inspect_snapshot, verify_metadata

        root = Path(request["download_directory"])
        manifest = json.loads((root / "bundle.json").read_bytes())
        limits = Limits(max_bytes=request["max_bytes"], max_files=request["max_files"])
        snapshot = inspect_snapshot(root / "snapshot", False, limits)
        if (
            snapshot.snapshot_id != manifest["snapshot_id"]
            or snapshot.source_id != manifest["source_dataset_id"]
            or snapshot.manifest["schema_version"] != manifest["source_snapshot_version"]
        ):
            raise ValueError("source_bundle_identity_mismatch")
        required = tuple(request["required_use_cases"])
        verify_metadata(root / "snapshot", snapshot, required)
        result = import_snapshot(
            root / "snapshot",
            Path(request["generated_root"]),
            required_use_cases=required,
            limits=limits,
        )
        sys.stdout.write(json.dumps(result.summary()) + "\n")
        return 0
    except Exception:  # noqa: BLE001 - fixed failure only; no source/configuration exception text
        sys.stdout.write('{"status":"failed","code":"native_import_rejected"}\n')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
