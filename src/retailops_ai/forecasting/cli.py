"""Offline task-check, calendar build/verify; no cloud, model training or API mutation."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from retailops_ai.forecasting.calendar import (
    build_calendar,
    default_task,
    load_calendar,
    publish_calendar,
    verified_parent,
)
from retailops_ai.forecasting.contract import OriginWindow, TaskConfig
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="retailops-ai-forecast")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("task-check")
    check.add_argument("--config", type=Path)
    build = commands.add_parser("calendar-build")
    build.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--origin-from", required=True)
    build.add_argument("--origin-to", required=True)
    build.add_argument("--config", type=Path)
    build.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-calendars")
    )
    verify = commands.add_parser("calendar-verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--curated-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    summary: dict[str, object]
    try:
        if args.command == "calendar-verify":
            manifest = load_calendar(args.manifest)
            if verified_parent(args.curated_dir) != manifest.descriptor.parent:
                raise SnapshotError("forecast_calendar_parent_mismatch")
            summary = {"status": "verified", "calendar_id": manifest.calendar_id}
        else:
            task = default_task()
            if args.config is not None:
                raw = read_bytes(args.config.absolute().parent, args.config.name)
                decode_json(raw)
                task = TaskConfig.model_validate_json(raw)
            if args.command == "task-check":
                summary = {
                    "status": "passed",
                    "task_id": task.task_id(),
                    "task": task.model_dump(mode="json"),
                }
            else:
                manifest = build_calendar(
                    args.curated_dir,
                    OriginWindow(
                        start=date.fromisoformat(args.origin_from),
                        end=date.fromisoformat(args.origin_to),
                    ),
                    task=task,
                )
                path = publish_calendar(manifest, args.output_root)
                summary = {
                    "status": "passed",
                    "calendar_id": manifest.calendar_id,
                    "manifest": str(path),
                    "origins": len(manifest.origins),
                    "daily_targets": len(manifest.origins) * 14,
                    "forecast_model_status": manifest.forecast_model_status,
                }
    except ImportError:
        print(
            '{"error":"snapshot_dependencies_required","install":"uv sync --locked --extra snapshot"}',
            file=sys.stderr,
        )
        return 2
    except (SnapshotError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print(
            '{"error":"forecast_rejected","code":"invalid_policy_calendar_or_curated_input"}',
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
