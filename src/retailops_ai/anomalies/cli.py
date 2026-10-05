"""Offline inputs only: no model promotion, service, database or broker access."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.anomalies.contract import Policy
from retailops_ai.anomalies.store import build_inputs, verify_inputs
from retailops_ai.source_snapshot.files import SnapshotError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    build.add_argument("--scoring-delay-hours", type=int, default=24)
    verify = commands.add_parser("verify")
    verify.add_argument("--input-dir", type=Path, required=True)
    verify.add_argument("--curated-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_inputs(
                args.curated_dir,
                args.generated_root,
                Policy(scoring_delay_hours=args.scoring_delay_hours),
            )
            output = result.summary()
        else:
            manifest = verify_inputs(args.input_dir, args.curated_dir)
            output = {
                "status": "verified",
                "anomaly_input_id": manifest.anomaly_input_id,
                "row_count": manifest.descriptor.row_count,
            }
    except (SnapshotError, ValidationError, OSError):
        print(json.dumps({"status": "rejected", "reason": "anomaly_input_verification_failed"}))
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
