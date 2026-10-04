"""Freeze or verify chronological forecast roles; preflight remains not ready."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from retailops_ai.data_contracts.common import DateWindow
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy
from retailops_ai.evaluation_campaign.partitions import (
    chronological_policy,
    prepare_partitions,
    readiness,
    verify_partitions,
)
from retailops_ai.source_snapshot.files import decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template")
    template.add_argument("--start", required=True, type=date.fromisoformat)
    template.add_argument("--end", required=True, type=date.fromisoformat)
    template.add_argument("--train-days", type=int, default=30)
    template.add_argument("--other-role-days", type=int, default=10)
    template.add_argument("--purge-days", type=int, default=15)
    template.add_argument("--label-delay-days", type=int, default=1)
    for command in ("prepare", "verify", "preflight"):
        child = commands.add_parser(command)
        child.add_argument("--features", required=True, type=Path)
        if command == "prepare":
            child.add_argument("--policy", required=True, type=Path)
            child.add_argument("--output-root", required=True, type=Path)
        else:
            child.add_argument("--partitions", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "template":
            policy = chronological_policy(
                DateWindow(start=args.start, end=args.end),
                train_days=args.train_days,
                other_role_days=args.other_role_days,
                purge_days=args.purge_days,
                label_delay_days=args.label_delay_days,
            )
            print(json.dumps(policy.model_dump(mode="json"), indent=2, sort_keys=True))
            return 0
        if args.command == "prepare":
            policy = ForecastPartitionPolicy.model_validate_json(
                canonical_bytes(
                    decode_json(read_bytes(args.policy.parent, args.policy.name, 64 * 1024))
                )
            )
            root = prepare_partitions(args.features, policy, args.output_root)
        else:
            root = args.partitions
        manifest = verify_partitions(args.features, root)
        print(json.dumps(readiness(manifest), sort_keys=True))
        return 3 if args.command == "preflight" else 0
    except (OSError, ValueError):
        print('{"error":"forecast_partition_rejected"}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
