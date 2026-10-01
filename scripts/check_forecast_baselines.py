"""Offline baseline gate; real temporal mode evaluates and replays the shared evaluator."""

import argparse
import hashlib
import json
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from retailops_ai.curated.builder import build_curated
from retailops_ai.forecasting.baselines import predict
from retailops_ai.forecasting.calendar import build_calendar
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.evaluation import (
    MetricAccumulator,
    build_evaluation,
    verify_evaluation,
)
from retailops_ai.forecasting.evaluation_contract import BaselinePolicy
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.manifests import build_feature_set, input_models, verify_feature_set
from retailops_ai.forecasting.splits import build_split
from retailops_ai.source_snapshot.files import SnapshotError, read_json
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]


def hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feature-dir", type=Path)
    parser.add_argument("--split-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-baselines.json")
    args = parser.parse_args()
    if (args.feature_dir is None) != (args.split_dir is None):
        parser.error("feature-dir and split-dir must be supplied together")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="forecast-baselines-check-") as temporary:
        workspace = Path(temporary).resolve()
        if args.feature_dir is None:
            imported = import_snapshot(
                ROOT / "data/fixtures/ai-smoke-v1/snapshot", workspace / "data/generated"
            )
            curated = build_curated(imported.directory, workspace / "data/generated").directory
            parameters = read_json(curated, "curated_manifest.json")["descriptor"][
                "source_parameters"
            ]
            start = date.fromisoformat(parameters["end_date"]) - timedelta(days=15)
            calendar = build_calendar(
                curated, OriginWindow(start=start, end=start + timedelta(days=1))
            )
            feature_dir = build_feature_set(curated, calendar, workspace / "features")
            features = verify_feature_set(feature_dir)
            contexts = {
                context.content_sha256(): context
                for context in input_models(feature_dir, "history")
                if isinstance(context, HistoryContext)
            }
            count = 0
            for row in input_models(feature_dir, "features"):
                if not isinstance(row, InputRow):
                    raise ValueError("baseline_gate_input_schema")
                for model in BaselinePolicy().candidates:
                    estimate = predict(
                        model, row, contexts[row.history_context_sha256], BaselinePolicy()
                    )
                    if any(day > row.forecast_origin.date() for day in estimate.history_dates):
                        raise ValueError("baseline_gate_future_history")
                    count += 1
            try:
                build_split(feature_dir, curated, workspace / "splits")
            except SnapshotError as exc:
                if str(exc) != "forecast_split_requires_at_least_51_origins":
                    raise
            else:
                raise ValueError("baseline_short_smoke_must_not_qualify")
            report = {
                "status": "passed",
                "scope": "AI 04.4 short-source contract gate, not temporal model qualification",
                "feature_set_id": features.feature_set_id,
                "candidate_predictions_checked": count,
                "split_status": "not_ready",
                "split_reason": "insufficient_origin_window",
                "selection": None,
            }
        else:
            feature_dir, split_dir = args.feature_dir, args.split_dir
            before = {"features": hashes(feature_dir), "split": hashes(split_dir)}
            output_root = args.output_root or workspace / "evaluations"
            directory = build_evaluation(feature_dir, split_dir, output_root)
            manifest = verify_evaluation(directory, feature_dir, split_dir)
            output_before = hashes(directory)
            if (
                build_evaluation(feature_dir, split_dir, output_root) != directory
                or hashes(directory) != output_before
            ):
                raise ValueError("baseline_evaluation_repeatability_failed")
            if before != {"features": hashes(feature_dir), "split": hashes(split_dir)}:
                raise ValueError("baseline_input_mutation")
            if manifest.descriptor.status != "passed":
                raise ValueError("baseline_temporal_evaluation_not_ready")
            report = {
                "status": "passed",
                "scope": "AI 04.4 temporal baseline execution, not model release or quality threshold",
                "evaluation_id": manifest.evaluation_id,
                "descriptor": manifest.descriptor.model_dump(mode="json"),
                "predictions": manifest.predictions.model_dump(mode="json"),
                "repeatable_and_immutable": True,
                "parents_unchanged": True,
            }
            if args.output_root is not None:
                report["evaluation_directory"] = str(directory)
        metric = MetricAccumulator()
        metric.add(0, 100.0)
        result = metric.result()
        if result.mae != 100.0 or result.wape is not None:
            raise ValueError("baseline_zero_denominator_failed")
        report.update(
            seconds=round(time.monotonic() - started, 3),
            aws_calls=0,
            forecast_model_status="not_ready",
            portfolio_final_test="not_included_not_opened",
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "passed", "report": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
