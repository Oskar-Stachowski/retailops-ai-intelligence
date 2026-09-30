"""Freeze or run AI04 v11 from the retained v10 snapshot, without source generation."""

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forecast_quality_v2_preflight import RETAINED, digest, verify_freeze, verify_retained

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.features_store import implementation
from retailops_ai.forecasting.functional_campaign import (
    build_campaign,
    load_campaign,
    require_unseen,
    validate_freeze,
)
from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.functional_evidence import test_receipts
from retailops_ai.forecasting.functional_models import functional_code
from retailops_ai.forecasting.functional_run import export_run, load_run
from retailops_ai.forecasting.manifests import load_feature_set
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.splits import load_split

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3


def save(path: Path, body: dict[str, Any], exclusive: bool = False) -> None:
    with path.open("x" if exclusive else "w") as stream:
        json.dump(body, stream, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("freeze", "run"))
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--test-report", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.report.exists():
        raise ValueError("preserve_previous_report_use_new_path")
    prepared = json.loads(args.prepared.read_text())
    if prepared.get("step") != "split_verified" or prepared.get("status") != "passed":
        raise ValueError("requires_complete_qualified_prepared_split")
    features, split, curated = (
        Path(prepared[n]) for n in ("feature_dir", "split_dir", "curated_dir")
    )
    feature, split_manifest = load_feature_set(features), load_split(split)
    original = json.loads(
        (ROOT / "contracts/forecast/v1/quality-remediation.campaign-v10.json").read_text()
    )
    backtest = BacktestPolicy.model_validate_json(json.dumps(original["backtest"]))
    policy = FunctionalPolicy(model=backtest.model)
    expected_split = plan_backtest(
        load_calendar(features / "inputs/calendar_manifest.json").descriptor.origin_window, backtest
    )
    if split_manifest.descriptor.resolved_policy != expected_split:
        raise ValueError("campaign_windows_changed_since_v10")
    protocol = verify_freeze()
    retained = verify_retained(RETAINED)
    input_pin = json.loads((ROOT / "contracts/forecast/v2/inputs-v12.freeze.json").read_text())
    if input_pin["implementation"] != implementation():
        raise ValueError("input_v12_code_changed")
    old_freeze = json.loads((ROOT / "contracts/forecast/v2/quality.freeze.json").read_text())
    if (
        feature.descriptor.parent.snapshot_id != old_freeze["snapshot_id"]
        or feature.descriptor.parent.source_dataset_id != old_freeze["source_dataset_id"]
    ):
        raise ValueError("snapshot_changed_since_quality_freeze")
    free = {str(p): shutil.disk_usage(p).free for p in (ROOT, Path(tempfile.gettempdir()))}
    if min(free.values()) < 16 * GIB:
        raise ValueError("requires_16GiB_free_before_sequential_campaign")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()  # noqa: S603,S607 -- fixed read-only command
    tests = test_receipts(args.test_report) if args.test_report else {}
    state: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "code_commit": commit,
        "protocol_freeze": protocol,
        "retained": retained,
        "free_bytes": free,
        "required_free_bytes": 16 * GIB,
        "source_regenerated": False,
        "portfolio_final_test": "not_included_not_opened",
        "test_receipts": tests,
    }
    save(args.report, state, True)
    if args.operation == "freeze":
        if not tests:
            raise ValueError("freeze_requires_passing_test_receipts")
        body = {
            "version": "ai04-campaign-v11-functional-v1",
            "frozen_at": datetime.now(UTC).isoformat(),
            "code_commit": commit,
            "code": functional_code(),
            "model_environment": model_code().model_dump(mode="json"),
            "policy": policy.model_dump(mode="json"),
            "backtest_policy": backtest.model_dump(mode="json"),
            "feature_set_id": feature.feature_set_id,
            "split_id": split_manifest.split_id,
            "parent": feature.descriptor.parent.model_dump(mode="json"),
            "quality_freeze_sha256": digest(ROOT / "contracts/forecast/v2/quality.freeze.json"),
            "input_freeze_sha256": digest(ROOT / "contracts/forecast/v2/inputs-v12.freeze.json"),
            "preparation_report_sha256": digest(args.prepared),
            "test_receipts": tests,
            "source_regenerated": False,
            "new_holdout_metrics_evaluated_before_freeze": False,
            "model_fits_before_freeze": 0,
            "portfolio_final_test": "not_included_not_opened",
            "disk_reserve_bytes": 16 * GIB,
            "original_v10_report_preserved": True,
        }
        body["freeze_id"] = "functional-freeze-sha256-" + canonical_sha256(body)
        validate_freeze(body, features, split, policy)
        require_unseen(ROOT / "data/generated/functional-campaigns", body)
        save(args.freeze, body, True)
        state.update(
            status="frozen", freeze_id=body["freeze_id"], freeze_sha256=digest(args.freeze)
        )
        save(args.report, state)
        print(json.dumps({"status": "frozen", "freeze": str(args.freeze)}))
        return 0
    freeze = json.loads(args.freeze.read_text())
    validate_freeze(freeze, features, split, policy)
    try:
        state.update(step="campaign_started", freeze_id=freeze["freeze_id"])
        save(args.report, state)
        campaign = build_campaign(
            features, split, ROOT / "data/generated/functional-campaigns", policy, freeze
        )
        manifest = load_campaign(campaign)
        state.update(
            step="campaign_completed",
            campaign_dir=str(campaign),
            campaign_id=manifest["campaign_id"],
            quality=manifest["descriptor"]["gates"],
            model_fits=len(policy.heads) * len(expected_split.folds),
            holdout_metrics_evaluated=True,
        )
        save(args.report, state)
        source = ROOT / "data/generated/snapshots" / feature.descriptor.parent.source_dataset_id
        run = export_run(
            source,
            curated,
            features,
            split,
            campaign,
            ROOT / "data/generated/functional-runs",
            commit,
        )
        state.update(
            step="replayed_and_exported",
            status="completed",
            run_dir=str(run),
            run_id=load_run(run)["run_id"],
            independent_replay="passed",
            finished_at=datetime.now(UTC).isoformat(),
        )
        save(args.report, state)
        print(json.dumps({"status": "completed", "quality": state["quality"], "run_dir": str(run)}))
        return 0 if state["quality"]["status"] == "passed" else 3
    except Exception as exc:
        state.update(
            status="blocked",
            error_type=type(exc).__name__,
            error=str(exc),
            failed_at=datetime.now(UTC).isoformat(),
        )
        save(args.report, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
