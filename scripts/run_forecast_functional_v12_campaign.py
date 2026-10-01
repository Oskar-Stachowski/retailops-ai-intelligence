"""Score, replay, resume, export or verify a frozen cohort campaign; never generates data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecasting.functional_v12_campaign import score_archives
from retailops_ai.forecasting.functional_v12_run import export_run, verify_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    evaluate = commands.add_parser("score")
    evaluate.add_argument("--freeze", type=Path, required=True)
    evaluate.add_argument("--registry", type=Path, required=True)
    evaluate.add_argument("--checkpoints", type=Path, nargs="+", required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    repeat = evaluate.add_mutually_exclusive_group()
    repeat.add_argument("--replay", type=Path)
    repeat.add_argument("--resume", type=Path)
    export = commands.add_parser("export")
    export.add_argument("--campaign", type=Path, required=True)
    export.add_argument("--replay", type=Path, required=True)
    export.add_argument("--checkpoints", type=Path, nargs="+", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--code-commit", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "score":
        destination = score_archives(
            args.checkpoints,
            json.loads(args.freeze.read_bytes()),
            args.registry,
            args.output,
            replay=args.replay,
            resume=args.resume,
        )
        result = {"directory": str(destination), "source_regenerated": False}
    else:
        destination = (
            export_run(args.campaign, args.replay, args.checkpoints, args.output, args.code_commit)
            if args.action == "export"
            else args.run
        )
        manifest = verify_run(destination)
        result = {"directory": str(destination), "manifest": manifest}
    print(canonical_bytes(result).decode())


if __name__ == "__main__":
    main()
