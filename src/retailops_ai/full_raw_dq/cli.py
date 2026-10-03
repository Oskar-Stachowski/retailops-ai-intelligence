"""Build or verify a local full-parent DQ artifact; no external mutations."""

import argparse
import json
from pathlib import Path

from retailops_ai.full_raw_dq.store import build_replay, verify_replay


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--capture-dir", type=Path, required=True)
    build.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    verify = sub.add_parser("verify")
    verify.add_argument("--replay-dir", type=Path, required=True)
    verify.add_argument("--curated-dir", type=Path, required=True)
    build.add_argument("--import-dir", type=Path, required=True)
    verify.add_argument("--import-dir", type=Path, required=True)
    args = parser.parse_args()
    result = (
        build_replay(args.capture_dir, args.curated_dir, args.import_dir, args.generated_root)
        if args.command == "build"
        else {
            "status": "verified",
            "manifest": verify_replay(
                args.replay_dir, args.curated_dir, args.import_dir
            ).model_dump(mode="json"),
        }
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
