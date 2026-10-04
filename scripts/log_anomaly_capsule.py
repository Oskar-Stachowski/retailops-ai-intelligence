"""Upload a sealed, independently qualified anomaly capsule to trusted local MLflow."""

import argparse
import hashlib
import http.client
import json
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_evaluation.verification import verify_quality
from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL, Qualification
from retailops_ai.anomaly_portfolio.model import load
from retailops_ai.anomaly_portfolio.registry import AnomalyRegistry
from retailops_ai.source_snapshot.files import canonical_json, decode_json, read_bytes

EXPERIMENT = "retailops/anomaly-portfolio"


def log(capsule: Path, registry: AnomalyRegistry) -> dict[str, Any]:
    raw = read_bytes(capsule, "qualification.json", 8 * 1024**2)
    qualification = Qualification.model_validate_json(raw)
    if raw != canonical_json(qualification.model_dump(mode="json")) + b"\n":
        raise ValueError("anomaly_log_noncanonical_qualification")
    digest = hashlib.sha256(raw).hexdigest()
    artifacts = {"qualification.json": raw}
    for name, receipt in (
        ("model.json", qualification.model),
        ("config.json", qualification.config),
        ("signature.json", qualification.signature),
        ("input_example.json", qualification.input_example),
        *(("gate_" + k + ".json", g.report) for k, g in qualification.gates.items()),
    ):
        raw = read_bytes(capsule, name, 8 * 1024**2)
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
            raise ValueError("anomaly_log_artifact_checksum")
        artifacts[name] = raw
    AnomalyRegistry.smoke(qualification, artifacts)
    final = decode_json(artifacts["gate_segments.json"])
    model = load(capsule / "model.json", qualification.model.sha256)
    for name in final["evaluation_inputs"]:
        artifacts[name] = read_bytes(capsule, name, 8 * 1024**2)
    quality = verify_quality(
        final,
        decode_json(artifacts["config.json"]),
        lambda name: artifacts[name],
        model.detector_id,
        qualification.model_family,
        model,
    )
    artifacts["model_card.json"] = read_bytes(capsule, "model_card.json", 8 * 1024**2)
    try:
        experiment = registry.api(
            "experiments/get-by-name?" + urllib.parse.urlencode({"experiment_name": EXPERIMENT})
        )["experiment"]["experiment_id"]
    except ValueError as exc:
        if str(exc) != "mlflow_resource_missing":
            raise
        experiment = registry.api("experiments/create", {"name": EXPERIMENT})["experiment_id"]
    matches = registry.api(
        "runs/search",
        {
            "experiment_ids": [str(experiment)],
            "filter": "tags.`retailops.anomaly_qualification_sha256` = '" + digest + "'",
            "max_results": 2,
        },
    ).get("runs", [])
    if matches:
        if len(matches) != 1 or matches[0]["info"]["status"] != "FINISHED":
            raise ValueError("anomaly_log_existing_run_requires_review")
        run_id = matches[0]["info"]["run_id"]
        registry.source(run_id, digest, MODEL)
        return {"status": "already_logged", "mlflow_run_id": run_id, "qualification_sha256": digest}
    run = registry.api(
        "runs/create",
        {
            "experiment_id": str(experiment),
            "run_name": qualification.model_family + "-" + model.detector_id[-12:],
            "start_time": int(qualification.original_started_at.timestamp() * 1000),
            "tags": [
                {"key": "retailops.anomaly_qualification_sha256", "value": digest},
                {
                    "key": "retailops.original_started_at",
                    "value": qualification.original_started_at.isoformat(),
                },
                {
                    "key": "retailops.original_completed_at",
                    "value": qualification.original_completed_at.isoformat(),
                },
                {"key": "retailops.logged_at", "value": datetime.now(UTC).isoformat()},
                {"key": "retailops.training_execution", "value": "native_external_run"},
                {
                    "key": "retailops.qualification_scope",
                    "value": qualification.qualification_scope,
                },
            ],
        },
    )["run"]
    run_id = run["info"]["run_id"]
    uri = run["info"]["artifact_uri"].rstrip("/") + "/anomaly"
    parts = uri.removeprefix("mlflow-artifacts:/").lstrip("/").split("/")
    if not uri.startswith("mlflow-artifacts:/") or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("anomaly_log_artifact_uri")
    try:
        registry.api(
            "runs/log-batch",
            {
                "run_id": run_id,
                "params": [
                    {"key": name, "value": str(value)}
                    for name, value in {
                        "detector_id": model.detector_id,
                        "model_family": qualification.model_family,
                        "source_dataset_id": qualification.source_dataset_id,
                        "qualified_anomaly_input_id": qualification.qualified_anomaly_input_id,
                        "model_seed": qualification.model_seed,
                        "data_seed": qualification.data_seed,
                        "ai_code_commit": qualification.ai_code_commit,
                        "source_code_commit": qualification.source_code_commit,
                        "dependency_lock_sha256": qualification.dependency_lock_sha256,
                        "evaluation_id": qualification.evaluation_id,
                    }.items()
                ],
                "metrics": [
                    {
                        "key": "required_quality_passed",
                        "value": 1,
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "step": 0,
                    }
                ]
                + [
                    {
                        "key": "final_" + name,
                        "value": value,
                        "timestamp": int(datetime.now(UTC).timestamp() * 1000),
                        "step": 0,
                    }
                    for name, value in quality["descriptor"]["summary"]["metrics"].items()
                    if value is not None
                ],
                "tags": [],
            },
        )
        for name, payload in artifacts.items():
            connection = http.client.HTTPConnection(
                registry.transport.host, registry.transport.port, timeout=60
            )
            try:
                connection.request(
                    "PUT",
                    "/api/2.0/mlflow-artifacts/artifacts/" + "/".join(parts) + "/" + name,
                    payload,
                    {"Content-Type": "application/octet-stream"},
                )
                response = connection.getresponse()
                response.read(1024**2)
                if response.status != 200:
                    raise ValueError("anomaly_log_upload_failed")
            finally:
                connection.close()
            if registry.artifact(uri, name, limit=8 * 1024**2) != payload:
                raise ValueError("anomaly_log_remote_checksum")
        registry.api(
            "runs/update",
            {
                "run_id": run_id,
                "status": "FINISHED",
                "end_time": int(qualification.original_completed_at.timestamp() * 1000),
            },
        )
        registry.source(run_id, digest, MODEL)
    except Exception:
        registry.api("runs/update", {"run_id": run_id, "status": "FAILED"})
        raise
    return {
        "status": "logged",
        "mlflow_run_id": run_id,
        "qualification_sha256": digest,
        "evaluation_id": quality["quality_id"],
        "artifact_count": len(artifacts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", type=Path, required=True)
    parser.add_argument("--registry-port", type=int)
    parser.add_argument("--compose", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            log(args.capsule, AnomalyRegistry(port=args.registry_port, compose=args.compose))
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
