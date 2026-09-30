"""Review AI 04 evidence and audit a rejected forecast candidate in local MLflow."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import mlflow_evidence as tracking

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.run_contract import ForecastRunManifest
from retailops_ai.security.model_operator import model_operator

MODEL = "retailops-demand-forecast"
DECISIONS = "retailops/model-decisions"
WORK = tracking.ROOT / ".local" / "mlflow-decisions"
MAX_REPORT = 1024**2
DECISION_ID = re.compile(r"^decision-[a-z0-9-]{8,64}$")
RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def values(rows: list[dict[str, str]]) -> dict[str, str]:
    result = {row["key"]: row["value"] for row in rows}
    if len(result) != len(rows):
        raise ValueError("duplicate_mlflow_field")
    return result


def run(run_id: str) -> dict[str, Any]:
    if RUN_ID.fullmatch(run_id) is None:
        raise ValueError("invalid_mlflow_run_id")
    value = tracking.api("/api/2.0/mlflow/runs/get?run_id=" + run_id)["run"]
    if not isinstance(value, dict):
        raise ValueError("invalid_mlflow_run")
    return value


def artifact(source: dict[str, Any], name: str) -> bytes:
    path = tracking.artifact_path(source, name)
    request = urllib.request.Request(tracking.BASE + path)  # noqa: S310 - fixed loopback
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        raw = response.read(MAX_REPORT + 1)
    if len(raw) > MAX_REPORT:
        raise ValueError("registry_report_too_large")
    return bytes(raw)


def review(run_id: str) -> dict[str, Any]:
    source = run(run_id)
    tags = values(source["data"].get("tags", []))
    params = values(source["data"].get("params", []))
    if (
        source["info"]["status"] != "FINISHED"
        or tags.get("retailops.import_kind") != "historical_evidence"
        or tags.get("retailops.import_status") != "verified"
        or tags.get("retailops.run_kind") != "forecast_evidence_export"
        or tags.get("retailops.registration_eligible") != "false"
        or tags.get("retailops.promotion_eligible") != "false"
        or tags.get("retailops.serving_eligible") != "false"
    ):
        raise ValueError("registry_evidence_not_verified")
    raw_manifest = artifact(source, "reports/run_manifest.json")
    manifest = ForecastRunManifest.model_validate_json(raw_manifest)
    descriptor = manifest.descriptor
    expected_params = {
        "source_dataset_id": descriptor.source_dataset_id,
        "curated_dataset_id": descriptor.curated_dataset_id,
        "feature_set_id": descriptor.feature_set_id,
        "label_dataset_id": descriptor.label_dataset_id,
        "split_id": descriptor.split_id,
        "backtest_id": descriptor.backtest_id,
        "quality_id": descriptor.quality_id,
        "source_code_commit": descriptor.source_code_commit,
        "ai_code_commit": descriptor.ai_code_commit,
        "dependency_lock_sha256": descriptor.dependency_lock_sha256,
        "data_seed": str(descriptor.data_seed),
        "model_seed": str(descriptor.model_seed),
    }
    if (
        hashlib.sha256(raw_manifest).hexdigest() != params.get("run_manifest_sha256")
        or any(params.get(key) != value for key, value in expected_params.items())
        or tags["retailops.original_run_id"] != manifest.run_id
        or tags["retailops.quality_status"] != manifest.descriptor.quality_status
        or tags["retailops.export_started_at"] != manifest.started_at.isoformat()
        or tags["retailops.export_completed_at"] != manifest.completed_at.isoformat()
    ):
        raise ValueError("registry_manifest_binding_mismatch")
    reports: dict[str, Any] = {}
    report_bytes: dict[str, bytes] = {}
    for name in (
        "config.json",
        "metrics.json",
        "handoff.json",
        "model_card.json",
        "signature.json",
        "input_example.json",
    ):
        raw = artifact(source, "reports/" + name)
        receipt = manifest.receipts[name]
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
            raise ValueError("registry_report_checksum_mismatch")
        report_bytes[name] = raw
        reports[name] = json.loads(raw)
    metrics, handoff = reports["metrics.json"], reports["handoff.json"]
    if (
        hashlib.sha256(report_bytes["config.json"]).hexdigest() != params.get("config_sha256")
        or metrics["quality_status"] != manifest.descriptor.quality_status
        or metrics["gate_counts"] != manifest.descriptor.gate_counts
        or handoff["original_run_id"] != manifest.run_id
        or handoff["training_executed_in_this_run"] is not False
        or handoff["training_run_id"] is not None
        or any(
            handoff[key] is not False
            for key in ("registration_eligible", "promotion_eligible", "serving_eligible")
        )
        or tracking.remote_hash(tracking.artifact_path(source))
        != (int(params["archive_size_bytes"]), params["archive_sha256"])
    ):
        raise ValueError("registry_evidence_binding_mismatch")
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "model_name": MODEL,
        "model_version": None,
        "mlflow_run_id": run_id,
        "evidence_id": manifest.run_id,
        "run_manifest_sha256": params["run_manifest_sha256"],
        "archive_sha256": params["archive_sha256"],
        "quality_status": manifest.descriptor.quality_status,
        "gate_counts": manifest.descriptor.gate_counts,
        "eligible_for_registration": False,
        "eligible_for_promotion": False,
        "reasons": [
            "historical_export_is_not_a_training_run",
            "quality_gates_not_passed",
            "portfolio_final_test_not_opened",
            "no_qualified_load_tested_model",
        ],
    }
    result["review_id"] = "model-review-sha256-" + canonical_sha256(result)
    return result


def principal(policy: Path, credentials: Path) -> str:
    return model_operator(policy, credentials).principal_id


def registry() -> dict[str, Any]:
    query = urllib.parse.quote(MODEL, safe="")
    try:
        model = tracking.api("/api/2.0/mlflow/registered-models/get?name=" + query)[
            "registered_model"
        ]
    except ValueError as exc:
        if str(exc) != "mlflow_resource_missing":
            raise
        model = tracking.api(
            "/api/2.0/mlflow/registered-models/create",
            {
                "name": MODEL,
                "description": "Observed-sales forecast; no serving version until qualification.",
                "tags": [{"key": "retailops.lifecycle", "value": "awaiting_qualified_version"}],
            },
        )["registered_model"]
    if not isinstance(model, dict):
        raise ValueError("invalid_registered_model")
    if model["name"] != MODEL:
        raise ValueError("invalid_registered_model_name")
    return model


def experiment() -> str:
    query = urllib.parse.quote(DECISIONS, safe="")
    try:
        value = tracking.api("/api/2.0/mlflow/experiments/get-by-name?experiment_name=" + query)
    except ValueError as exc:
        if str(exc) != "mlflow_resource_missing":
            raise
        return str(
            tracking.api("/api/2.0/mlflow/experiments/create", {"name": DECISIONS})["experiment_id"]
        )
    return str(value["experiment"]["experiment_id"])


def prior_decisions(experiment_id: str, decision_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        request: dict[str, Any] = {"experiment_ids": [experiment_id], "max_results": 1000}
        if page:
            request["page_token"] = page
        response = tracking.api("/api/2.0/mlflow/runs/search", request)
        found.extend(
            item
            for item in response.get("runs", [])
            if values(item["data"].get("tags", [])).get("retailops.decision_id") == decision_id
        )
        page = response.get("next_page_token")
        if not page:
            return found


def source_tag(source: dict[str, Any], decision_id: str) -> None:
    tags = values(source["data"].get("tags", []))
    previous = tags.get("retailops.rejection_decision_id")
    if previous not in {None, decision_id}:
        raise ValueError("registry_source_already_decided")
    if previous is None:
        tracking.api(
            "/api/2.0/mlflow/runs/set-tag",
            {
                "run_id": source["info"]["run_id"],
                "key": "retailops.rejection_decision_id",
                "value": decision_id,
            },
        )


def reject(
    run_id: str, decision_id: str, reason: str, policy: Path, credentials: Path
) -> dict[str, Any]:
    if DECISION_ID.fullmatch(decision_id) is None or not 12 <= len(reason.strip()) <= 500:
        raise ValueError("invalid_registry_decision_input")
    actor = principal(policy, credentials)
    review_result = review(run_id)
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    if WORK.is_symlink() or WORK.stat().st_mode & 0o077:
        raise ValueError("registry_private_workdir_required")
    fd = os.open(WORK / "reject.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry()
        experiment_id = experiment()
        decision: dict[str, Any] = {
            "schema_version": "1.0.0",
            "decision_id": decision_id,
            "action": "reject",
            "model_name": MODEL,
            "model_version": None,
            "evidence_id": review_result["evidence_id"],
            "mlflow_run_id": run_id,
            "review_id": review_result["review_id"],
            "principal": actor,
            "reason": reason.strip(),
        }
        digest = canonical_sha256(decision)
        matches = prior_decisions(experiment_id, decision_id)
        if matches:
            if len(matches) != 1:
                raise ValueError("registry_duplicate_decision_id")
            previous = matches[0]
            tags = values(previous["data"].get("tags", []))
            if (
                previous["info"]["status"] != "FINISHED"
                or tags.get("retailops.decision_sha256") != digest
                or tags.get("retailops.phase") != "verified"
                or json.loads(artifact(previous, "decision.json"))
                != {**decision, "decision_sha256": digest, "review": review_result}
            ):
                raise ValueError("registry_decision_conflict_or_incomplete")
            source_tag(run(run_id), decision_id)
            return {
                "status": "already_rejected",
                "decision_id": decision_id,
                "review_id": review_result["review_id"],
                "audit_run_id": previous["info"]["run_id"],
            }
        source = run(run_id)
        if values(source["data"].get("tags", [])).get("retailops.rejection_decision_id"):
            raise ValueError("registry_source_already_decided")
        created = tracking.api(
            "/api/2.0/mlflow/runs/create",
            {
                "experiment_id": experiment_id,
                "run_name": decision_id,
                "start_time": int(datetime.now(UTC).timestamp() * 1000),
                "tags": [
                    {"key": "retailops.decision_id", "value": decision_id},
                    {"key": "retailops.decision_sha256", "value": digest},
                    {"key": "retailops.action", "value": "reject"},
                    {"key": "retailops.phase", "value": "recording"},
                ],
            },
        )["run"]
        audit_id = created["info"]["run_id"]
        with tempfile.TemporaryDirectory(prefix=".decision-", dir=WORK) as temporary:
            path = Path(temporary) / "decision.json"
            path.write_text(
                json.dumps(
                    {**decision, "decision_sha256": digest, "review": review_result},
                    sort_keys=True,
                )
                + "\n"
            )
            tracking.upload(tracking.artifact_path(created, "decision.json"), path)
            if tracking.remote_hash(tracking.artifact_path(created, "decision.json")) != (
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ):
                raise ValueError("registry_decision_artifact_mismatch")
        tracking.api(
            "/api/2.0/mlflow/runs/set-tag",
            {"run_id": audit_id, "key": "retailops.phase", "value": "verified"},
        )
        tracking.api(
            "/api/2.0/mlflow/runs/update",
            {
                "run_id": audit_id,
                "status": "FINISHED",
                "end_time": int(datetime.now(UTC).timestamp() * 1000),
            },
        )
        # A complete audit run can be replayed to reconcile this source tag.
        source_tag(source, decision_id)
        return {
            "status": "rejected",
            "decision_id": decision_id,
            "review_id": review_result["review_id"],
            "audit_run_id": audit_id,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("review")
    check.add_argument("--run-id", required=True)
    change = sub.add_parser("reject")
    change.add_argument("--run-id", required=True)
    change.add_argument("--decision-id", required=True)
    change.add_argument("--reason", required=True)
    change.add_argument("--policy-file", type=Path, required=True)
    change.add_argument("--credentials-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (
            review(args.run_id)
            if args.command == "review"
            else reject(
                args.run_id, args.decision_id, args.reason, args.policy_file, args.credentials_file
            )
        )
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, json.JSONDecodeError):
        print('{"error":"mlflow_registry_operation_failed"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
