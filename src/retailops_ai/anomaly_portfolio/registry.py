"""AI05 bounded MLflow transport with anomaly-specific qualification verification."""

import hashlib
import json
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from retailops_ai.anomaly_detectors.protocol import Scope, Window
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_evaluation.verification import verify_quality
from retailops_ai.anomaly_portfolio.lifecycle_contract import MODEL, Binding, Qualification
from retailops_ai.anomaly_portfolio.model import Model, score
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.qualified_anomalies.contract import Point
from retailops_ai.source_snapshot.files import canonical_json, decode_json, json_sha256


class AnomalyRegistry:
    def __init__(
        self, *, port: int | None = None, compose: bool = False, environment: str = "local"
    ) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("anomaly_registry_environment")
        self.transport = MLflowRegistry(compose=compose, environment=environment)
        if port is not None and (compose or type(port) is not int or not 1 <= port <= 65535):
            raise ValueError("anomaly_registry_loopback_port")
        if port is not None:
            self.transport.port = port

    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.transport.api(path, payload)

    def artifact(self, uri: str, name: str, *, limit: int = 4 * 1024**2) -> bytes:
        if re.fullmatch(r"evaluation_inputs_(42|137|2026)_(demand|physical)\.json", name):
            if not re.fullmatch(r"mlflow-artifacts:/[A-Za-z0-9_./-]+", uri):
                raise ValueError("anomaly_registry_artifact_uri")
            parts = uri.removeprefix("mlflow-artifacts:/").lstrip("/").split("/")
            if any(part in {"", ".", ".."} for part in parts):
                raise ValueError("anomaly_registry_artifact_uri")
            return self.transport.request(
                "/api/2.0/mlflow-artifacts/artifacts/" + "/".join(parts) + "/" + name,
                limit=limit,
            )
        return self.transport.artifact(uri, name, limit=limit)

    def version(self, model: str, version: str) -> dict[str, Any]:
        if model != MODEL:
            raise ValueError("anomaly_registry_namespace")
        return self.transport.version(model, version)

    def find(self, model: str, decision: str) -> list[str]:
        if model != MODEL:
            raise ValueError("anomaly_registry_namespace")
        return self.transport.find(model, decision)

    def create(self, model: str, run_id: str, source: str, decision: str, digest: str) -> str:
        if model != MODEL:
            raise ValueError("anomaly_registry_namespace")
        return self.transport.create(model, run_id, source, decision, digest)

    def set_alias(self, model: str, alias: str, version: str) -> None:
        if model != MODEL or alias not in {"candidate", "champion", "rollback"}:
            raise ValueError("anomaly_registry_namespace_or_alias")
        self.transport.set_alias(model, alias, version)

    def aliases(self, model: str) -> dict[str, str]:
        if model != MODEL:
            raise ValueError("anomaly_registry_namespace")
        try:
            value = self.api("registered-models/get?" + urllib.parse.urlencode({"name": model}))
        except ValueError as exc:
            if str(exc) == "mlflow_resource_missing":
                return {}
            raise
        aliases = value["registered_model"].get("aliases", [])
        result = {a["alias"]: a["version"] for a in aliases}
        if len(result) != len(aliases) or set(result) - {"candidate", "champion", "rollback"}:
            raise ValueError("anomaly_registry_uncontrolled_alias")
        return result

    def source(self, run_id: str, digest: str, model: str) -> tuple[str, Qualification]:
        if model != MODEL or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
            raise ValueError("anomaly_registry_run_or_namespace")
        run = self.api("runs/get?run_id=" + run_id)["run"]
        tags = {t["key"]: t["value"] for t in run["data"].get("tags", [])}
        if (
            run["info"]["status"] != "FINISHED"
            or tags.get("retailops.anomaly_qualification_sha256") != digest
        ):
            raise ValueError("anomaly_registry_qualified_run_required")
        uri = run["info"]["artifact_uri"].rstrip("/") + "/anomaly"
        raw = self.artifact(uri, "qualification.json")
        qualification = Qualification.model_validate_json(raw)
        if (
            raw != canonical_json(qualification.model_dump(mode="json")) + b"\n"
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ValueError("anomaly_registry_qualification_checksum")
        artifacts = {}
        for name, receipt in (
            ("model.json", qualification.model),
            ("config.json", qualification.config),
            ("signature.json", qualification.signature),
            ("input_example.json", qualification.input_example),
            *(("gate_" + k + ".json", g.report) for k, g in qualification.gates.items()),
        ):
            raw = self.artifact(uri, name, limit=8 * 1024**2)
            if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
                raise ValueError("anomaly_registry_artifact_checksum")
            artifacts[name] = raw
        for name, gate in qualification.gates.items():
            report = decode_json(artifacts["gate_" + name + ".json"])
            if (
                gate.status != "passed"
                or report.get("status") != "passed"
                or report.get("gate") != name
                or report.get("evidence_id") != qualification.evidence_id
                or report.get("model_sha256") != qualification.model.sha256
            ):
                raise ValueError("anomaly_registry_required_gate_unqualified")
        freshness = decode_json(artifacts["gate_freshness_drift_compatibility.json"])
        until = datetime.fromisoformat(freshness["valid_until"])
        if (
            until.utcoffset() is None
            or until <= datetime.now(UTC)
            or freshness["reference_id"] != qualification.reference_id
        ):
            raise ValueError("anomaly_registry_compatibility_expired_or_unbound")
        self.smoke(qualification, artifacts)
        saved_model = Model.model_validate_json(artifacts["model.json"])
        config = decode_json(artifacts["config.json"])
        frozen = config["selection"]["descriptor"]
        if (
            frozen["original_started_at"] != qualification.original_started_at.isoformat()
            or frozen["original_completed_at"] != qualification.original_completed_at.isoformat()
        ):
            raise ValueError("anomaly_registry_original_fit_time_binding")
        quality = verify_quality(
            decode_json(artifacts["gate_segments.json"]),
            config,
            lambda name: self.artifact(uri, name, limit=8 * 1024**2),
            saved_model.detector_id,
            qualification.model_family,
            saved_model,
        )
        if (
            quality["quality_id"] != qualification.evaluation_id
            or quality["descriptor"]["policy"]["qualification_scope"]
            != qualification.qualification_scope
        ):
            raise ValueError("anomaly_registry_quality_identity")
        return uri, qualification

    @staticmethod
    def smoke(qualification: Qualification, artifacts: dict[str, bytes]) -> None:
        model = Model.model_validate_json(artifacts["model.json"])
        if (
            model.descriptor.source_dataset_id != qualification.source_dataset_id
            or model.descriptor.qualified_anomaly_input_id
            != qualification.qualified_anomaly_input_id
            or model.descriptor.dependency_lock_sha256 != qualification.dependency_lock_sha256
            or model.descriptor.policy.model_seed != qualification.model_seed
        ):
            raise ValueError("anomaly_registry_model_lineage")
        signature = decode_json(artifacts["signature.json"])
        expected = {
            "point_schema_sha256": json_sha256(Point.model_json_schema()),
            "feature_schema_version": qualification.feature_schema_version,
            "detector_id": model.detector_id,
            "family": qualification.model_family,
            "output_schema_sha256": json_sha256(Decision.model_json_schema()),
        }
        if signature != expected:
            raise ValueError("anomaly_registry_signature")
        example = decode_json(artifacts["input_example.json"])
        if (
            set(example) != {"points", "scopes", "window", "as_of"}
            or not 1 <= len(example["points"]) <= 32
        ):
            raise ValueError("anomaly_registry_input_example")
        points = [Point.model_validate_json(json.dumps(p)) for p in example["points"]]
        scopes = tuple(Scope.model_validate_json(json.dumps(s)) for s in example["scopes"])
        window = Window.model_validate_json(json.dumps(example["window"]))
        predictions = score(
            model,
            points,
            scopes,
            window,
            qualification.model_family,
            "validation",
            datetime.fromisoformat(example["as_of"]),
        )
        if (
            json_sha256([p.model_dump(mode="json") for p in predictions])
            != qualification.expected_output_sha256
        ):
            raise ValueError("anomaly_registry_load_predict_mismatch")

    def validate(self, binding: Binding) -> None:
        version = self.version(binding.model_name, binding.model_version)
        uri, qualification = self.source(
            binding.mlflow_run_id, binding.qualification_sha256, binding.model_name
        )
        if (
            version["name"] != binding.model_name
            or version["version"] != binding.model_version
            or version["run_id"] != binding.mlflow_run_id
            or version["source"] != binding.source_uri
            or version.get("status") != "READY"
            or uri != binding.source_uri
            or qualification != binding.qualification
        ):
            raise ValueError("anomaly_registry_immutable_version_changed")
