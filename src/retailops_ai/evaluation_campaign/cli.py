"""Prepare/check an offline AI 09 plan; preflight is explicitly not ready."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.contract import EvaluationPreparation, Repository
from retailops_ai.evaluation_campaign.preparation import (
    default_plan,
    prepare,
    readiness,
    verify_preparation,
)
from retailops_ai.source_snapshot.files import decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("template")
    for command in ("prepare", "verify", "preflight"):
        child = commands.add_parser(command)
        child.add_argument("--retailops-repo", required=True, type=Path)
        child.add_argument("--ai-repo", required=True, type=Path)
        if command == "prepare":
            child.add_argument("--plan", type=Path)
            child.add_argument("--output-root", required=True, type=Path)
        else:
            child.add_argument("--preparation", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "template":
            print(json.dumps(default_plan().model_dump(mode="json"), indent=2, sort_keys=True))
            return 0
        repositories: dict[Repository, Path] = {
            "retailops-cloud-native-platform": args.retailops_repo,
            "retailops-ai-intelligence": args.ai_repo,
        }
        if args.command == "prepare":
            plan = default_plan()
            if args.plan is not None:
                plan = EvaluationPreparation.model_validate_json(
                    canonical_bytes(decode_json(read_bytes(args.plan.parent, args.plan.name)))
                )
            path = prepare(plan, repositories, args.output_root)
        else:
            path = args.preparation
        manifest = verify_preparation(path, repositories)
        print(json.dumps(readiness(manifest), sort_keys=True))
        return 3 if args.command == "preflight" else 0
    except (OSError, ValueError, ValidationError):
        print('{"error":"evaluation_preparation_rejected"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
