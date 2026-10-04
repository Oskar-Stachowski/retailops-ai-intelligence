"""Generate/check the AI 09 planning schemas without touching upstream checkouts."""

import argparse
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.contract import EvaluationPreparation, PreparationManifest
from retailops_ai.evaluation_campaign.outcome_contract import (
    OutcomeAccessBinding,
    OutcomeAccessPlan,
    OutcomeJournal,
    OutcomeJournalPolicy,
)
from retailops_ai.evaluation_campaign.partition_contract import (
    ForecastPartitionManifest,
    ForecastPartitionPolicy,
    PartitionMembership,
)
from retailops_ai.evaluation_campaign.preparation import default_plan
from retailops_ai.evaluation_campaign.trial_contract import AttemptSnapshot, TrialLedger, TrialPlan

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    values: dict[str, object] = {"preparation.default.json": default_plan().model_dump(mode="json")}
    for name, model in (
        ("preparation", EvaluationPreparation),
        ("preparation_manifest", PreparationManifest),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:1.0.0"
        values[name + ".schema.json"] = schema
    stale = []
    for name, value in values.items():
        path = ROOT / "contracts/evaluation/v1" / name
        raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    if stale:
        print("Evaluation preparation contracts differ: " + ", ".join(stale))
        return 1
    for name, trial_model in (
        ("attempt_snapshot", AttemptSnapshot),
        ("trial_plan", TrialPlan),
        ("trial_ledger", TrialLedger),
    ):
        schema = trial_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:3.0.0"
        path = ROOT / "contracts/evaluation/v3" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Development trial contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, partition_model in (
        ("forecast_partition_policy", ForecastPartitionPolicy),
        ("forecast_partition_manifest", ForecastPartitionManifest),
        ("forecast_partition_membership", PartitionMembership),
    ):
        schema = partition_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:4.0.0"
        path = ROOT / "contracts/evaluation/v4" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Independent forecast partition contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, outcome_model in (
        ("outcome_access_binding", OutcomeAccessBinding),
        ("outcome_access_plan", OutcomeAccessPlan),
        ("outcome_journal_policy", OutcomeJournalPolicy),
        ("outcome_journal", OutcomeJournal),
    ):
        schema = outcome_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:5.0.0"
        path = ROOT / "contracts/evaluation/v5" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Outcome access contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    print("Evaluation preparation contract snapshots checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
