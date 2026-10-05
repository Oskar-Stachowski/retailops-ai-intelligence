"""Build or verify a bounded return event-day view at an explicit UTC origin."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.return_inputs.contract import Policy
from retailops_ai.return_inputs.store import build_inputs, verify_inputs
from retailops_ai.source_snapshot.files import SnapshotError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    build.add_argument("--start-date", required=True)
    build.add_argument("--end-date", required=True)
    build.add_argument("--as-of-time", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--input-dir", type=Path, required=True)
    verify.add_argument("--curated-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            policy = Policy.model_validate_json(
                json.dumps(
                    {
                        "start_date": args.start_date,
                        "end_date": args.end_date,
                        "as_of_time": args.as_of_time,
                    }
                )
            )
            output = build_inputs(args.curated_dir, args.generated_root, policy).summary()
        else:
            manifest = verify_inputs(args.input_dir, args.curated_dir)
            output = {
                "status": "verified",
                "return_input_id": manifest.return_input_id,
                "row_count": manifest.descriptor.row_count,
                "detector_readiness": manifest.descriptor.detector_readiness,
            }
    except (SnapshotError, ValidationError, OSError):
        print(json.dumps({"status": "rejected", "reason": "return_input_verification_failed"}))
        return 2
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
