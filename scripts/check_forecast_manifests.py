"""Offline formal-feature gate; temporal mode also qualifies split and fits train preprocessing."""

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
from retailops_ai.forecasting.manifests import build_feature_set, verify_feature_set
from retailops_ai.forecasting.preprocessing import fit_fold, load_preprocessing
from retailops_ai.forecasting.splits import build_split, verify_split
from retailops_ai.source_snapshot.files import SnapshotError, read_json
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
    parser.add_argument("--curated-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-manifests.json")
    args = parser.parse_args()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="forecast-manifests-check-") as temporary:
        workspace = Path(temporary).resolve()
        curated_dir = args.curated_dir
        if curated_dir is None:
            imported = import_snapshot(
                ROOT / "data/fixtures/ai-smoke-v1/snapshot", workspace / "data/generated"
            )
            curated_dir = build_curated(imported.directory, workspace / "data/generated").directory
        source_before = hashes(curated_dir)
        parameters = read_json(curated_dir, "curated_manifest.json")["descriptor"][
            "source_parameters"
        ]
        temporal = parameters["profile"] == "ai-temporal-smoke"
        start = (
            date.fromisoformat(parameters["start_date"]) + timedelta(days=parameters["warmup_days"])
            if temporal
            else date.fromisoformat(parameters["end_date"]) - timedelta(days=15)
        )
        end = start + timedelta(days=parameters["origin_days"] - 1 if temporal else 1)
        calendar = build_calendar(curated_dir, OriginWindow(start=start, end=end))
        feature_dir = build_feature_set(curated_dir, calendar, workspace / "features")
        features = verify_feature_set(feature_dir)
        feature_before = hashes(feature_dir)
        if (
            build_feature_set(curated_dir, calendar, workspace / "features") != feature_dir
            or hashes(feature_dir) != feature_before
        ):
            raise ValueError("formal_feature_identity_or_immutability_failed")
        report = {
            "status": "passed",
            "profile": parameters["profile"],
            "scope": "AI 04.3 contracts and development qualification, no model quality or portfolio final test",
            "feature_set_id": features.feature_set_id,
            "inputs_id": features.descriptor.inputs_id,
            "source_dataset_id": features.descriptor.parent.source_dataset_id,
            "curated_dataset_id": features.descriptor.parent.curated_dataset_id,
            "calendar_id": calendar.calendar_id,
            "origin_window": calendar.descriptor.origin_window.model_dump(mode="json"),
            "origins": len(calendar.origins),
            "rows": features.descriptor.row_count,
            "feature_policy": features.descriptor.resolved_policy.model_dump(mode="json"),
            "code": features.descriptor.code.model_dump(mode="json"),
            "feature_repeatable_and_immutable": True,
            "aws_calls": 0,
            "forecast_model_status": "not_ready",
            "portfolio_final_test": "not_included_not_opened",
        }
        if temporal:
            split_dir = build_split(feature_dir, curated_dir, workspace / "splits")
            split = verify_split(split_dir, feature_dir)
            before = hashes(split_dir)
            if (
                build_split(feature_dir, curated_dir, workspace / "splits") != split_dir
                or hashes(split_dir) != before
            ):
                raise ValueError("forecast_split_identity_or_immutability_failed")
            if split.descriptor.qualification_status != "passed":
                raise ValueError("temporal_split_qualification_not_ready")
            preprocessing_dir = fit_fold(
                feature_dir,
                split_dir,
                split.descriptor.resolved_policy.folds[0].name,
                workspace / "preprocessing",
            )
            fitted = load_preprocessing(preprocessing_dir)
            before = hashes(preprocessing_dir)
            if (
                fit_fold(
                    feature_dir, split_dir, fitted.descriptor.fold.name, workspace / "preprocessing"
                )
                != preprocessing_dir
                or hashes(preprocessing_dir) != before
            ):
                raise ValueError("forecast_preprocessing_identity_or_immutability_failed")
            report.update(
                split_id=split.split_id,
                label_dataset_id=split.descriptor.label_dataset_id,
                split_status=split.descriptor.qualification_status,
                split_policy=split.descriptor.resolved_policy.model_dump(mode="json"),
                split_counts=split.descriptor.counts,
                split_repeatable_and_immutable=True,
                preprocessing_id=fitted.preprocessing_id,
                train_rows=fitted.descriptor.train_rows,
                transformed_columns=len(fitted.descriptor.output_columns),
                preprocessing_repeatable_and_immutable=True,
                train_content_sha256=fitted.descriptor.train_content_sha256,
            )
        else:
            try:
                build_split(feature_dir, curated_dir, workspace / "splits")
            except SnapshotError as exc:
                if str(exc) != "forecast_split_requires_at_least_51_origins":
                    raise
            else:
                raise ValueError("short_smoke_must_not_qualify_temporal_split")
            report.update(
                split_status="not_ready",
                split_reason="insufficient_origin_window",
                preprocessing_status="not_performed",
            )
        if hashes(curated_dir) != source_before or hashes(feature_dir) != feature_before:
            raise ValueError("forecast_manifest_input_mutation")
        report["seconds"] = round(time.monotonic() - started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "profile": report["profile"],
                "split_status": report["split_status"],
                "rows": report["rows"],
                "report": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
