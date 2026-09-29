"""Real local MLflow backup/restore: same run and artifact in a fresh project."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import local_stack as stack
import mlflow_store as store

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/evidence/05-01-local-store.json"


def api(
    path: str,
    *,
    data: dict[str, Any] | None = None,
    raw: bytes | None = None,
    method: str | None = None,
) -> tuple[int, bytes]:
    payload = json.dumps(data).encode() if data is not None else raw
    request = urllib.request.Request(  # noqa: S310 - fixed loopback endpoint
        "http://127.0.0.1:5010" + path,
        data=payload,
        headers={"Content-Type": "application/json" if data is not None else "text/plain"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return response.status, response.read()


def check_run(
    experiment: str, experiment_id: str, run_id: str, artifact_path: str, proof: bytes
) -> None:
    code, body = api("/api/2.0/mlflow/experiments/get?experiment_id=" + experiment_id)
    if code != 200 or json.loads(body)["experiment"]["name"] != experiment:
        raise ValueError("restored_experiment_mismatch")
    code, body = api("/api/2.0/mlflow/runs/get?run_id=" + run_id)
    if code != 200 or json.loads(body)["run"]["info"]["run_id"] != run_id:
        raise ValueError("restored_run_mismatch")
    if api(artifact_path)[1] != proof:
        raise ValueError("restored_artifact_mismatch")


def main() -> int:
    project = stack.project_name()
    target = "retailops_ai_restore_" + uuid.uuid4().hex[:10]
    experiment = "ai05-backup-" + uuid.uuid4().hex
    proof = b"retailops-ai05-local-backup-proof\n"
    source_stopped = False
    target_owned = False
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "scope": "real_local_mlflow_metadata_and_artifact_backup_restore",
        "source_project": project,
        "target_project": target,
        "checks": [],
    }
    try:
        if stack.main(["up"]) != 0:
            raise ValueError("source_stack_not_ready")
        docker = store.compose(target)[0]
        if any(
            store.checked_run(command).strip()
            for command in (
                store.compose(target, "ps", "-a", "-q"),
                [
                    docker,
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={target}",
                ],
                [
                    docker,
                    "network",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={target}",
                ],
            )
        ):
            raise ValueError("test_target_project_already_exists")
        target_owned = True
        code, body = api("/api/2.0/mlflow/experiments/create", data={"name": experiment})
        if code != 200:
            raise ValueError("experiment_creation_failed")
        experiment_id = json.loads(body)["experiment_id"]
        code, body = api(
            "/api/2.0/mlflow/runs/create",
            data={"experiment_id": experiment_id, "start_time": int(time.time() * 1000)},
        )
        if code != 200:
            raise ValueError("run_creation_failed")
        info = json.loads(body)["run"]["info"]
        run_id = info["run_id"]
        uri = info["artifact_uri"]
        if not isinstance(uri, str) or not uri.startswith("mlflow-artifacts:/"):
            raise ValueError("artifact_uri_invalid")
        artifact_path = (
            "/api/2.0/mlflow-artifacts/artifacts/"
            + uri.removeprefix("mlflow-artifacts:/").lstrip("/")
            + "/proof.txt"
        )
        if api(artifact_path, raw=proof, method="PUT")[0] != 200:
            raise ValueError("artifact_upload_failed")
        check_run(experiment, experiment_id, run_id, artifact_path, proof)
        report["checks"].append("source_run_and_artifact_persisted")

        bundle = store.backup()
        manifest = store.verify_bundle(bundle)
        if manifest["artifact_files"] < 1:
            raise ValueError("backup_missing_artifact")
        report["backup_id"] = manifest["backup_id"]
        report["metadata_dump_sha256"] = manifest["files"][store.DB_DUMP]["sha256"]
        report["metadata_dump_bytes"] = manifest["files"][store.DB_DUMP]["size_bytes"]
        report["artifact_archive_sha256"] = manifest["files"][store.ARTIFACTS]["sha256"]
        report["artifact_archive_bytes"] = manifest["files"][store.ARTIFACTS]["size_bytes"]
        report["artifact_files"] = manifest["artifact_files"]
        report["artifact_bytes"] = manifest["artifact_bytes"]
        report["compose_sha256"] = manifest["compose_sha256"]
        report["dockerfile_sha256"] = manifest["dockerfile_sha256"]
        report["checks"].append("offline_consistent_bundle_verified")
        check_run(experiment, experiment_id, run_id, artifact_path, proof)
        report["checks"].append("source_recovered_after_backup")

        restored = store.restore(bundle, target)
        if restored["artifact_files"] != manifest["artifact_files"]:
            raise ValueError("restored_artifact_count_mismatch")
        report["checks"].append("fresh_target_restore_metadata_and_volume")
        store.checked_run(store.compose(project, "stop", "mlflow"))
        source_stopped = True
        store.checked_run(store.compose(target, "up", "-d", "--wait", "mlflow"))
        check_run(experiment, experiment_id, run_id, artifact_path, proof)
        report["checks"].append("restored_run_and_artifact_read_through_mlflow_http")
        store.checked_run(store.compose(target, "down", "-v"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "mlflow"))
        source_stopped = False
        check_run(experiment, experiment_id, run_id, artifact_path, proof)
        report["checks"].append("source_unchanged_after_isolated_restore")
        report["status"] = "passed"
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "passed", "report": str(REPORT)}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, TimeoutError):
        print('{"error":"mlflow_store_smoke_failed"}')
        return 1
    finally:
        try:
            if target_owned:
                store.checked_run(store.compose(target, "down", "-v"))
            if source_stopped:
                store.checked_run(store.compose(project, "up", "-d", "--wait", "mlflow"))
            stack.main(["down"])
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
