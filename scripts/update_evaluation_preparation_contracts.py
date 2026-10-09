"""Generate/check the AI 09 planning schemas without touching upstream checkouts."""

import argparse
import json
from pathlib import Path

from retailops_ai.evaluation_campaign.campaign_anomaly_fit_contract import (
    CampaignAnomalyFitPlan,
    CampaignAnomalyFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibration,
    CampaignForecastCalibrationPlan,
    CampaignForecastCalibrationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_context_bundle_contract import (
    CampaignContextBundleReceipt,
    CampaignContextBundleRecipe,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignJournal,
    CampaignProtocol,
    SelectionFreeze,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastEvaluationPrediction,
    CampaignForecastFrozenConfiguration,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationReceipt,
    CampaignForecastEvaluationRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignDevelopmentExportReceipt,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    CampaignFinalExportReceipt,
    FinalForecastExample,
    FinalForecastManifest,
    FinalForecastRecipe,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastEncoding,
    CampaignForecastFitPlan,
    CampaignForecastFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationPlan,
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioJournal,
    CampaignPortfolioProtocol,
    PortfolioSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_evaluation_contract import (
    CampaignPortfolioForecastEvaluationBinding,
)
from retailops_ai.evaluation_campaign.campaign_required_group_contract import (
    CampaignPortfolioRequiredGroupPolicy,
)
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CampaignForecastPortfolioEvaluationReceipt,
    CampaignForecastPortfolioRobustEvaluationReceipt,
    CampaignForecastRequiredGroupEvaluationReceipt,
    CampaignForecastRobustEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    CampaignForecastRawPrediction,
    CampaignForecastScorePlan,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignContextStoragePolicy,
    CampaignForecastContextScope,
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
    CampaignForecastSegmentPolicy,
    CampaignOriginRoute,
    CampaignSourceKeyAnnotation,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastTunePlan,
    CampaignForecastTuneReceipt,
    CampaignForecastTuneSelection,
)
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
    CampaignForecastUncertaintyReport,
)
from retailops_ai.evaluation_campaign.contract import EvaluationPreparation, PreparationManifest
from retailops_ai.evaluation_campaign.development_planning_contract import (
    NativeDevelopmentPlanningJournal,
    NativeDevelopmentPlanningProtocol,
    NativeDevelopmentPlanningReceipt,
)
from retailops_ai.evaluation_campaign.development_preparation import (
    DevelopmentPreparationJournal,
    DevelopmentPreparationProtocol,
)
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfile,
    DevelopmentProfilePreparation,
    DevelopmentProfileSource,
)
from retailops_ai.evaluation_campaign.development_search import ForecastDevelopmentSearch
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
    for name, export_model in (
        ("campaign_development_export_plan", CampaignDevelopmentExportPlan),
        ("campaign_generated_parent_receipt", CampaignGeneratedParentReceipt),
        ("campaign_development_export_receipt", CampaignDevelopmentExportReceipt),
    ):
        schema = export_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:12.0.0"
        path = ROOT / "contracts/evaluation/v12" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign development export contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, generation_model in (
        ("campaign_generation_plan", CampaignGenerationPlan),
        ("campaign_generation_resources", CampaignGenerationResources),
    ):
        schema = generation_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:13.0.0"
        path = ROOT / "contracts/evaluation/v13" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign generation contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, final_model in (
        ("campaign_final_export_plan", CampaignFinalExportPlan),
        ("campaign_final_export_receipt", CampaignFinalExportReceipt),
        ("final_forecast_recipe", FinalForecastRecipe),
        ("final_forecast_manifest", FinalForecastManifest),
        ("final_forecast_example", FinalForecastExample),
    ):
        schema = final_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:14.0.0"
        path = ROOT / "contracts/evaluation/v14" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign final export contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, fit_model in (
        ("campaign_forecast_fit_plan", CampaignForecastFitPlan),
        ("campaign_forecast_fit_receipt", CampaignForecastFitReceipt),
        ("campaign_forecast_encoding", CampaignForecastEncoding),
    ):
        schema = fit_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:15.0.0"
        path = ROOT / "contracts/evaluation/v15" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast fit contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, score_model in (
        ("campaign_forecast_score_plan", CampaignForecastScorePlan),
        ("campaign_forecast_score_receipt", CampaignForecastScoreReceipt),
        ("campaign_forecast_raw_prediction", CampaignForecastRawPrediction),
    ):
        schema = score_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:16.0.0"
        path = ROOT / "contracts/evaluation/v16" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast score contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, tune_model in (
        ("campaign_forecast_tune_plan", CampaignForecastTunePlan),
        ("campaign_forecast_tune_receipt", CampaignForecastTuneReceipt),
        ("campaign_forecast_tune_selection", CampaignForecastTuneSelection),
    ):
        schema = tune_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:17.0.0"
        path = ROOT / "contracts/evaluation/v17" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast Tune contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, calibration_model in (
        ("campaign_forecast_calibration_plan", CampaignForecastCalibrationPlan),
        ("campaign_forecast_calibration_receipt", CampaignForecastCalibrationReceipt),
        ("campaign_forecast_calibration", CampaignForecastCalibration),
    ):
        schema = calibration_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:18.0.0"
        path = ROOT / "contracts/evaluation/v18" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast Calibration contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, evaluation_model in (
        ("frozen_forecast_configuration", CampaignForecastFrozenConfiguration),
        ("campaign_forecast_evaluation_plan", CampaignForecastEvaluationPlan),
        ("campaign_forecast_trial_prediction", CampaignForecastTrialPrediction),
        ("campaign_forecast_evaluation_prediction", CampaignForecastEvaluationPrediction),
    ):
        schema = evaluation_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:19.0.0"
        path = ROOT / "contracts/evaluation/v19" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast evaluation contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, execution_model in (
        ("campaign_forecast_evaluation_recipe", CampaignForecastEvaluationRecipe),
        ("campaign_forecast_evaluation_receipt", CampaignForecastEvaluationReceipt),
    ):
        schema = execution_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:20.0.0"
        path = ROOT / "contracts/evaluation/v20" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast evaluation execution contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, uncertainty_model in (
        ("campaign_forecast_uncertainty_policy", CampaignForecastUncertaintyPolicy),
        ("campaign_forecast_uncertainty_report", CampaignForecastUncertaintyReport),
    ):
        schema = uncertainty_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:21.0.0"
        path = ROOT / "contracts/evaluation/v21" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast uncertainty contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, context_model in (
        ("campaign_context_storage_policy", CampaignContextStoragePolicy),
        ("campaign_forecast_context_scope", CampaignForecastContextScope),
        ("campaign_forecast_key_context", CampaignForecastKeyContext),
        ("campaign_forecast_segment_census", CampaignForecastSegmentCensus),
        ("campaign_forecast_segment_policy", CampaignForecastSegmentPolicy),
        ("campaign_origin_route", CampaignOriginRoute),
        ("campaign_source_key_annotation", CampaignSourceKeyAnnotation),
    ):
        schema = context_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:22.0.0"
        path = ROOT / "contracts/evaluation/v22" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign forecast source context contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, context_bundle_model in (
        ("campaign_context_bundle_recipe", CampaignContextBundleRecipe),
        ("campaign_context_bundle_receipt", CampaignContextBundleReceipt),
    ):
        schema = context_bundle_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:23.0.0"
        path = ROOT / "contracts/evaluation/v23" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign source context bundle contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    name = "campaign_forecast_robust_evaluation_receipt"
    schema = CampaignForecastRobustEvaluationReceipt.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:retailops:evaluation:{name}:24.0.0"
    path = ROOT / "contracts/evaluation/v24" / (name + ".schema.json")
    raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not path.is_file() or path.read_text() != raw:
            print("Campaign complete forecast robustness contract differs: " + name)
            return 1
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw)
    for name, portfolio_model in (
        ("full_scenario_portfolio_source_recipe", PortfolioSourceRecipe),
        ("full_scenario_portfolio_protocol", CampaignPortfolioProtocol),
        ("full_scenario_portfolio_journal", CampaignPortfolioJournal),
    ):
        schema = portfolio_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:25.0.0"
        path = ROOT / "contracts/evaluation/v25" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign full scenario portfolio contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, portfolio_evaluation_model in (
        ("portfolio_forecast_evaluation_binding", CampaignPortfolioForecastEvaluationBinding),
        ("portfolio_forecast_evaluation_receipt", CampaignForecastPortfolioEvaluationReceipt),
        (
            "portfolio_forecast_robust_evaluation_receipt",
            CampaignForecastPortfolioRobustEvaluationReceipt,
        ),
    ):
        schema = portfolio_evaluation_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:26.0.0"
        path = ROOT / "contracts/evaluation/v26" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign portfolio forecast evaluation contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, required_model in (
        ("portfolio_required_group_policy", CampaignPortfolioRequiredGroupPolicy),
        (
            "forecast_required_group_evaluation_receipt",
            CampaignForecastRequiredGroupEvaluationReceipt,
        ),
    ):
        schema = required_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:27.0.0"
        path = ROOT / "contracts/evaluation/v27" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign required-group contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, anomaly_model in (
        ("campaign_anomaly_fit_plan", CampaignAnomalyFitPlan),
        ("campaign_anomaly_fit_receipt", CampaignAnomalyFitReceipt),
    ):
        schema = anomaly_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:28.0.0"
        path = ROOT / "contracts/evaluation/v28" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Campaign anomaly fit contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, development_model in (
        ("development_profile", DevelopmentProfile),
        ("development_profile_source", DevelopmentProfileSource),
        ("development_profile_preparation", DevelopmentProfilePreparation),
        ("forecast_development_search", ForecastDevelopmentSearch),
    ):
        schema = development_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:29.0.0"
        path = ROOT / "contracts/evaluation/v29" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Development profile/search contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for products in (25, 50):
        profile = DevelopmentProfile.model_validate(
            {"name": f"ai09-development-{products}-v1", "products": products}
        )
        path = ROOT / "contracts/evaluation/v29" / f"development-{products}.default.json"
        raw = json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Development default profile differs: " + str(products))
                return 1
        else:
            path.write_text(raw)
    for name, preparation_model in (
        ("development_preparation_protocol", DevelopmentPreparationProtocol),
        ("development_preparation_journal", DevelopmentPreparationJournal),
    ):
        schema = preparation_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:30.0.0"
        path = ROOT / "contracts/evaluation/v30" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Development preparation execution contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    for name, planning_model in (
        ("native_development_planning_protocol", NativeDevelopmentPlanningProtocol),
        ("native_development_planning_journal", NativeDevelopmentPlanningJournal),
        ("native_development_planning_receipt", NativeDevelopmentPlanningReceipt),
    ):
        schema = planning_model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:retailops:evaluation:{name}:31.0.0"
        path = ROOT / "contracts/evaluation/v31" / (name + ".schema.json")
        raw = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != raw:
                print("Native development planning contract differs: " + name)
                return 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw)
    print("Evaluation preparation contract snapshots checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
