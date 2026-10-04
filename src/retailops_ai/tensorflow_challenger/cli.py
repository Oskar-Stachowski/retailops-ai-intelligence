"""Explicit development-only CPU training from existing verified feature/split artifacts."""

import argparse
import importlib
import json
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy
from retailops_ai.tensorflow_challenger.evaluation import compare_development
from retailops_ai.tensorflow_challenger.parents import development_parents
from retailops_ai.tensorflow_challenger.pipeline import (
    LoadedChallenger,
    fit_challenger,
    verify_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train-development")
    train.add_argument("--features", type=Path, required=True)
    train.add_argument("--split", type=Path, required=True)
    train.add_argument("--fold", required=True)
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--policy", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            manifest, _ = verify_artifact(args.artifact)
        else:
            policy = (
                ChallengerPolicy.model_validate_json(args.policy.read_bytes())
                if args.policy
                else ChallengerPolicy()
            )
            with development_parents(
                args.features, args.split, args.fold, policy.max_windows
            ) as parents:
                feature, split, fold, train_windows, validation_windows = parents
                if feature.descriptor.source_parameters.get("seed") != policy.data_seed:
                    raise SnapshotError("tensorflow_data_seed_parent_mismatch")
                manifest = fit_challenger(
                    train_windows,
                    validation_windows,
                    fold=fold,
                    feature_set_id=feature.feature_set_id,
                    split_id=split.split_id,
                    output=args.output,
                    policy=policy,
                )
                predictions = LoadedChallenger(args.output).predict(validation_windows)
                evaluation = compare_development(validation_windows, predictions)
                evaluation["model_id"] = manifest["model_id"]
                receipt = args.output / "evaluation.json"
                with receipt.open("xb") as stream:
                    stream.write(canonical_bytes(evaluation))
                client = importlib.import_module("mlflow.tracking").MlflowClient(
                    tracking_uri=(args.output.resolve() / "tracking").as_uri()
                )
                client.log_artifact(
                    manifest["worker"]["mlflow_run_id"], str(receipt), artifact_path="evaluation"
                )
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "model_id": manifest["model_id"],
                    "deployment_status": "not_ready",
                    "interval_status": "not_ready",
                    "final_test_accessed": False,
                    "promotion_allowed": False,
                }
            )
        )
        return 0
    except (OSError, ValueError, SnapshotError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": str(exc)
                    if isinstance(exc, SnapshotError)
                    else "tensorflow_development_command_failed",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
