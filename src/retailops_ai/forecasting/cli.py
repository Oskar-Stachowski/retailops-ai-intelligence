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
    args = parser.parse_args(argv)
    summary: dict[str, object]
    try:
        if args.command in {"features-build", "features-verify"}:
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
