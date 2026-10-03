"""Offline feature/split preparation; final test metrics are a separate campaign."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError, read_json
from retailops_ai.stockout.artifacts import write_artifact
from retailops_ai.stockout.dataset import MAX_LABEL_BYTES, verify_labels
from retailops_ai.stockout.feature_dataset import MAX_FEATURE_BYTES, build_features, verify_features
from retailops_ai.stockout.split import SplitPolicy, build_split


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("features-build", "features-verify", "split-build", "split-verify")
    )
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--features", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "features-build":
            document = build_features(args.curated)
            status = write_artifact(document, args.output, max_bytes=MAX_FEATURE_BYTES)
        elif args.action == "features-verify":
            document = verify_features(args.output, args.curated)
            status = "verified"
        else:
            if not all((args.features, args.labels, args.source, args.policy)):
                raise ValueError("split_requires_fully_pinned_features_labels_source_policy")
            features = verify_features(args.features, args.curated)
            labels = verify_labels(
                args.labels, args.source, allow_evaluation_truth=args.allow_evaluation_truth
            )
            policy = SplitPolicy.model_validate(read_json(args.policy.parent, args.policy.name))
            document = build_split(features, labels, policy)
            if args.action == "split-build":
                status = write_artifact(document, args.output, max_bytes=MAX_LABEL_BYTES)
            else:
                if read_json(args.output.parent, args.output.name) != document:
                    raise ValueError("stockout_split_full_replay_mismatch")
                status = "verified"
        print(
            json.dumps(
                dict(
                    status=status,
                    report=document["report"],
                    id=document.get("feature_dataset_id", document.get("split_id")),
                )
            )
        )
        return 0
    except (SnapshotError, ValidationError, OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(dict(status="failed", reason="stockout_preparation_or_verification_failed"))
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
