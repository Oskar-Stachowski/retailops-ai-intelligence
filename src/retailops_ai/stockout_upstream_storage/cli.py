"""Offline bounded historical upstream preparation from public facts and feature bundle 2.2."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout_upstream_storage.bundle import (
    build_upstream_bundle,
    verify_upstream_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "build":
            document, status = build_upstream_bundle(args.curated, args.features, args.output)
        else:
            document, status = (
                verify_upstream_bundle(args.output, args.curated, args.features),
                "verified",
            )
        print(
            json.dumps(
                dict(
                    status=status,
                    upstream_bundle_id=document["upstream_bundle_id"],
                    report=document["report"],
                )
            )
        )
        return 0
    except (SnapshotError, ValidationError, OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                dict(
                    status="failed",
                    error="stockout_upstream_partition_build_or_verification_failed",
                )
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
