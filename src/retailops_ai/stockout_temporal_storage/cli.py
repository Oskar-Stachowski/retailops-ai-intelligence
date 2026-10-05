"""Offline comparison/split partitions and development models from sealed parents."""

import argparse
import json
import sqlite3
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError, read_json
from retailops_ai.stockout.split import SplitPolicy
from retailops_ai.stockout_temporal_storage.bundle import (
    assemble_partitioned_development,
    build_temporal_bundle,
    verify_temporal_bundle,
)
from retailops_ai.stockout_temporal_storage.store import PartitionInputs
from retailops_ai.stockout_training.development import build_development


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify", "train"))
    for name in ("curated", "private", "features", "upstream", "labels", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--split-policy", type=Path)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    inputs = PartitionInputs(args.curated, args.private, args.features, args.upstream, args.labels)
    try:
        if args.action == "build":
            if args.split_policy is None:
                raise SnapshotError("stockout_temporal_split_policy_required")
            policy = SplitPolicy.model_validate(
                read_json(args.split_policy.parent, args.split_policy.name)
            )
            document, status = build_temporal_bundle(
                inputs,
                args.output,
                split_policy=policy,
                allow_evaluation_truth=args.allow_evaluation_truth,
            )
        elif args.action == "verify":
            document, status = (
                verify_temporal_bundle(
                    args.output, inputs, allow_evaluation_truth=args.allow_evaluation_truth
                ),
                "verified",
            )
        else:
            # JSON is an offline development result, not a registry write or promotion.
            document = build_development(
                assemble_partitioned_development(
                    args.output, inputs, allow_evaluation_truth=args.allow_evaluation_truth
                )
            )
            print(json.dumps(document))
            return 0
        print(
            json.dumps(
                dict(
                    status=status,
                    temporal_bundle_id=document["temporal_bundle_id"],
                    report=document["report"],
                )
            )
        )
        return 0
    except (
        SnapshotError,
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
    ):
        print(
            json.dumps(
                dict(status="failed", error="stockout_temporal_build_or_verification_failed")
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
