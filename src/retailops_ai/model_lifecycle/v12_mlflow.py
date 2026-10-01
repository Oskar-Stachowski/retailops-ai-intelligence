"""Import complete v12 evidence into tracking; model registration is a separate operation."""

from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from retailops_ai.model_lifecycle.v12_evidence import (
    V12ArtifactReceipt,
    V12Evidence,
    projected_metrics,
)
from retailops_ai.source_snapshot.files import (
    checked_directory,
    regular_file,
    relative_path,
)

EXPERIMENT = "retailops/forecast-v12-campaign-evidence"
MAX_RESPONSE = 4 * 1024**2


class Tracking(Protocol):
    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def upload(self, path: str, root: Path, name: str, receipt: V12ArtifactReceipt) -> None: ...
    def checksum(self, path: str, maximum: int) -> tuple[int, str]: ...


class LocalTracking:
    """Streaming GET/PUT to the existing loopback MLflow; no SDK or Docker build needed."""

    def __init__(self, port: int = 5010) -> None:
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("v12_mlflow_loopback_port")
        self.port = port
        self.base = f"http://127.0.0.1:{port}"

    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - fixed loopback server
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                raw = response.read(MAX_RESPONSE + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ValueError("v12_mlflow_resource_missing") from None
            raise ValueError("v12_mlflow_api_failed") from None
        if len(raw) > MAX_RESPONSE:
            raise ValueError("v12_mlflow_response_budget")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("v12_mlflow_response_invalid")
        return result

    def upload(self, path: str, root: Path, name: str, receipt: V12ArtifactReceipt) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            connection.putrequest("PUT", path)
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(receipt.size_bytes))
            connection.endheaders()
            digest, count = hashlib.sha256(), 0
            with regular_file(root, name) as stream:
                while chunk := stream.read(1024**2):
                    count += len(chunk)
                    if count > receipt.size_bytes:
                        raise ValueError("v12_mlflow_source_changed")
                    digest.update(chunk)
                    connection.send(chunk)
            if (count, digest.hexdigest()) != (receipt.size_bytes, receipt.sha256):
                raise ValueError("v12_mlflow_source_changed")
            response = connection.getresponse()
            response.read(MAX_RESPONSE + 1)
            if response.status != 200:
                raise ValueError("v12_mlflow_upload_failed")
        finally:
            connection.close()

    def checksum(self, path: str, maximum: int) -> tuple[int, str]:
        request = urllib.request.Request(self.base + path)  # noqa: S310 - fixed loopback server
        digest, count = hashlib.sha256(), 0
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            while chunk := response.read(1024**2):
                count += len(chunk)
                if count > maximum:
                    raise ValueError("v12_mlflow_remote_byte_budget")
                digest.update(chunk)
        return count, digest.hexdigest()


def artifact_path(run: dict[str, Any], name: str) -> str:
    uri = run["info"]["artifact_uri"]
    if not isinstance(uri, str) or not uri.startswith("mlflow-artifacts:/"):
        raise ValueError("v12_mlflow_artifact_uri")
    base = uri.removeprefix("mlflow-artifacts:/").lstrip("/")
    relative_path(base)
    relative_path(name)
    escaped = "/".join(urllib.parse.quote(part, safe="") for part in (base + "/" + name).split("/"))
    return "/api/2.0/mlflow-artifacts/artifacts/" + escaped


def metadata(evidence: V12Evidence) -> dict[str, Any]:
    desc, freeze = evidence.manifest["descriptor"], evidence.freeze["descriptor"]
    params = {
        "original_run_id": evidence.run_id,
        "campaign_id": desc["campaign_id"],
        "freeze_id": desc["freeze_id"],
        "replay_id": desc["replay_id"],
        "ai_code_commit": desc["ai_code_commit"],
        "source_code_commit": freeze["remote_preparation"]["source_commit"],
        "code_sha256": desc["code"]["code_sha256"],
        "dependency_lock_sha256": desc["code"]["dependency_lock_sha256"],
        "run_manifest_sha256": evidence.manifest_receipt.sha256,
        "cohort_count": str(len(desc["checkpoints"])),
        "retained_bytes": str(desc["bytes"]),
        "artifact_files": str(len(evidence.files)),
        "source_format": desc["version"],
        "quality_protocol": freeze["quality_policy"]["version"],
        "target_type": "observed_sales_units",
        "median_objective": "mae",
        "mean_objective": "mse_with_bias_guard",
        "interval_objective": "central_interval_score",
    }
    tags = {
        "retailops.original_run_id": evidence.run_id,
        "retailops.import_kind": "v12_campaign_evidence",
        "retailops.exported_at": evidence.manifest["exported_at"],
        "retailops.original_model_status": desc["forecast_model_status"],
        "retailops.quality_status": desc["quality_qualification_status"],
        "retailops.independent_replay": "passed",
        "retailops.output_components": "median,mean,interval",
        "retailops.training_executed": "false",
        "retailops.registration_eligible": "false",
        "retailops.promotion_eligible": "false",
        "retailops.serving_eligible": "false",
        "retailops.portfolio_final_test": "not_included_not_opened",
    }
    return {"params": params, "tags": tags, "metrics": projected_metrics(evidence)}


def _experiment(client: Tracking, name: str = EXPERIMENT) -> str:
    try:
        response = client.api(
            "/api/2.0/mlflow/experiments/get-by-name?experiment_name="
            + urllib.parse.quote(name, safe="")
        )
        return str(response["experiment"]["experiment_id"])
    except ValueError as error:
        if str(error) != "v12_mlflow_resource_missing":
            raise
        return str(
            client.api("/api/2.0/mlflow/experiments/create", {"name": name})["experiment_id"]
        )


def _matches(client: Tracking, experiment: str, run_id: str) -> list[dict[str, Any]]:
    result, pages = [], set()
    token = None
    for _ in range(100):
        payload: dict[str, Any] = {"experiment_ids": [experiment], "max_results": 1000}
        if token:
            payload["page_token"] = token
        response = client.api("/api/2.0/mlflow/runs/search", payload)
        for run in response.get("runs", []):
            tags = {tag["key"]: tag["value"] for tag in run["data"].get("tags", [])}
            params = {param["key"]: param["value"] for param in run["data"].get("params", [])}
            if (
                tags.get("retailops.original_run_id") == run_id
                or params.get("original_run_id") == run_id
                or run["info"].get("run_name") == "v12-" + run_id
                or tags.get("mlflow.runName") == "v12-" + run_id
            ):
                result.append(run)
        token = response.get("next_page_token")
        if not token:
            return result
        if token in pages:
            break
        pages.add(token)
    raise ValueError("v12_mlflow_search_budget_or_cycle")


def _verify_remote(client: Tracking, run: dict[str, Any], evidence: V12Evidence) -> None:
    for name, receipt in evidence.files.items():
        if client.checksum(artifact_path(run, name), receipt.size_bytes) != (
            receipt.size_bytes,
            receipt.sha256,
        ):
            raise ValueError("v12_mlflow_remote_checksum")


def import_evidence(evidence: V12Evidence, client: Tracking, work: Path) -> dict[str, Any]:
    """Serialize local imports; stream every original artifact and reject ambiguous retries."""
    evidence.verify_bytes()
    if work.absolute().is_relative_to(evidence.root) or evidence.root.is_relative_to(
        work.absolute()
    ):
        raise ValueError("v12_mlflow_work_overlaps_export")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = checked_directory(work)
    if work.stat().st_mode & 0o077:
        raise ValueError("v12_mlflow_private_work_directory")
    fd = os.open(
        work / (evidence.run_id + ".lock"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("v12_mlflow_private_regular_lock_required")
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _import_locked(evidence, client)


def _import_locked(evidence: V12Evidence, client: Tracking) -> dict[str, Any]:
    record = metadata(evidence)
    experiment = _experiment(client)
    matches = _matches(client, experiment, evidence.run_id)
    if matches:
        if len(matches) != 1:
            raise ValueError("v12_mlflow_duplicate_imports")
        run = matches[0]
        tags = {t["key"]: t["value"] for t in run["data"].get("tags", [])}
        params = {p["key"]: p["value"] for p in run["data"].get("params", [])}
        metrics = {m["key"]: m["value"] for m in run["data"].get("metrics", [])}
        if (
            run["info"]["status"] != "FINISHED"
            or tags.get("retailops.import_status") != "verified"
            or any(tags.get(key) != value for key, value in record["tags"].items())
            or params != record["params"]
            or metrics != record["metrics"]
        ):
            raise ValueError("v12_mlflow_existing_import_conflict")
        _verify_remote(client, run, evidence)
        evidence.verify_bytes()
        return _result("already_imported", run, evidence)
    now = datetime.now(UTC)
    tags = record["tags"] | {
        "retailops.imported_at": now.isoformat(),
        "retailops.import_status": "uploading",
    }
    run = client.api(
        "/api/2.0/mlflow/runs/create",
        {
            "experiment_id": experiment,
            "run_name": "v12-" + evidence.run_id,
            "start_time": int(now.timestamp() * 1000),
            "tags": [{"key": key, "value": value} for key, value in tags.items()],
        },
    )["run"]
    run_id = run["info"]["run_id"]
    try:
        client.api(
            "/api/2.0/mlflow/runs/log-batch",
            {
                "run_id": run_id,
                "params": [{"key": key, "value": value} for key, value in record["params"].items()],
                "metrics": [
                    {
                        "key": key,
                        "value": value,
                        "timestamp": int(now.timestamp() * 1000),
                        "step": 0,
                    }
                    for key, value in record["metrics"].items()
                ],
                "tags": [],
            },
        )
        for name, receipt in evidence.files.items():
            client.upload(artifact_path(run, name), evidence.root, name, receipt)
            if client.checksum(artifact_path(run, name), receipt.size_bytes) != (
                receipt.size_bytes,
                receipt.sha256,
            ):
                raise ValueError("v12_mlflow_remote_checksum")
        evidence.verify_bytes()
        client.api(
            "/api/2.0/mlflow/runs/set-tag",
            {
                "run_id": run_id,
                "key": "retailops.import_status",
                "value": "verified",
            },
        )
        client.api(
            "/api/2.0/mlflow/runs/update",
            {
                "run_id": run_id,
                "status": "FINISHED",
                "end_time": int(datetime.now(UTC).timestamp() * 1000),
            },
        )
    except Exception:
        try:
            client.api(
                "/api/2.0/mlflow/runs/set-tag",
                {
                    "run_id": run_id,
                    "key": "retailops.import_status",
                    "value": "failed",
                },
            )
            client.api(
                "/api/2.0/mlflow/runs/update",
                {
                    "run_id": run_id,
                    "status": "FAILED",
                    "end_time": int(datetime.now(UTC).timestamp() * 1000),
                },
            )
        except Exception as cleanup_error:
            raise ValueError("v12_mlflow_failed_status_unknown") from cleanup_error
        raise
    return _result("imported", run, evidence)


def _result(status: str, run: dict[str, Any], evidence: V12Evidence) -> dict[str, Any]:
    return {
        "status": status,
        "original_run_id": evidence.run_id,
        "mlflow_run_id": run["info"]["run_id"],
        "forecast_model_status": evidence.handoff["forecast_model_status"],
        "quality_status": evidence.handoff["quality_qualification_status"],
        "artifact_files": len(evidence.files),
        "source_generation": False,
        "model_refits": 0,
        "serving_eligible": False,
    }
