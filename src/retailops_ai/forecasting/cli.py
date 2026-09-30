"""Offline task-check, calendar build/verify; no cloud, model training or API mutation."""

import argparse
import json
import sqlite3
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
    inputs = commands.add_parser("inputs-build")
    inputs.add_argument("--curated-dir", type=Path, required=True)
    inputs.add_argument("--calendar", type=Path, required=True)
    inputs.add_argument("--output-root", type=Path, default=Path("data/generated/forecast-inputs"))
    inputs_verify = commands.add_parser("inputs-verify")
    inputs_verify.add_argument("--inputs-dir", type=Path, required=True)
    feature_build = commands.add_parser("features-build")
    feature_build.add_argument("--curated-dir", type=Path, required=True)
    feature_build.add_argument("--calendar", type=Path, required=True)
    feature_build.add_argument("--config", type=Path)
    feature_build.add_argument(
        "--output-root", type=Path, default=Path("data/generated/feature-sets")
    )
    feature_verify = commands.add_parser("features-verify")
    feature_verify.add_argument("--feature-dir", type=Path, required=True)
    split_build = commands.add_parser("split-build")
    split_build.add_argument("--curated-dir", type=Path, required=True)
    split_build.add_argument("--feature-dir", type=Path, required=True)
    split_build.add_argument("--config", type=Path)
    split_build.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-splits")
    )
    split_verify = commands.add_parser("split-verify")
    split_verify.add_argument("--split-dir", type=Path, required=True)
    split_verify.add_argument("--feature-dir", type=Path, required=True)
    fit = commands.add_parser("preprocessing-fit")
    fit.add_argument("--feature-dir", type=Path, required=True)
    fit.add_argument("--split-dir", type=Path, required=True)
    fit.add_argument("--fold", required=True)
    fit.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-preprocessing")
    )
    preprocess_verify = commands.add_parser("preprocessing-verify")
    preprocess_verify.add_argument("--preprocessing-dir", type=Path, required=True)
    preprocess_verify.add_argument("--feature-dir", type=Path, required=True)
    preprocess_verify.add_argument("--split-dir", type=Path, required=True)
    baseline_build = commands.add_parser("baselines-evaluate")
    baseline_build.add_argument("--feature-dir", type=Path, required=True)
    baseline_build.add_argument("--split-dir", type=Path, required=True)
    baseline_build.add_argument("--config", type=Path)
    baseline_build.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-evaluations")
    )
    baseline_verify = commands.add_parser("evaluation-verify")
    baseline_verify.add_argument("--evaluation-dir", type=Path, required=True)
    baseline_verify.add_argument("--feature-dir", type=Path, required=True)
    baseline_verify.add_argument("--split-dir", type=Path, required=True)
    models = commands.add_parser("models-evaluate")
    models.add_argument("--feature-dir", type=Path, required=True)
    models.add_argument("--split-dir", type=Path, required=True)
    models.add_argument("--config", type=Path)
    models.add_argument("--output-root", type=Path, default=Path("data/generated/forecast-models"))
    models_verify = commands.add_parser("models-verify")
    models_verify.add_argument("--comparison-dir", type=Path, required=True)
    models_verify.add_argument("--feature-dir", type=Path, required=True)
    models_verify.add_argument("--split-dir", type=Path, required=True)
    backtest = commands.add_parser("backtest-run")
    backtest.add_argument("--feature-dir", type=Path, required=True)
    backtest.add_argument("--curated-dir", type=Path, required=True)
    backtest.add_argument("--config", type=Path)
    backtest.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-backtests")
    )
    backtest_verify = commands.add_parser("backtest-verify")
    backtest_verify.add_argument("--backtest-dir", type=Path, required=True)
    backtest_verify.add_argument("--feature-dir", type=Path, required=True)
    backtest_verify.add_argument("--curated-dir", type=Path, required=True)
    quality = commands.add_parser("quality-evaluate")
    quality.add_argument("--feature-dir", type=Path, required=True)
    quality.add_argument("--backtest-dir", type=Path, required=True)
    quality.add_argument("--config", type=Path)
    quality.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-quality")
    )
    quality_verify = commands.add_parser("quality-verify")
    quality_verify.add_argument("--quality-dir", type=Path, required=True)
    quality_verify.add_argument("--feature-dir", type=Path, required=True)
    quality_verify.add_argument("--backtest-dir", type=Path, required=True)
    remediation = commands.add_parser("quality-remediate")
    remediation.add_argument("--feature-dir", type=Path, required=True)
    remediation.add_argument("--backtest-dir", type=Path, required=True)
    remediation.add_argument("--config", type=Path)
    remediation.add_argument(
        "--output-root", type=Path, default=Path("data/generated/forecast-remediation")
    )
    remediation_verify = commands.add_parser("remediation-verify")
    remediation_verify.add_argument("--remediation-dir", type=Path, required=True)
    remediation_verify.add_argument("--feature-dir", type=Path, required=True)
    remediation_verify.add_argument("--backtest-dir", type=Path, required=True)
    run_build = commands.add_parser("run-export")
    run_build.add_argument("--source-dir", type=Path, required=True)
    run_build.add_argument("--curated-dir", type=Path, required=True)
    run_build.add_argument("--feature-dir", type=Path, required=True)
    run_build.add_argument("--backtest-dir", type=Path, required=True)
    run_build.add_argument("--quality-dir", type=Path, required=True)
    run_build.add_argument("--ai-commit", required=True)
    run_build.add_argument("--output-root", type=Path, default=Path("data/generated/forecast-runs"))
    run_verify = commands.add_parser("run-verify")
    run_verify.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    summary: dict[str, object]
    try:
        if args.command in {"quality-remediate", "remediation-verify"}:
            from retailops_ai.forecasting.remediation import (
                build_remediation,
                load_remediation,
                verify_remediation,
            )
            from retailops_ai.forecasting.remediation_contract import RemediationPolicy

            if args.command == "quality-remediate":
                remediation_policy = RemediationPolicy()
                if args.config:
                    remediation_policy = RemediationPolicy.model_validate_json(
                        read_bytes(args.config.absolute().parent, args.config.name)
                    )
                directory = build_remediation(
                    args.feature_dir, args.backtest_dir, args.output_root, remediation_policy
                )
                remediation_manifest = load_remediation(directory)
            else:
                directory = args.remediation_dir
                remediation_manifest = verify_remediation(
                    directory, args.feature_dir, args.backtest_dir
                )
            summary = {
                "status": "passed",
                "quality_status": remediation_manifest.descriptor.quality_status,
                "gate_counts": remediation_manifest.descriptor.gate_counts,
                "remediation_id": remediation_manifest.remediation_id,
                "directory": str(directory),
                "forecast_model_status": remediation_manifest.forecast_model_status,
            }
            if remediation_manifest.descriptor.quality_status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"run-export", "run-verify"}:
            from retailops_ai.forecasting.run import build_run, verify_run

            directory = (
                build_run(
                    args.source_dir,
                    args.curated_dir,
                    args.feature_dir,
                    args.backtest_dir,
                    args.quality_dir,
                    args.output_root,
                    args.ai_commit,
                )
                if args.command == "run-export"
                else args.run_dir
            )
            run_manifest = verify_run(directory)
            summary = {
                "status": "passed",
                "run_id": run_manifest.run_id,
                "run_status": run_manifest.run_status,
                "quality_status": run_manifest.descriptor.quality_status,
                "gate_counts": run_manifest.descriptor.gate_counts,
                "forecast_model_status": run_manifest.forecast_model_status,
                "directory": str(directory),
            }
        elif args.command in {"quality-evaluate", "quality-verify"}:
            from retailops_ai.forecasting.quality import build_quality, load_quality, verify_quality
            from retailops_ai.forecasting.quality_contract import QualityPolicy

            quality_policy = QualityPolicy()
            if args.command == "quality-evaluate":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    quality_policy = QualityPolicy.model_validate_json(raw)
                directory = build_quality(
                    args.feature_dir, args.backtest_dir, args.output_root, quality_policy
                )
                quality_manifest = load_quality(directory)
            else:
                directory = args.quality_dir
                quality_manifest = verify_quality(directory, args.feature_dir, args.backtest_dir)
            summary = {
                "status": "passed",
                "quality_status": quality_manifest.descriptor.quality_status,
                "quality_id": quality_manifest.quality_id,
                "backtest_id": quality_manifest.descriptor.backtest_id,
                "gate_counts": quality_manifest.descriptor.gate_counts,
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
            if quality_manifest.descriptor.quality_status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"backtest-run", "backtest-verify"}:
            from retailops_ai.forecasting.backtest import (
                build_backtest,
                load_backtest,
                verify_backtest,
            )
            from retailops_ai.forecasting.backtest_contract import BacktestPolicy

            backtest_policy = BacktestPolicy()
            if args.command == "backtest-run":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    backtest_policy = BacktestPolicy.model_validate_json(raw)
                directory = build_backtest(
                    args.feature_dir, args.curated_dir, args.output_root, backtest_policy
                )
                backtest_manifest = load_backtest(directory)
            else:
                directory = args.backtest_dir
                backtest_manifest = verify_backtest(directory, args.feature_dir, args.curated_dir)
            summary = {
                "status": backtest_manifest.descriptor.status,
                "backtest_id": backtest_manifest.backtest_id,
                "split_id": backtest_manifest.descriptor.split_id,
                "comparison_id": backtest_manifest.descriptor.comparison_id,
                "folds": len(backtest_manifest.descriptor.folds),
                "pooled_metrics": {
                    name: metric.model_dump(mode="json")
                    for name, metric in backtest_manifest.descriptor.pooled_metrics.items()
                },
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
            if backtest_manifest.descriptor.status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"models-evaluate", "models-verify"}:
            from retailops_ai.forecasting.model_contract import ModelPolicy
            from retailops_ai.forecasting.models import (
                build_comparison,
                load_comparison,
                verify_comparison,
            )

            model_policy = ModelPolicy()
            if args.command == "models-evaluate":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    model_policy = ModelPolicy.model_validate_json(raw)
                directory = build_comparison(
                    args.feature_dir, args.split_dir, args.output_root, model_policy
                )
                comparison = load_comparison(directory)
            else:
                directory = args.comparison_dir
                comparison = verify_comparison(directory, args.feature_dir, args.split_dir)
            summary = {
                "status": comparison.descriptor.status,
                "comparison_id": comparison.comparison_id,
                "models": comparison.descriptor.models,
                "prediction_rows": comparison.predictions.row_count,
                "selections": [
                    value.model_dump(mode="json") for value in comparison.descriptor.selections
                ],
                "resources": {
                    name: receipt.model_dump(mode="json")
                    for name, receipt in comparison.resources.items()
                },
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
            if comparison.descriptor.status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"baselines-evaluate", "evaluation-verify"}:
            from retailops_ai.forecasting.evaluation import (
                build_evaluation,
                load_evaluation,
                verify_evaluation,
            )
            from retailops_ai.forecasting.evaluation_contract import BaselinePolicy

            baseline_policy = BaselinePolicy()
            if args.command == "baselines-evaluate":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    baseline_policy = BaselinePolicy.model_validate_json(raw)
                directory = build_evaluation(
                    args.feature_dir, args.split_dir, args.output_root, baseline_policy
                )
                evaluation = load_evaluation(directory)
            else:
                directory = args.evaluation_dir
                evaluation = verify_evaluation(directory, args.feature_dir, args.split_dir)
            summary = {
                "status": evaluation.descriptor.status,
                "evaluation_id": evaluation.evaluation_id,
                "feature_set_id": evaluation.descriptor.feature_set_id,
                "split_id": evaluation.descriptor.split_id,
                "prediction_rows": evaluation.predictions.row_count,
                "selections": [
                    selection.model_dump(mode="json")
                    for selection in evaluation.descriptor.selections
                ],
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
            if evaluation.descriptor.status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"features-build", "features-verify"}:
            from retailops_ai.forecasting.manifest_contract import FeaturePolicy
            from retailops_ai.forecasting.manifests import build_feature_set, verify_feature_set

            policy = FeaturePolicy()
            if args.command == "features-build":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    policy = FeaturePolicy.model_validate_json(raw)
                directory = build_feature_set(
                    args.curated_dir, load_calendar(args.calendar), args.output_root, policy
                )
            else:
                directory = args.feature_dir
            feature_manifest = verify_feature_set(directory)
            summary = {
                "status": "passed",
                "feature_set_id": feature_manifest.feature_set_id,
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
        elif args.command in {"split-build", "split-verify"}:
            from retailops_ai.forecasting.manifest_contract import SplitPolicy
            from retailops_ai.forecasting.splits import build_split, verify_split

            split_policy = None
            if args.command == "split-build":
                if args.config is not None:
                    raw = read_bytes(args.config.absolute().parent, args.config.name)
                    decode_json(raw)
                    split_policy = SplitPolicy.model_validate_json(raw)
                directory = build_split(
                    args.feature_dir, args.curated_dir, args.output_root, split_policy
                )
            else:
                directory = args.split_dir
            split_manifest = verify_split(directory, args.feature_dir)
            summary = {
                "status": split_manifest.descriptor.qualification_status,
                "split_id": split_manifest.split_id,
                "feature_set_id": split_manifest.descriptor.feature_set_id,
                "label_dataset_id": split_manifest.descriptor.label_dataset_id,
                "directory": str(directory),
                "counts": split_manifest.descriptor.counts,
                "forecast_model_status": "not_ready",
            }
            if split_manifest.descriptor.qualification_status != "passed":
                print(json.dumps(summary, sort_keys=True))
                return 3
        elif args.command in {"preprocessing-fit", "preprocessing-verify"}:
            from retailops_ai.forecasting.preprocessing import (
                fit_fold,
                load_preprocessing,
                verify_preprocessing,
            )

            directory = (
                fit_fold(args.feature_dir, args.split_dir, args.fold, args.output_root)
                if args.command == "preprocessing-fit"
                else args.preprocessing_dir
            )
            state = (
                load_preprocessing(directory)
                if args.command == "preprocessing-fit"
                else verify_preprocessing(directory, args.feature_dir, args.split_dir)
            )
            summary = {
                "status": "passed",
                "preprocessing_id": state.preprocessing_id,
                "feature_set_id": state.descriptor.feature_set_id,
                "split_id": state.descriptor.split_id,
                "fold": state.descriptor.fold.name,
                "train_rows": state.descriptor.train_rows,
                "directory": str(directory),
                "forecast_model_status": "not_ready",
            }
        elif args.command in {"inputs-build", "inputs-verify"}:
            from retailops_ai.forecasting.features_store import build_inputs, verify_inputs

            directory = (
                build_inputs(args.curated_dir, load_calendar(args.calendar), args.output_root)
                if args.command == "inputs-build"
                else args.inputs_dir
            )
            document = verify_inputs(directory)
            summary = {
                "status": "passed",
                "inputs_id": document["inputs_id"],
                "directory": str(directory),
                "stats": document["descriptor"]["stats"],
                "forecast_model_status": document["forecast_model_status"],
            }
        elif args.command == "calendar-verify":
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
        if args.command in {
            "models-evaluate",
            "models-verify",
            "backtest-run",
            "backtest-verify",
            "quality-evaluate",
            "quality-verify",
            "run-export",
            "run-verify",
        }:
            print(
                '{"error":"forecast_dependencies_required","install":"uv sync --locked --extra snapshot --extra forecast"}',
                file=sys.stderr,
            )
            return 2
        print(
            '{"error":"snapshot_dependencies_required","install":"uv sync --locked --extra snapshot"}',
            file=sys.stderr,
        )
        return 2
    except (SnapshotError, OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.Error):
        print(
            '{"error":"forecast_rejected","code":"invalid_policy_calendar_or_curated_input"}',
            file=sys.stderr,
        )
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
