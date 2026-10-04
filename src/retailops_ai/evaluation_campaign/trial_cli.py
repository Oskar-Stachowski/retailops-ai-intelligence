"""Freeze, inspect and run a shared development registry; never access a final test."""

import argparse
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.development_contract import (
    DevelopmentComparisonPolicy,
    DevelopmentProtocol,
)
from retailops_ai.evaluation_campaign.trial_contract import TrialPlan
from retailops_ai.evaluation_campaign.trial_registry import audit_code, initialize, inspect, summary
from retailops_ai.evaluation_campaign.trial_runner import (
    development_protocol,
    run_registered_comparison,
    snapshot_attempt,
)
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    protocol = commands.add_parser("protocol")
    for name in ("features", "split", "curated"):
        protocol.add_argument("--" + name, required=True, type=Path)
    protocol.add_argument("--fold", required=True)
    protocol.add_argument("--policy", type=Path)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--registry", required=True, type=Path)
    freeze.add_argument("--protocol", required=True, action="append", type=Path)
    freeze.add_argument("--historical-attempt", action="append", type=Path, default=[])
    freeze.add_argument("--maximum-new-attempts", type=int, required=True)
    freeze.add_argument("--maximum-attempts-per-protocol", type=int, default=1)
    show = commands.add_parser("inspect")
    show.add_argument("--registry", required=True, type=Path)
    show.add_argument("--full", action="store_true")
    history = commands.add_parser("verify-history")
    history.add_argument("--registry", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--registry", required=True, type=Path)
    run.add_argument("--protocol-sha256", required=True)
    for name in ("features", "split", "curated", "output"):
        run.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "protocol":
            policy = (
                DevelopmentComparisonPolicy.model_validate_json(
                    read_bytes(args.policy.parent, args.policy.name)
                )
                if args.policy
                else DevelopmentComparisonPolicy()
            )
            result = development_protocol(
                features=args.features,
                split=args.split,
                curated=args.curated,
                fold_name=args.fold,
                policy=policy,
            ).model_dump(mode="json")
        elif args.command == "freeze":
            plan = TrialPlan(
                registry_path=str(args.registry.absolute()),
                protocols=tuple(
                    DevelopmentProtocol.model_validate_json(read_bytes(p.parent, p.name))
                    for p in args.protocol
                ),
                maximum_new_attempts=args.maximum_new_attempts,
                maximum_attempts_per_protocol=args.maximum_attempts_per_protocol,
                historical_attempts=tuple(snapshot_attempt(p) for p in args.historical_attempt),
                audit_code_sha256=audit_code(),
            )
            result = summary(initialize(args.registry, plan))
        elif args.command == "inspect":
            ledger = inspect(args.registry)
            result = ledger.model_dump(mode="json") if args.full else summary(ledger)
        elif args.command == "verify-history":
            ledger = inspect(args.registry)
            for previous in ledger.plan.historical_attempts:
                if snapshot_attempt(Path(previous.output)) != previous:
                    raise SnapshotError("trial_registry_historical_artifact_changed")
            result = summary(ledger) | {"historical_inventory_verified": True}
        else:
            manifest = run_registered_comparison(
                registry=args.registry,
                protocol_sha256=args.protocol_sha256,
                features=args.features,
                split=args.split,
                curated=args.curated,
                output=args.output,
            )
            result = {
                "comparison_id": manifest["comparison_id"],
                "registry_attempt_id": manifest["registry_attempt_id"],
                "evaluation_status": "not_ready",
                "final_test_access_authorized": False,
                "promotion_allowed": False,
            }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": str(exc)
                    if isinstance(exc, SnapshotError)
                    else "development_registry_command_failed",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
