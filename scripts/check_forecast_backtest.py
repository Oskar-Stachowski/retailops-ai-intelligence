"""Offline planner/pooled-metric gate; optional complete temporal backtest and source replay."""

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from retailops_ai.forecasting.backtest import build_backtest, pool_metrics, verify_backtest
from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.evaluation import MetricAccumulator
from retailops_ai.forecasting.models import load_comparison

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
    parser.add_argument("--curated-dir", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/forecast-backtest.json")
    args = parser.parse_args()
    if (args.feature_dir is None) != (args.curated_dir is None):
        parser.error("feature-dir and curated-dir must be supplied together")
    started = time.monotonic()
    policy = BacktestPolicy()
    report: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="forecast-backtest-check-") as temporary:
        workspace = Path(temporary).resolve()
        if args.feature_dir is None:
            window = OriginWindow(start=date(2026, 5, 19), end=date(2026, 7, 17))
            plan = plan_backtest(window, policy)
            rolling = plan_backtest(window, BacktestPolicy(mode="rolling"))
            if len(plan.folds) != 3 or rolling.folds[0].train != plan.folds[0].train:
                raise ValueError("backtest_planner_component_failed")
            zero, positive = MetricAccumulator(), MetricAccumulator()
            zero.add(0, 100.0)
            positive.add(1000, 1000.0)
            result = pool_metrics([zero.result(), positive.result()])
            if result.mae != 50.0 or result.wape != 0.1:
                raise ValueError("backtest_pooled_denominator_failed")
            report = {
                "status": "passed",
                "scope": "chronological planner and pooled metric components only",
                "temporal_qualification": "not_ready",
                "policy": policy.model_dump(mode="json"),
                "resolved_split": plan.model_dump(mode="json"),
                "pooled_zero_example": result.model_dump(mode="json"),
            }
        else:
            feature_dir, curated_dir = args.feature_dir, args.curated_dir
            before = {"features": hashes(feature_dir), "curated": hashes(curated_dir)}
            output_root = args.output_root or workspace / "backtests"
            print("Backtest: build source split and train all folds", file=sys.stderr, flush=True)
            directory = build_backtest(feature_dir, curated_dir, output_root, policy)
            print(
                "Backtest: independent source rebuild and retraining/replay",
                file=sys.stderr,
                flush=True,
            )
            manifest = verify_backtest(directory, feature_dir, curated_dir)
            output_before = hashes(directory)
            print("Backtest: immutable rerun", file=sys.stderr, flush=True)
            if (
                build_backtest(feature_dir, curated_dir, output_root, policy) != directory
                or hashes(directory) != output_before
            ):
                raise ValueError("backtest_repeatability_failed")
            if before != {"features": hashes(feature_dir), "curated": hashes(curated_dir)}:
                raise ValueError("backtest_parent_mutation")
            if manifest.descriptor.status != "passed":
                raise ValueError("backtest_execution_not_ready")
            comparison = load_comparison(directory / "comparison")
            report = {
                "status": "passed",
                "scope": "AI 04.6 chronological development backtest, not model release",
                "backtest_id": manifest.backtest_id,
                "descriptor": manifest.descriptor.model_dump(mode="json"),
                "comparison_descriptor": comparison.descriptor.model_dump(mode="json"),
                "predictions": comparison.predictions.model_dump(mode="json"),
                "resources": {
                    name: receipt.model_dump(mode="json")
                    for name, receipt in comparison.resources.items()
                },
                "repeatable_and_immutable": True,
                "parents_unchanged": True,
                "independent_source_rebuild_and_retraining": True,
            }
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
