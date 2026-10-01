"""Repeatable, offline AI 04.2 source/curated/panel/features gate; optional temporal input."""

import argparse
import hashlib
import json
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from retailops_ai.curated.builder import build_curated
from retailops_ai.forecasting.calendar import build_calendar
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.features_contract import FEATURE_TYPES
from retailops_ai.forecasting.features_store import build_inputs, verify_inputs
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
    parser.add_argument(
        "--snapshot-dir", type=Path, default=ROOT / "data/fixtures/ai-smoke-v1/snapshot"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-features.json")
    parser.add_argument("--all-temporal-origins", action="store_true")
    args = parser.parse_args()
    source_before = hashes(args.snapshot_dir)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="forecast-features-check-") as temporary:
        workspace = Path(temporary).resolve()
        imported = import_snapshot(args.snapshot_dir, workspace / "data/generated")
        curated = build_curated(imported.directory, workspace / "data/generated")
        curated_before = hashes(curated.directory)
        parameters = curated.manifest["descriptor"]["source_parameters"]
        if args.all_temporal_origins:
            if parameters["profile"] != "ai-temporal-smoke":
                raise ValueError("full_temporal_gate_requires_standard_temporal_profile")
            origin_from = date.fromisoformat(parameters["start_date"]) + timedelta(
                days=parameters["warmup_days"]
            )
            origin_to = origin_from + timedelta(days=parameters["origin_days"] - 1)
        else:
            origin_from = date.fromisoformat(parameters["end_date"]) - timedelta(days=15)
            origin_to = origin_from + timedelta(days=1)
        calendar = build_calendar(curated.directory, OriginWindow(start=origin_from, end=origin_to))
        first = build_inputs(curated.directory, calendar, workspace / "inputs")
        before = hashes(first)
        manifest = verify_inputs(first)
        repeated = build_inputs(curated.directory, calendar, workspace / "inputs")
        if (
            first != repeated
            or hashes(first) != before
            or hashes(args.snapshot_dir) != source_before
            or hashes(curated.directory) != curated_before
        ):
            raise ValueError("forecast_features_immutability_gate_failed")
        report = {
            "status": "passed",
            "scope": "AI 04.2 draft panel/features; no split, preprocessing or model qualification",
            "profile": parameters["profile"],
            "source_dataset_id": imported.snapshot.source_id,
            "snapshot_id": imported.snapshot.snapshot_id,
            "curated_dataset_id": curated.manifest["curated_dataset_id"],
            "calendar_id": calendar.calendar_id,
            "inputs_id": manifest["inputs_id"],
            "origin_window": calendar.descriptor.origin_window.model_dump(mode="json"),
            "origins": len(calendar.origins),
            "feature_columns": len(FEATURE_TYPES),
            "policy": manifest["descriptor"]["policy"],
            "stats": manifest["descriptor"]["stats"],
            "tables": manifest["descriptor"]["tables"],
            "implementation": manifest["descriptor"]["implementation"],
            "input_files_unchanged": True,
            "repeatable_identity": True,
            "published_bytes_unchanged": True,
            "aws_calls": 0,
            "forecast_model_status": "not_ready",
            "seconds": round(time.monotonic() - start, 3),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": "passed",
                "profile": report["profile"],
                "origins": report["origins"],
                "stats": report["stats"],
                "report": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
