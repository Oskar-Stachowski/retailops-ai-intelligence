"""Freeze and execute the original 25/50/100 development Source/forecast program."""

import argparse
import json
import logging
from pathlib import Path

from retailops_ai.evaluation_campaign.development_policy_freeze import (
    load_frozen_development_policies,
)
from retailops_ai.evaluation_campaign.development_workflow import run_development_workflow
from retailops_ai.evaluation_campaign.development_workflow_contract import (
    parse_development_workflow_options,
)
from retailops_ai.source_snapshot.files import read_bytes


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--options", required=True, type=Path)
    parser.add_argument("--policies", required=True, type=Path)
    parser.add_argument("--maximum-new-operations", type=int, choices=range(1, 52))
    args = parser.parse_args()
    options = parse_development_workflow_options(
        read_bytes(args.options.parent, args.options.name, 65536)
    )
    policies = load_frozen_development_policies(args.policies)
    args.root.mkdir(mode=0o700, exist_ok=True)
    print(
        json.dumps(
            run_development_workflow(
                args.root, options, policies, maximum_new_operations=args.maximum_new_operations
            ),
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
