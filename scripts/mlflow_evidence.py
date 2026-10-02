"""Import a verified AI 04.8 archive as historical, non-serving MLflow evidence."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import http.client
import json
import os
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.run import REPORTS, verify_run
from retailops_ai.source_snapshot.files import SnapshotError

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".local" / "mlflow-imports"
EXPERIMENT = "retailops/forecast-historical-evidence"
BASE = "http://127.0.0.1:5010"
ARTIFACT = "historical-evidence.tar.gz"
MAX_RESPONSE = 1024**2


def api(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(  # noqa: S310 - fixed loopback server
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("mlflow_resource_missing") from None
        raise ValueError("mlflow_api_failed") from None
    if len(raw) > MAX_RESPONSE:
        raise ValueError("mlflow_response_too_large")
    value = json.loads(raw or b"{}")
    if not isinstance(value, dict):
        raise ValueError("mlflow_response_invalid")
    return value


def experiment_id() -> str:
    name = urllib.parse.quote(EXPERIMENT, safe="")
    try:
        value = api("/api/2.0/mlflow/experiments/get-by-name?experiment_name=" + name)
    except ValueError as exc:
        if str(exc) != "mlflow_resource_missing":
            raise
        value = api("/api/2.0/mlflow/experiments/create", {"name": EXPERIMENT})
        return str(value["experiment_id"])
    return str(value["experiment"]["experiment_id"])


def matching_runs(experiment: str, original_id: str) -> list[dict[str, Any]]:
    page: str | None = None
    found: list[dict[str, Any]] = []
    while True:
        request: dict[str, Any] = {"experiment_ids": [experiment], "max_results": 1000}
        if page:
            request["page_token"] = page
        response = api("/api/2.0/mlflow/runs/search", request)
        for run in response.get("runs", []):
            tags = {t["key"]: t["value"] for t in run["data"].get("tags", [])}
            if tags.get("retailops.original_run_id") == original_id:
                found.append(run)
        page = response.get("next_page_token")
        if not page:
            return found


def archive_run(source: Path, output: Path, names: list[str]) -> tuple[int, str]:
    with output.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for name in names:
                    path = source / name
                    info = tarfile.TarInfo(name)
                    info.size = path.stat().st_size
                    info.mode = 0o600
                    info.mtime = 0
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
    sha = hashlib.sha256()
    with output.open("rb") as stream:
        while block := stream.read(1024**2):
            sha.update(block)
    return output.stat().st_size, sha.hexdigest()


def artifact_path(run: dict[str, Any], name: str = ARTIFACT) -> str:
    uri = run["info"]["artifact_uri"]
    if not isinstance(uri, str) or not uri.startswith("mlflow-artifacts:/"):
        raise ValueError("mlflow_artifact_uri_invalid")
    base = uri.removeprefix("mlflow-artifacts:/").lstrip("/")
    if not base or any(part in {"", ".", ".."} for part in base.split("/")):
        raise ValueError("mlflow_artifact_uri_invalid")
    return "/api/2.0/mlflow-artifacts/artifacts/" + base + "/" + name


def upload(path: str, source: Path) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", 5010, timeout=120)
    try:
        connection.putrequest("PUT", path)
        connection.putheader("Content-Type", "application/octet-stream")
        connection.putheader("Content-Length", str(source.stat().st_size))
        connection.endheaders()
        with source.open("rb") as stream:
            while block := stream.read(1024**2):
                connection.send(block)
        response = connection.getresponse()
        response.read(MAX_RESPONSE + 1)
        if response.status != 200:
            raise ValueError("mlflow_artifact_upload_failed")
    finally:
        connection.close()


def remote_hash(path: str) -> tuple[int, str]:
    request = urllib.request.Request(BASE + path)  # noqa: S310 - fixed loopback server
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        while block := response.read(1024**2):
            total += len(block)
            digest.update(block)
    return total, digest.hexdigest()


def record(source: Path, size: int, sha: str) -> dict[str, Any]:
    manifest = verify_run(source)
    descriptor = manifest.descriptor
    source_manifest = (source / "run_manifest.json").read_bytes()
    metrics = json.loads((source / "metrics.json").read_bytes())
    config = (source / "config.json").read_bytes()
    handoff = json.loads((source / "handoff.json").read_bytes())
    if (
        handoff["original_run_id"] != manifest.run_id
        or handoff["run_kind"] != descriptor.kind
        or handoff["quality_status"] != descriptor.quality_status
        or any(
            handoff[field] is not False
            for field in ("registration_eligible", "promotion_eligible", "serving_eligible")
        )
        or metrics["quality_status"] != descriptor.quality_status
        or metrics["gate_counts"] != descriptor.gate_counts
    ):
        raise ValueError("mlflow_import_handoff_mismatch")
    params = {
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
        "config_sha256": hashlib.sha256(config).hexdigest(),
        "run_manifest_sha256": hashlib.sha256(source_manifest).hexdigest(),
        "archive_sha256": sha,
        "archive_size_bytes": str(size),
    }
    tags = {
        "retailops.original_run_id": manifest.run_id,
        "retailops.run_kind": descriptor.kind,
        "retailops.import_kind": "historical_evidence",
        "retailops.export_started_at": manifest.started_at.isoformat(),
        "retailops.export_completed_at": manifest.completed_at.isoformat(),
        "retailops.original_run_status": manifest.run_status,
        "retailops.quality_status": descriptor.quality_status,
        "retailops.model_status": manifest.forecast_model_status,
        "retailops.training_executed": "false",
        "retailops.registration_eligible": "false",
        "retailops.promotion_eligible": "false",
        "retailops.serving_eligible": "false",
        "retailops.imported_at": datetime.now(UTC).isoformat(),
        "retailops.import_status": "uploading",
    }
    scalar_metrics = [
        {
            "key": f"gate_{name}",
            "value": count,
            "timestamp": int(datetime.now(UTC).timestamp() * 1000),
            "step": 0,
        }
        for name, count in sorted(descriptor.gate_counts.items())
    ]
    for group, result in sorted(metrics["pooled_metrics"].items()):
        role, family = group.split(":", 1)
        for name in ("mae", "wape"):
            if (
                result["status" if name == "mae" else "wape_status"] == "passed"
                and result[name] is not None
            ):
                scalar_metrics.append(
                    {
                        "key": f"{role}_{family}_{name}",
                        "value": result[name],
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "step": 0,
                    }
                )
    return {"params": params, "tags": tags, "metrics": scalar_metrics}


def verify_remote_reports(run: dict[str, Any], source: Path) -> None:
    for name in (*REPORTS, "run_manifest.json"):
        path = source / name
        expected = (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        if remote_hash(artifact_path(run, "reports/" + name)) != expected:
            raise ValueError("mlflow_import_report_checksum_mismatch")


def import_run(source: Path) -> dict[str, Any]:
    manifest = verify_run(source)
    WORK.mkdir(parents=True, exist_ok=True, mode=0o700)
    if WORK.stat().st_mode & 0o077:
        raise ValueError("mlflow_import_directory_permissions")
    lock = WORK / (manifest.run_id + ".lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".import-", dir=WORK) as temporary:
            archive = Path(temporary) / ARTIFACT
            names = sorted([*manifest.receipts, "run_manifest.json"])
            size, sha = archive_run(source, archive, names)
            metadata = record(source, size, sha)
            experiment = experiment_id()
            matches = matching_runs(experiment, manifest.run_id)
            if matches:
                if len(matches) != 1:
                    raise ValueError("mlflow_import_identity_conflict")
                run = matches[0]
                tags = {t["key"]: t["value"] for t in run["data"].get("tags", [])}
                params = {p["key"]: p["value"] for p in run["data"].get("params", [])}
                if (
                    run["info"]["status"] != "FINISHED"
                    or tags.get("retailops.import_status") != "verified"
                    or params != metadata["params"]
                    or remote_hash(artifact_path(run)) != (size, sha)
                ):
                    raise ValueError("mlflow_import_existing_run_conflict")
                verify_remote_reports(run, source)
                return {
                    "status": "already_imported",
                    "original_run_id": manifest.run_id,
                    "mlflow_run_id": run["info"]["run_id"],
                    "archive_sha256": sha,
                    "archive_bytes": size,
                }
            created = api(
                "/api/2.0/mlflow/runs/create",
                {
                    "experiment_id": experiment,
                    "run_name": "historical-" + manifest.run_id,
                    "start_time": int(datetime.now(UTC).timestamp() * 1000),
                    "tags": [{"key": k, "value": v} for k, v in metadata["tags"].items()],
                },
            )["run"]
            run_id = created["info"]["run_id"]
            try:
                api(
                    "/api/2.0/mlflow/runs/log-batch",
                    {
                        "run_id": run_id,
                        "params": [{"key": k, "value": v} for k, v in metadata["params"].items()],
                        "metrics": metadata["metrics"],
                        "tags": [],
                    },
                )
                upload(artifact_path(created), archive)
                if remote_hash(artifact_path(created)) != (size, sha):
                    raise ValueError("mlflow_import_remote_checksum_mismatch")
                for name in (*REPORTS, "run_manifest.json"):
                    upload(artifact_path(created, "reports/" + name), source / name)
                verify_remote_reports(created, source)
                api(
                    "/api/2.0/mlflow/runs/set-tag",
                    {"run_id": run_id, "key": "retailops.import_status", "value": "verified"},
                )
                api(
                    "/api/2.0/mlflow/runs/update",
                    {
                        "run_id": run_id,
                        "status": "FINISHED",
                        "end_time": int(datetime.now(UTC).timestamp() * 1000),
                    },
                )
            except Exception:
                try:
                    api(
                        "/api/2.0/mlflow/runs/set-tag",
                        {"run_id": run_id, "key": "retailops.import_status", "value": "failed"},
                    )
                    api(
                        "/api/2.0/mlflow/runs/update",
                        {
                            "run_id": run_id,
                            "status": "FAILED",
                            "end_time": int(datetime.now(UTC).timestamp() * 1000),
                        },
                    )
                except Exception as cleanup_error:
                    raise ValueError("mlflow_import_failed_status_unknown") from cleanup_error
                raise
            return {
                "status": "imported",
                "original_run_id": manifest.run_id,
                "mlflow_run_id": run_id,
                "archive_sha256": sha,
                "archive_bytes": size,
                "artifact_files": len(names),
                "quality_status": manifest.descriptor.quality_status,
            }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = import_run(args.run_dir)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        SnapshotError,
        urllib.error.URLError,
        http.client.HTTPException,
        json.JSONDecodeError,
    ):
        print('{"error":"mlflow_historical_import_failed"}')
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
