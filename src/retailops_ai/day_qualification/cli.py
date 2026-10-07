"""Build or verify an isolated, causal day qualification artifact."""

import argparse
import json
from pathlib import Path

from retailops_ai.day_qualification.contract import Policy
from retailops_ai.day_qualification.store import build, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    for name in ("replay-dir", "coverage-dir", "curated-dir", "import-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    inputs = (args.replay_dir, args.coverage_dir, args.curated_dir, args.import_dir)
    if args.command == "build":
        result = build(*inputs, args.generated_root, Policy(as_of=args.as_of))
    else:
        if args.directory is None:
            parser.error("verify requires --directory")
        if args.as_of is not None:
            parser.error("verify uses the artifact policy")
        result = {
            "status": "verified",
            "manifest": verify(args.directory, *inputs).model_dump(mode="json"),
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
