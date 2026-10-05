"""Explicit private offline label partitions; no training or model promotion."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout_label_partitions.bundle import build_label_bundle, verify_label_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "build":
            document, status = build_label_bundle(
                args.source, args.output, allow_evaluation_truth=args.allow_evaluation_truth
            )
        else:
            document, status = (
                verify_label_bundle(
                    args.output, args.source, allow_evaluation_truth=args.allow_evaluation_truth
                ),
                "verified",
            )
        print(
            json.dumps(
                dict(
                    status=status,
                    label_bundle_id=document["label_bundle_id"],
                    report=document["report"],
                )
            )
        )
        return 0
    except (SnapshotError, ValidationError, OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                dict(status="failed", error="stockout_label_partition_build_or_verification_failed")
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
