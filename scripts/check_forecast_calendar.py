"""Offline AI 04.1 gate against the accepted source/import/curated fixture."""

import argparse
import hashlib
import json
import tempfile
import time
from datetime import date
from pathlib import Path

from retailops_ai.curated.builder import build_curated
from retailops_ai.forecasting.calendar import (
    build_calendar,
    load_calendar,
    publish_calendar,
    rows_for_origin,
)
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-calendar.json")
    args = parser.parse_args()
    source = ROOT / "data/fixtures/ai-smoke-v1/snapshot"
    before = hashes(source)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="forecast-calendar-check-") as temporary:
        workspace = Path(temporary).resolve()
        imported = import_snapshot(source, workspace / "data/generated")
        curated = build_curated(imported.directory, workspace / "data/generated")
        curated_before = hashes(curated.directory)
        window = OriginWindow(start=date(2026, 7, 16), end=date(2026, 7, 17))
        first = build_calendar(curated.directory, window)
        path = publish_calendar(first, workspace / "calendars")
        published = path.read_bytes()
        repeated = build_calendar(curated.directory, window)
        publish_calendar(repeated, workspace / "calendars")
        if (
            first.calendar_id != repeated.calendar_id
            or load_calendar(path) != first
            or path.read_bytes() != published
        ):
            raise ValueError("forecast_calendar_repeatability_failed")
        history_counts = []
        for origin in first.origins:
            history = list(rows_for_origin(curated.directory, first, origin.origin_date))
            if not history or any(
                r["curated_available_at"] > origin.availability_cutoff
                or r["business_date"] > origin.origin_date
                for r in history
            ):
                raise ValueError("forecast_calendar_history_boundary_failed")
            history_counts.append(len(history))
        origin = first.origins[0]
        plan_counts = []
        for target in (origin.targets[0], origin.targets[6], origin.targets[-1]):
            plans = list(
                rows_for_origin(
                    curated.directory,
                    first,
                    origin.origin_date,
                    table="price_plans",
                    target_date=target.target_date,
                )
            )
            if not plans or any(
                r["curated_available_at"] > origin.availability_cutoff for r in plans
            ):
                raise ValueError("forecast_calendar_plan_boundary_failed")
            plan_counts.append({"horizon_days": target.horizon_days, "known_plans": len(plans)})
        if hashes(source) != before or hashes(curated.directory) != curated_before:
            raise ValueError("forecast_calendar_changed_input")
        report = {
            "status": "passed",
            "scope": "AI 04.1 task/calendar/as-of; no model qualification",
            "task_id": first.descriptor.task_id,
            "calendar_id": first.calendar_id,
            "parent": first.descriptor.parent.model_dump(mode="json"),
            "origin_window": window.model_dump(mode="json"),
            "origins": len(first.origins),
            "daily_targets": len(first.origins) * 14,
            "history_rows_per_origin": history_counts,
            "known_price_plans": plan_counts,
            "repeatable_identity": True,
            "publication_bytes_unchanged": True,
            "inputs_unchanged": True,
            "cutoff_time_utc": first.descriptor.task.cutoff_time_utc,
            "forecast_model_status": first.forecast_model_status,
            "inventory_features_enabled": False,
            "aws_calls": 0,
            "seconds": round(time.monotonic() - start, 3),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "origins": report["origins"],
                "daily_targets": report["daily_targets"],
                "report": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
