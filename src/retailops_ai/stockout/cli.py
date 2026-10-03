"""Explicit private offline label build/verification; no model promotion."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.dataset import build_labels, verify_labels, write_labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-evaluation-truth", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "build":
            document = build_labels(args.source, allow_evaluation_truth=args.allow_evaluation_truth)
            status = write_labels(document, args.output)
        else:
            document = verify_labels(
                args.output, args.source, allow_evaluation_truth=args.allow_evaluation_truth
            )
            status = "verified"
        print(
            json.dumps(
                {
                    "status": status,
                    "label_dataset_id": document["label_dataset_id"],
                    "source_dataset_id": document["descriptor"]["source_dataset_id"],
                    "snapshot_id": document["descriptor"]["snapshot_id"],
                    "report": document["report"],
                }
            )
        )
        return 0
    except (SnapshotError, ValidationError, OSError, ValueError):
        print(
            json.dumps(
                {"status": "failed", "reason": "stockout_label_build_or_verification_failed"}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
