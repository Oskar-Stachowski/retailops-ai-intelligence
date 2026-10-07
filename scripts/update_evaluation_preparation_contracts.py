"""Generate/check the AI 09 planning schemas without touching upstream checkouts."""

import argparse
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignProtocol,
    SelectionFreeze,
)
from retailops_ai.evaluation_campaign.contract import EvaluationPreparation, PreparationManifest
from retailops_ai.evaluation_campaign.label_contract import (
    ForecastOutcomeReadProtocol,
    OutcomeEvidence,
    OutcomeEvidenceManifest,
    QualifiedForecastOutcome,
)
from retailops_ai.evaluation_campaign.legacy_carryover import LegacyCampaignCarryover
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
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
    PhysicalForecastRecipe,
    PhysicalSourceSpec,
)
from retailops_ai.evaluation_campaign.preparation import default_plan
from retailops_ai.evaluation_campaign.source_replay_contract import (
    ForecastSourceReplayProtocol,
    ForecastSourceReplayReceipt,
)
from retailops_ai.evaluation_campaign.source_version_contract import (
    ForecastSourceVersion,
    ForecastSourceVersionReceipt,
)
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
    for name, label_model in (
        ("forecast_outcome_read_protocol", ForecastOutcomeReadProtocol),
        ("outcome_evidence", OutcomeEvidence),
        ("outcome_evidence_manifest", OutcomeEvidenceManifest),
        ("qualified_forecast_outcome", QualifiedForecastOutcome),
    ):
        schema = label_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:6.0.0"
        path = ROOT / "contracts/evaluation/v6" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Forecast outcome reader contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, replay_model in (
        ("forecast_source_replay_protocol", ForecastSourceReplayProtocol),
        ("forecast_source_replay_receipt", ForecastSourceReplayReceipt),
    ):
        schema = replay_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:7.0.0"
        path = ROOT / "contracts/evaluation/v7" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Forecast source replay contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, version_model in (
        ("forecast_source_version", ForecastSourceVersion),
        ("forecast_source_version_receipt", ForecastSourceVersionReceipt),
    ):
        schema = version_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:8.0.0"
        path = ROOT / "contracts/evaluation/v8" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Forecast source version contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    schema = LegacyCampaignCarryover.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:retailops:evaluation:legacy_campaign_carryover:9.0.0"
    path = ROOT / "contracts/evaluation/v9/legacy_campaign_carryover.schema.json"
    raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not path.is_file() or path.read_text() != raw:
            print("Legacy campaign carryover contract differs")
            return 1
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw)
    for name, campaign_model in (
        ("prospective_campaign_protocol", CampaignProtocol),
        ("prospective_campaign_journal", CampaignJournal),
        ("prospective_selection_freeze", SelectionFreeze),
    ):
        schema = campaign_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:10.0.0"
        path = ROOT / "contracts/evaluation/v10" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Prospective campaign contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, physical_model in (
        ("physical_source_spec", PhysicalSourceSpec),
        ("physical_forecast_recipe", PhysicalForecastRecipe),
        ("physical_forecast_example", PhysicalForecastExample),
        ("physical_forecast_manifest", PhysicalForecastManifest),
    ):
        schema = physical_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:11.0.0"
        path = ROOT / "contracts/evaluation/v11" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Physical forecast contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    print("Evaluation preparation contract snapshots checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
