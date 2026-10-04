"""Build or verify isolated receipt-qualified anomaly feature inputs."""

import argparse
import json
from pathlib import Path

from retailops_ai.qualified_anomalies.contract import Policy
from retailops_ai.qualified_anomalies.store import build, verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    for name in ("replay-dir", "coverage-dir", "curated-dir", "import-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--sales-delay-hours", type=int, default=24)
    parser.add_argument("--returns-delay-hours", type=int, default=72)
    args = parser.parse_args()
    inputs = (args.replay_dir, args.coverage_dir, args.curated_dir, args.import_dir)
    if args.command == "build":
        result = build(
            *inputs,
            args.generated_root,
            Policy(
                sales_delay_hours=args.sales_delay_hours,
                returns_delay_hours=args.returns_delay_hours,
            ),
        )
    else:
        if args.directory is None:
            parser.error("verify requires --directory")
        if args.sales_delay_hours != 24 or args.returns_delay_hours != 72:
            parser.error("verify uses the artifact policy")
        result = {
            "status": "verified",
            "manifest": verify(args.directory, *inputs).model_dump(mode="json"),
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
