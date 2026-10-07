"""Indexed causal daily history and bounded offline feature preparation, without training or promotion."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout_history.bundle import build_history_bundle, verify_history_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            document, status = build_history_bundle(args.curated, args.output)
        else:
            document, status = verify_history_bundle(args.output, args.curated), "verified"
        print(
            json.dumps(
                {
                    "status": status,
                    "id": document["feature_bundle_id"],
                    "report": document["report"],
                }
            )
        )
        return 0
    except (SnapshotError, ValidationError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "failed", "error": "stockout_history_preparation_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
