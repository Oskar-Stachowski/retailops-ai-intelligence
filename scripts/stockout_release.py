"""Qualify and explicitly review a final stockout capsule; no model activation."""

import argparse
import json
from pathlib import Path

from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.security.model_operator import model_operator
from retailops_ai.source_snapshot.files import read_bytes
from retailops_ai.stockout_campaign.contract import CampaignFreeze, CampaignPermission
from retailops_ai.stockout_lifecycle.contract import ApprovalRequest
from retailops_ai.stockout_lifecycle.qualification import approve_stockout, qualify_final
from retailops_ai.stockout_lifecycle.release import verify_approved_capsule
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.inputs import PhysicalScope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    qualify = commands.add_parser("qualify")
    for name in (
        "freeze",
        "permission",
        "recipe",
        "policy",
        "receipts",
        "selection",
        "curated",
        "features",
        "upstream",
        "scope",
        "output",
    ):
        qualify.add_argument("--" + name, type=Path, required=True)
    qualify.add_argument("--as-of", required=True)
    approve = commands.add_parser("approve")
    for name in (
        "qualification",
        "request",
        "reports",
        "policy-file",
        "credentials-file",
        "output",
    ):
        approve.add_argument("--" + name, type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--capsule", type=Path, required=True)
    verify.add_argument("--approval-id", required=True)
    args = parser.parse_args()
    try:
        if args.command == "qualify":
            freeze = CampaignFreeze.model_validate_json(
                read_bytes(args.freeze.parent, args.freeze.name)
            )
            path = qualify_final(
                freeze=freeze,
                permission=CampaignPermission.model_validate_json(
                    read_bytes(args.permission.parent, args.permission.name)
                ),
                recipe=ScoringRecipe.model_validate_json(
                    read_bytes(args.recipe.parent, args.recipe.name)
                ),
                policy=ScoringPolicy.model_validate_json(
                    read_bytes(args.policy.parent, args.policy.name)
                ),
                receipt_roots={
                    (s.world, s.seed): args.receipts / f"{s.world}-{s.seed}" for s in freeze.sources
                },
                selection_path=args.selection,
                curated=args.curated,
                features=args.features,
                upstream=args.upstream,
                scope=PhysicalScope.model_validate_json(
                    read_bytes(args.scope.parent, args.scope.name)
                ),
                as_of=TypeAdapter(UtcTime).validate_python(args.as_of),
                output=args.output,
            )
            result = dict(qualification_id=path.name, serving_eligible=False)
        elif args.command == "approve":
            actor = model_operator(args.policy_file, args.credentials_file)
            path = approve_stockout(
                args.qualification,
                actor=actor,
                request=ApprovalRequest.model_validate_json(
                    read_bytes(args.request.parent, args.request.name)
                ),
                reports=args.reports,
                output=args.output,
            )
            result = dict(
                approval_id=path.name, registered_in_mlflow=False, activated_as_champion=False
            )
        else:
            approval = verify_approved_capsule(args.capsule, approval_id=args.approval_id)
            result = dict(approval_id=approval.release_id, capsule_verified=True)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(
            json.dumps(dict(error="stockout_release_operation_failed", kind=type(error).__name__))
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
