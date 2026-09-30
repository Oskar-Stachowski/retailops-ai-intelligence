"""Read-only preservation, protocol freeze and disk checks; never opens test labels or fits."""

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
RETAINED = ROOT / "docs/evidence/04-quality-protocol-v2-retained.json"
FREEZE = ROOT / "contracts/forecast/v2/quality.freeze.json"
FROZEN_FILES = (
    "src/retailops_ai/forecasting/quality_v2.py",
    "src/retailops_ai/forecasting/quality_v2_contract.py",
    "scripts/forecast_quality_v2_preflight.py",
    "tests/test_forecast_quality_v2.py",
    "tests/test_forecast_quality_v2_preflight.py",
    "contracts/forecast/v2/quality.default.json",
    "contracts/forecast/v2/quality_policy.schema.json",
    "contracts/forecast/v2/quality_observation.schema.json",
    "contracts/forecast/v1/quality-remediation.campaign-v10.json",
    "docs/evidence/04-quality-protocol-v2-retained.json",
    "uv.lock",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_retained(manifest: Path) -> dict[str, Any]:
    rows = json.loads(manifest.read_text())["files"]
    for row in rows:
        path = Path(row["path"])
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != row["bytes"]
            or digest(path) != row["sha256"]
        ):
            raise ValueError("retained_artifact_changed: " + str(path))
    return {"status": "passed", "files": len(rows), "bytes": sum(r["bytes"] for r in rows)}


def estimate_space(campaign: dict[str, Any]) -> dict[str, Any]:
    """Conservative planning reserve, not a certified upper bound for uncapped SQLite files."""
    days = (
        date.fromisoformat(campaign["origins"]["end"])
        - date.fromisoformat(campaign["origins"]["start"])
    ).days + 1
    series = campaign["source"]["products"] * campaign["source"]["stores"]
    old_history_rows, old_feature_rows = 9100, 127400
    old_logical_bytes = 1952151135
    history_rows, feature_rows = series * days, series * days * 14
    ratio = (history_rows + feature_rows) / (old_history_rows + old_feature_rows)
    logical = math.ceil(old_logical_bytes * ratio)
    # Includes both point targets and a separately calibrated baseline interval. Components
    # are planning allowances, not new artifact limits and not measured peak usage.
    components = {
        "new_persistent_curated_features_predictions_reports": 3 * GIB,
        "uncompressed_feature_verification_index": math.ceil(logical * 1.5),
        "other_nested_indexes_source_quality_or_output": 2 * GIB,
        "sqlite_journal_and_sort_reserve": math.ceil(logical * 0.5),
        "sealed_source_and_replay_staging": GIB,
        "system_and_estimation_margin": 2 * GIB,
    }
    required = math.ceil(sum(components.values()) / GIB) * GIB
    return {
        "basis": "old_one_store_91_origins_measured_logical_bytes_scaled_to_full_two_store_100_origin_panel",
        "old_logical_bytes": old_logical_bytes,
        "old_history_rows": old_history_rows,
        "old_feature_rows": old_feature_rows,
        "planned_origins": days,
        "planned_series_upper_bound": series,
        "history_rows_upper_bound": history_rows,
        "feature_rows_upper_bound": feature_rows,
        "logical_feature_bytes_estimate": logical,
        "logical_feature_bytes_with_10_percent_uncertainty": math.ceil(logical * 1.1),
        "existing_logical_byte_limit": 4 * GIB,
        "logical_budget_risk": logical * 1.1 > 4 * GIB,
        "components_bytes": components,
        "minimum_free_bytes_before_full_run": required,
        "required_free_gib": required // GIB,
        "scope": "additional_free_space_existing_retained_artifacts_already_on_disk_not_deleted",
        "assumptions": [
            "one_sequential_campaign_no_parallel_model_runs_or_duplicate_exports",
            "workspace_and_system_temp_on_same_filesystem_check_both_at_start",
            "current_verify_inputs_stores_uncompressed_canonical_bodies_in_SQLite",
            "no_new_source_generation_no_full_source_copy_for_backup",
            "uncapped_indexes_make_this_a_planning_reserve_not_a_guaranteed_maximum",
            "stop_before_test_scoring_if_resource_budget_or_dual_forecast_contract_unresolved",
        ],
    }


def verify_freeze(path: Path = FREEZE) -> dict[str, Any]:
    frozen = json.loads(path.read_text())
    if frozen["protocol_version"] != "forecast-quality-2.0.0" or set(frozen["file_sha256"]) != set(
        FROZEN_FILES
    ):
        raise ValueError("quality_v2_freeze_inventory_or_version_mismatch")
    for relative, expected in frozen["file_sha256"].items():
        if digest(ROOT / relative) != expected:
            raise ValueError("quality_v2_freeze_mismatch: " + relative)
    return {
        "status": "passed",
        "protocol_version": frozen["protocol_version"],
        "files": len(frozen["file_sha256"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-retained", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("preflight_output_already_exists_use_new_receipt_path")
    campaign = json.loads(
        (ROOT / "contracts/forecast/v1/quality-remediation.campaign-v10.json").read_text()
    )
    estimate = estimate_space(campaign)
    free_by_path = {
        str(path): shutil.disk_usage(path).free for path in (ROOT, Path(tempfile.gettempdir()))
    }
    free = min(free_by_path.values())
    report = {
        "protocol_freeze": verify_freeze(),
        "retained": verify_retained(RETAINED) if args.verify_retained else "not_rechecked",
        "space_estimate": estimate,
        "observed_free_bytes": free,
        "observed_free_bytes_by_path": free_by_path,
        "disk_status": "passed"
        if free >= estimate["minimum_free_bytes_before_full_run"]
        else "not_ready",
        "full_campaign_status": "not_ready",
        "remaining_requirements": [
            "freeze_model_and_baselines_with_explicit_median_mean_and_central_interval_outputs",
            "verify_as_of_calibration_and_complete_critical_segment_inventory",
            "resolve_full_panel_logical_byte_budget_risk_without_dropping_rows",
        ],
        "new_data_generated": False,
        "new_test_outcomes_evaluated": False,
        "model_fits": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": report["full_campaign_status"],
                "disk_status": report["disk_status"],
                "required_free_gib": estimate["required_free_gib"],
                "output": str(args.output),
            }
        )
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
