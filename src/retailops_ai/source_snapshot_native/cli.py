"""Explicit file-only CLI; the operational API never imports snapshots implicitly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retailops-ai-snapshot")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "import", "verify-import"):
        command = commands.add_parser(name)
        command.add_argument(
            "--snapshot-dir" if name != "verify-import" else "--import-dir",
            type=Path,
            required=True,
        )
        command.add_argument("--allow-evaluation-truth", action="store_true")
        command.add_argument("--require-use-case", action="append", default=None)
        command.add_argument("--max-bytes", type=int, default=2 * 1024**3)
        command.add_argument("--max-files", type=int, default=10000)
        command.add_argument("--max-rows", type=int, default=20000000)
        command.add_argument("--batch-rows", type=int, default=8192)
        if name == "import":
            command.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    args = parser.parse_args(argv)
    try:
        from retailops_ai.source_snapshot.files import SnapshotError
        from retailops_ai.source_snapshot.importer import (
            import_snapshot,
            verify_import,
            verify_snapshot,
        )
        from retailops_ai.source_snapshot.protocol import Limits
    except ImportError:
        print(
            '{"error":"snapshot_dependencies_required","install":"uv sync --locked --extra snapshot"}',
            file=sys.stderr,
        )
        return 2
    try:
        options = {
            "allow_evaluation_truth": args.allow_evaluation_truth,
            "required_use_cases": tuple(args.require_use_case or ["forecast_source"]),
            "limits": Limits(args.max_bytes, args.max_files, args.max_rows, args.batch_rows),
        }
        if args.command == "import":
            result = import_snapshot(args.snapshot_dir, args.generated_root, **options)
            summary = result.summary()
        else:
            snapshot = (
                verify_import(args.import_dir, **options)
                if args.command == "verify-import"
                else verify_snapshot(args.snapshot_dir, **options)
            )
            summary = {
                "status": "verified",
                "source_dataset_id": snapshot.source_id,
                "snapshot_id": snapshot.snapshot_id,
                "typed_canonical_parity": "passed",
                "tables": len(snapshot.manifest["tables"]),
            }
    except SnapshotError as exc:
        print(json.dumps({"error": "snapshot_rejected", "code": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print(
            '{"error":"snapshot_rejected","code":"invalid_or_unavailable_snapshot"}',
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
