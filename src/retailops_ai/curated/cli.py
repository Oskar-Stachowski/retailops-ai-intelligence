"""Explicit curated build/verify/as-of commands, independent of the API/agent CLI."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retailops-ai-curated")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--import-dir", type=Path, required=True)
    build.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    build.add_argument("--allow-evaluation-truth", action="store_true")
    build.add_argument("--currency", action="append")
    for name in ("verify", "as-of"):
        command = commands.add_parser(name)
        command.add_argument("--curated-dir", type=Path, required=True)
        if name == "as-of":
            command.add_argument("--origin", required=True)
            command.add_argument("--table", default="daily_demand_versions")
            command.add_argument("--business-date")
            command.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    try:
        from retailops_ai.curated.builder import build_curated, verify_curated
        from retailops_ai.curated.contract import Config, cell
        from retailops_ai.curated.reader import rows_as_of
        from retailops_ai.source_snapshot.files import SnapshotError
    except ImportError:
        print(
            '{"error":"snapshot_dependencies_required","install":"uv sync --locked --extra snapshot"}',
            file=sys.stderr,
        )
        return 2
    try:
        if args.command == "build":
            result = build_curated(
                args.import_dir,
                args.generated_root,
                config=Config(tuple(sorted(set(args.currency)))) if args.currency else Config(),
                allow_evaluation_truth=args.allow_evaluation_truth,
            )
            print(json.dumps(result.summary(), sort_keys=True))
            return 0 if result.manifest["readiness"]["forecast_source"] == "passed" else 2
        if args.command == "verify":
            document = verify_curated(args.curated_dir)
            summary = {
                "status": "verified",
                "curated_dataset_id": document["curated_dataset_id"],
                "readiness": document["readiness"],
            }
        else:
            if not 1 <= args.limit <= 10000:
                raise SnapshotError("as_of_output_limit")
            rows: list[dict[str, object]] = []
            for row in rows_as_of(
                args.curated_dir,
                datetime.fromisoformat(args.origin),
                table=args.table,
                business_date=date.fromisoformat(args.business_date)
                if args.business_date
                else None,
            ):
                if len(rows) >= args.limit:
                    raise SnapshotError("as_of_output_limit_exceeded")
                rows.append({k: cell(v) for k, v in row.items()})
            summary = {"status": "passed", "rows": rows, "table": args.table, "origin": args.origin}
    except SnapshotError as exc:
        print(json.dumps({"error": "curated_rejected", "code": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print(
            '{"error":"curated_rejected","code":"invalid_or_unavailable_curated_input"}',
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
