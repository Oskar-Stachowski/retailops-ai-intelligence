"""Verify the owner's artifact-specific AI04 acceptance without changing quality gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "docs/evidence/04-v12-acceptance.json"
EVIDENCE = ROOT / "docs/evidence/04-v12-final"
RUN_ID = (
    "functional-v12-run-sha256-345a725d435a477374292cb9483350fb5c50c8ba87d06668c727e0a9f964fb6b"
)
METRICS_SHA256 = "ba376ed1ef5cff27e2d43e1d255b2296b5b26ac878617b1d5e88f7624e743558"
RUN_SHA256 = "29bf6837ca2cb7504213168743239b037041f375763bc8f01ae28c1f6d09b26e"
FILES = {
    "completion.json",
    "metrics.json",
    "run_manifest.json",
    "replay_receipt.json",
    "detached-verification.json",
    "export-receipt.json",
}


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def verify_acceptance(decision: dict[str, Any], evidence: Path) -> dict[str, Any]:
    """An exception accepts only this complete v12 result, never a future campaign."""
    expected = {
        "version": "ai04-stage-acceptance-1.0.0",
        "stage": "AI 04",
        "stage_status": "ready",
        "scope": "development_stage_final_deliverable",
        "selected_version": "v12",
        "acceptance_basis": "explicit_owner_acceptance_of_enumerated_deviations",
        "authorized_by": "project_owner",
        "original_quality_status": "not_ready",
        "original_model_status": "not_ready",
        "original_segment_counts": {"passed": 221, "failed": 3},
        "run_id": RUN_ID,
        "effective_on": "merge_of_acceptance_commit_through_protected_main_and_successful_required_ci",
    }
    for name, value in expected.items():
        require(decision.get(name) == value, f"acceptance_scope_or_identity_changed:{name}")
    for name in (
        "changes_quality_protocol",
        "changes_original_artifacts",
        "statistical_insignificance_established",
        "business_impact_validated",
        "production_deployment_authorized",
        "registry_promotion_authorized",
        "portfolio_final_test_opened",
    ):
        require(decision.get(name) is False, f"acceptance_exceeds_authorization:{name}")
    require(bool(decision.get("authorized_at")), "owner_authorization_time_missing")
    require(
        decision.get("authorization_text") == "doprowadź AI 04 do ready. uznajemy V12 za finalne",
        "owner_authorization_missing",
    )
    require(set(decision["evidence"]) == FILES, "evidence_inventory_changed")
    bodies: dict[str, Any] = {}
    for name in FILES:
        path = evidence / name
        require(path.is_file() and not path.is_symlink(), f"evidence_not_regular:{name}")
        raw = path.read_bytes()
        require(
            decision["evidence"][name]
            == {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)},
            f"evidence_checksum_mismatch:{name}",
        )
        bodies[name] = json.loads(raw)
    require(
        decision["evidence"]["metrics.json"]["sha256"] == METRICS_SHA256, "original_metrics_changed"
    )
    require(
        decision["evidence"]["run_manifest.json"]["sha256"] == RUN_SHA256, "original_run_changed"
    )
    metrics, completion = bodies["metrics.json"], bodies["completion.json"]
    run, replay = bodies["run_manifest.json"], bodies["replay_receipt.json"]
    detached, export = bodies["detached-verification.json"], bodies["export-receipt.json"]
    desc, replay_desc = run["descriptor"], replay["descriptor"]
    for body in (completion, desc, replay_desc, detached):
        for name in ("campaign_id", "freeze_id"):
            require(body[name] == decision[name], f"evidence_identity_mismatch:{name}")
        require(body["forecast_model_status"] == "not_ready", "original_quality_reclassified")
    for body in (completion, run, detached, export):
        require(body["run_id"] == RUN_ID, "run_identity_mismatch")
    for body in (desc, replay, detached):
        require(body["replay_id"] == decision["replay_id"], "replay_identity_mismatch")
    require(
        completion["execution_status"] == "complete"
        and completion["all_64_cohorts_included"] is True,
        "campaign_incomplete",
    )
    require(
        metrics["input_complete"] is True
        and metrics["execution_failure"] is None
        and len(metrics["cohorts"]) == 64
        and metrics["processed_rows"] == 27396096,
        "evaluation_incomplete",
    )
    require(
        len(metrics["segments"]) == 224
        and Counter(s["status"] for s in metrics["segments"]) == {"passed": 221, "failed": 3}
        and metrics["segment_counts"] == decision["original_segment_counts"]
        and metrics["failed_reasons"] == {"mean_mse_regression": 3}
        and metrics["status"] == "not_ready",
        "quality_result_changed",
    )
    deviations = []
    for segment in metrics["segments"]:
        require(not segment["not_ready_reasons"], "unaccepted_missing_quality_evidence")
        if not segment["failed_reasons"]:
            continue
        baseline, candidate = (
            segment["baseline"]["mean"]["mse"],
            segment["candidate"]["mean"]["mse"],
        )
        deviations.append(
            {
                **{
                    key: segment[key]
                    for key in ("fold", "role", "dimension", "value", "failed_reasons")
                },
                "baseline_mse": baseline,
                "candidate_mse": candidate,
                "relative_mse_regression_pct": 100 * (candidate / baseline - 1),
            }
        )
    require(decision["accepted_deviations"] == deviations, "accepted_deviations_do_not_match")
    require(
        replay_desc["status"] == "passed"
        and replay_desc["refit_calls"] == 0
        and replay_desc["new_qualification"] is False
        and replay_desc["all_preregistered_cohorts_included"] is True
        and replay_desc["metrics_sha256"] == METRICS_SHA256,
        "independent_replay_invalid",
    )
    require(
        detached["status"] == "passed"
        and detached["pythonpath_unset"] is True
        and detached["new_fits"] == 0
        and detached["new_qualification"] is False
        and detached["run_manifest_sha256"] == RUN_SHA256,
        "detached_verification_invalid",
    )
    require(
        len(desc["files"]) == detached["file_count"] == 663
        and desc["bytes"] == detached["retained_bytes"] == 31994594655
        and len(desc["checkpoints"]) == 64
        and export["manifest"]["sha256"] == RUN_SHA256,
        "durable_export_incomplete",
    )
    return {
        "acceptance_record_status": "passed",
        "stage_status_after_protected_publication": "ready",
        "selected_version": "v12",
        "run_id": RUN_ID,
        "original_quality_status": "not_ready",
        "accepted_deviations": len(deviations),
        "production_deployment_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision", type=Path, default=DECISION)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    print(json.dumps(verify_acceptance(json.loads(args.decision.read_bytes()), args.evidence)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
