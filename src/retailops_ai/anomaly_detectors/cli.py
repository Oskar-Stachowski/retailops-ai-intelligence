"""Build or independently replay a frozen development detector recipe."""

import argparse
import json
from pathlib import Path

from retailops_ai.anomaly_detectors.contract import FitPolicy
from retailops_ai.anomaly_detectors.protocol import Protocol
from retailops_ai.anomaly_detectors.store import build, verify
from retailops_ai.day_qualification.parents import read_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    for name in ("feature-dir", "replay-dir", "coverage-dir", "curated-dir", "import-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--generated-root", type=Path, default=Path("data/generated"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--protocol-file", type=Path)
    parser.add_argument("--policy-file", type=Path)
    args = parser.parse_args()
    inputs = (
        args.feature_dir,
        args.replay_dir,
        args.coverage_dir,
        args.curated_dir,
        args.import_dir,
    )
    if args.command == "build":
        if args.protocol_file is None:
            parser.error("build requires an explicit frozen protocol-file")
        protocol = Protocol.model_validate_json(
            read_artifact(args.protocol_file.parent, args.protocol_file.name, 1024**2)
        )
        policy = (
            FitPolicy()
            if args.policy_file is None
            else FitPolicy.model_validate_json(
                read_artifact(args.policy_file.parent, args.policy_file.name, 65536)
            )
        )
        result = build(*inputs, args.generated_root, protocol, policy)
    else:
        if args.directory is None or args.protocol_file is not None or args.policy_file is not None:
            parser.error("verify requires directory and uses its frozen protocol/policy")
        result = {
            "status": "verified",
            "manifest": verify(args.directory, *inputs).model_dump(mode="json"),
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
