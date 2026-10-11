"""Prepare original ordinary/demand/physical parents using frozen policy documents."""

import argparse
import json
import logging
from pathlib import Path

from retailops_ai.evaluation_campaign.development_policy_freeze import (
    load_frozen_development_policies,
)
from retailops_ai.evaluation_campaign.source_preparation import run_source_preparation
from retailops_ai.evaluation_campaign.source_preparation_contract import SourcePreparationOptions
from retailops_ai.source_snapshot.files import read_bytes


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--options", type=Path, required=True)
    parser.add_argument("--policies", type=Path, required=True)
    parser.add_argument("--maximum-new-operations", type=int, default=5, choices=range(1, 6))
    args = parser.parse_args()
    options = SourcePreparationOptions.model_validate_json(
        read_bytes(args.options.parent, args.options.name, 65536)
    )
    policies = load_frozen_development_policies(args.policies)
    args.root.mkdir(mode=0o700, exist_ok=True)
    print(
        json.dumps(
            run_source_preparation(
                args.root, options, policies, maximum_new_operations=args.maximum_new_operations
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
