"""Audit metadata and development read reservations; never open outcome datasets."""

import argparse
import json
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.outcome_contract import (
    OutcomeAccessBinding,
    OutcomeAccessPlan,
    OutcomeJournalPolicy,
)
from retailops_ai.evaluation_campaign.outcome_journal import (
    audit_code,
    exposure_status,
    finish,
    initialize,
    inspect,
    load_history,
    register_plan,
    reserve,
    summary,
    verify_history,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, read_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "freeze",
        "inspect",
        "preflight",
        "register-plan",
        "reserve",
        "finish",
        "verify-history",
    ):
        child = commands.add_parser(name)
        child.add_argument("--journal", required=True, type=Path)
        if name == "freeze":
            child.add_argument("--history", required=True, type=Path)
            child.add_argument("--expected-history-sha256", required=True)
            child.add_argument("--maximum-plans", type=int, default=16)
            child.add_argument("--maximum-new-reads", type=int, default=64)
            child.add_argument("--maximum-reads-per-binding", type=int, default=4)
        elif name == "inspect":
            child.add_argument("--full", action="store_true")
        elif name == "register-plan":
            child.add_argument("--plan", required=True, type=Path)
        elif name == "reserve":
            child.add_argument("--access-plan-sha256", required=True)
            child.add_argument("--binding", required=True, type=Path)
        elif name == "finish":
            child.add_argument("--access-id", required=True)
            child.add_argument("--result", choices=("completed", "failed"), required=True)
            child.add_argument("--error-code")
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            history, digest = load_history(args.history)
            if digest != args.expected_history_sha256:
                raise SnapshotError("outcome_journal_history_freeze_mismatch")
            policy = OutcomeJournalPolicy(
                journal_path=str(args.journal.absolute()),
                historical_inventory_path=str(args.history.absolute()),
                historical_inventory_file_sha256=digest,
                history=history,
                runtime=runtime_pin(),
                audit_code_sha256=audit_code(),
                maximum_plans=args.maximum_plans,
                maximum_new_reads=args.maximum_new_reads,
                maximum_reads_per_binding=args.maximum_reads_per_binding,
            )
            result = summary(initialize(args.journal, policy))
        elif args.command == "register-plan":
            plan = OutcomeAccessPlan.model_validate_json(
                canonical_bytes(decode_json(read_bytes(args.plan.parent, args.plan.name)))
            )
            result = {"access_plan_sha256": register_plan(args.journal, plan)}
        elif args.command == "reserve":
            binding = OutcomeAccessBinding.model_validate_json(
                canonical_bytes(decode_json(read_bytes(args.binding.parent, args.binding.name)))
            )
            result = reserve(args.journal, args.access_plan_sha256, binding).model_dump(mode="json")
            result["related_data"] = exposure_status(inspect(args.journal), binding.population)
        elif args.command == "finish":
            result = finish(
                args.journal, args.access_id, result=args.result, error_code=args.error_code
            ).model_dump(mode="json")
        else:
            ledger = inspect(args.journal)
            if args.command == "verify-history":
                verify_history(ledger.policy.history)
            result = (
                ledger.model_dump(mode="json")
                if args.command == "inspect" and args.full
                else summary(ledger)
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 3 if args.command == "preflight" else 0
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "error_code": str(exc)
                    if isinstance(exc, SnapshotError)
                    else "outcome_journal_command_rejected"
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
