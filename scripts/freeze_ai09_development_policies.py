"""Freeze actual policy documents and legacy costs before initializing development."""

import argparse
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.development_policy_freeze import (
    DevelopmentPolicySet,
    freeze_development_policies,
)
from retailops_ai.source_snapshot.files import read_bytes


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policies",
        type=Path,
        default=root / "contracts/evaluation/v38/development_policies.default.json",
    )
    parser.add_argument("--legacy-evidence", type=Path, default=root / "docs/evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--producer", type=Path, required=True)
    args = parser.parse_args()
    policies = DevelopmentPolicySet.model_validate_json(
        read_bytes(args.policies.parent, args.policies.name, 1024**2)
    )
    result = freeze_development_policies(
        args.output, policies=policies, evidence=args.legacy_evidence, producer=args.producer
    )
    print(
        json.dumps(
            {
                "policies_sha256": result.policies_sha256,
                "runtime_code_sha256": result.context.runtime.code_sha256,
                "legacy_sha256": result.context.legacy_sha256,
                "producer_commit": result.policies.catalog.producer_commit,
                "model_fit_authorized": False,
                "final_test_access_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
