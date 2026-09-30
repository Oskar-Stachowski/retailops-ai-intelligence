"""Run a frozen offline campaign; missing feature samples stop before any model fit."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from retailops_ai.curated.builder import build_curated
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting import remediation as legacy_remediation
from retailops_ai.forecasting import remediation_v2
from retailops_ai.forecasting.backtest import build_backtest
from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.calendar import build_calendar, publish_calendar
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.manifests import build_feature_set
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.remediation_preflight import preflight
from retailops_ai.forecasting.remediation_source_preflight import source_preflight
from retailops_ai.source_snapshot.importer import import_snapshot

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument(
        "--campaign",
        type=Path,
        default=ROOT / "contracts/forecast/v1/quality-remediation.campaign-v10.json",
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/quality-remediation-campaign.json"
    )
    args = parser.parse_args()
    raw = args.campaign.read_bytes()
    campaign = json.loads(raw)
    version = campaign.get("remediation_version", "forecast-quality-remediation-1.0.0")
    if version not in ("forecast-quality-remediation-1.0.0", "forecast-quality-remediation-2.1.0"):
        raise ValueError("campaign_unknown_remediation_version")
    engine = remediation_v2 if version.endswith("2.1.0") else legacy_remediation
    if version.endswith("2.1.0") and (
        campaign.get("remediation_policy") != engine.RemediationPolicy().model_dump(mode="json")
        or not campaign.get("remediation_code_sha256")
        or not campaign.get("resolved_backtest_policy")
    ):
        raise ValueError("campaign_requires_frozen_v2_recipe_and_model_policy")
    if campaign.get("remediation_code_sha256") and (
        canonical_sha256(engine.code_files()) != campaign["remediation_code_sha256"]
    ):
        raise ValueError("campaign_remediation_code_pin_mismatch")
    if (
        QualityPolicy.model_validate_json(json.dumps(campaign["quality_thresholds"]))
        != QualityPolicy()
    ):
        raise ValueError("campaign_requires_unchanged_original_quality_thresholds")
    policy = BacktestPolicy.model_validate_json(json.dumps(campaign["backtest"]))
    if campaign.get(
        "resolved_backtest_policy", policy.model_dump(mode="json")
    ) != policy.model_dump(mode="json"):
        raise ValueError("campaign_model_policy_changed")
    window = OriginWindow(
        start=date.fromisoformat(campaign["origins"]["start"]),
        end=date.fromisoformat(campaign["origins"]["end"]),
    )
    plan = plan_backtest(window, policy)
    if plan.model_dump(mode="json") != campaign["resolved_split"] or any(
        f.development_holdout.start
        <= date.fromisoformat(campaign["previous_development_holdout_last_origin"])
        for f in plan.folds
    ):
        raise ValueError("campaign_requires_frozen_later_holdouts")
    report: dict[str, Any] = {
        "campaign_sha256": hashlib.sha256(raw).hexdigest(),
        "campaign": campaign,
        "forecast_model_status": "not_ready",
        "aws_calls": 0,
    }

    def save(step: str, **fields: Any) -> None:
        report.update(last_step=step, **fields)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"step": step, **fields}), flush=True)

    generated = ROOT / "data/generated"
    imported = import_snapshot(
        args.snapshot_dir, generated, required_use_cases=("forecast_source",)
    )
    provenance = imported.snapshot.manifest["source"]["provenance"]
    if provenance["git_commit"] != campaign["source_repository_commit"] or (
        provenance["code_state"] != "clean"
    ):
        raise ValueError("campaign_requires_pinned_clean_source_producer")
    if (
        campaign.get("source_dataset_id", imported.snapshot.source_id)
        != imported.snapshot.source_id
    ):
        raise ValueError("campaign_source_identity_mismatch")
    if campaign.get("source_snapshot_verifier_commit") and (
        imported.snapshot.manifest["exporter"]["git_commit"]
        != campaign["source_snapshot_verifier_commit"]
    ):
        raise ValueError("campaign_snapshot_verifier_revision_mismatch")
    save("import", source=imported.summary())
    curated = build_curated(imported.directory, generated)
    parameters = curated.manifest["descriptor"]["source_parameters"]
    if curated.manifest["descriptor"]["source_schema_version"] != (
        campaign["source"]["source_version"] + ".0"
    ) or any(
        parameters[name] != value
        for name, value in campaign["source"].items()
        if name != "source_version"
    ):
        raise ValueError("campaign_source_parameters_mismatch")
    save("curated", curated=curated.summary(), curated_dir=str(curated.directory))
    calendar = build_calendar(curated.directory, window)
    path = publish_calendar(calendar, generated / "calendars")
    save("calendar", calendar_manifest=str(path))
    history_sample = source_preflight(curated.directory, calendar, policy)
    save("source_preflight", source_preflight=history_sample)
    if history_sample["status"] == "not_ready":
        save(
            "blocked_before_features",
            status="not_ready",
            reason="critical_source_history_sample_cannot_reach_minimum_rows",
            model_fits=0,
            features_materialized=False,
        )
        return 3
    features = build_feature_set(curated.directory, calendar, generated / "features")
    save("features", feature_dir=str(features))
    sample = preflight(features, policy)
    save("preflight", preflight=sample)
    if sample["status"] != "passed":
        save(
            "blocked_before_training",
            status="not_ready",
            reason="critical_feature_sample_missing",
            model_fits=0,
        )
        return 3
    backtest = build_backtest(features, curated.directory, generated / "backtests", policy)
    save("backtest", backtest_dir=str(backtest))
    remediation = engine.build_remediation(features, backtest, generated / "forecast-remediation")
    save("remediation", remediation_dir=str(remediation))
    verified = engine.verify_remediation(remediation, features, backtest)
    save(
        "verified",
        status=verified.descriptor.quality_status,
        gate_counts=verified.descriptor.gate_counts,
        remediation_id=verified.remediation_id,
    )
    return 0 if verified.descriptor.quality_status == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
