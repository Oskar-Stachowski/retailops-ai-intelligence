"""Bounded client for the trusted local MLflow server and qualification capsule."""

import hashlib
import http.client
import json
import math
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.model_lifecycle.contracts import MODEL, TEST_MODEL, Binding, Qualification
from retailops_ai.security.local import strict_json

MAX_METADATA = 4 * 1024**2
MAX_MODEL = 256 * 1024**2


class MLflowRegistry:
    def __init__(self, *, compose: bool = False, environment: str = "local") -> None:
        self.host, self.port = ("mlflow", 5000) if compose else ("127.0.0.1", 5010)
        self.environment = environment

    def request(
        self, path: str, payload: dict[str, Any] | None = None, *, limit: int = MAX_METADATA
    ) -> bytes:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=60)
        try:
            raw = json.dumps(payload).encode() if payload is not None else None
            connection.request(
                "POST" if payload is not None else "GET",
                path,
                raw,
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            value = response.read(limit + 1)
            if response.status == 404:
                raise ValueError("mlflow_resource_missing")
            if response.status != 200 or len(value) > limit:
                raise ValueError("mlflow_response_invalid")
            return value
        finally:
            connection.close()

    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = self.request("/api/2.0/mlflow/" + path, payload)
        strict_json(raw)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("mlflow_response_object_required")
        return value

    def artifact(self, source_uri: str, name: str, *, limit: int = MAX_METADATA) -> bytes:
        if not re.fullmatch(r"mlflow-artifacts:/[A-Za-z0-9_./-]+", source_uri):
            raise ValueError("untrusted_model_source")
        parts = source_uri.removeprefix("mlflow-artifacts:/").lstrip("/").split("/")
        if any(p in {"", ".", ".."} for p in parts):
            raise ValueError("untrusted_model_source")
        if not re.fullmatch(r"[a-z_]+\.json", name):
            raise ValueError("invalid_capsule_artifact_name")
        return self.request(
            "/api/2.0/mlflow-artifacts/artifacts/" + "/".join(parts) + "/" + name, limit=limit
        )

    def aliases(self, model: str) -> dict[str, str]:
        if model not in {MODEL, TEST_MODEL}:
            raise ValueError("unsupported_registry_model")
        try:
            value = self.api("registered-models/get?" + urllib.parse.urlencode({"name": model}))
        except ValueError as exc:
            if str(exc) == "mlflow_resource_missing":
                return {}
            raise
        rows = value["registered_model"].get("aliases", [])
        result = {x["alias"]: x["version"] for x in rows}
        if len(result) != len(rows) or set(result) - {"candidate", "champion", "rollback"}:
            raise ValueError("uncontrolled_registry_alias")
        return result

    def source(self, run_id: str, digest: str, model: str) -> tuple[str, Qualification]:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("invalid_mlflow_run_id")
        source = self.api("runs/get?run_id=" + run_id)["run"]
        tags = {t["key"]: t["value"] for t in source["data"].get("tags", [])}
        if (
            source["info"]["status"] != "FINISHED"
            or tags.get("retailops.qualification_sha256") != digest
        ):
            raise ValueError("qualified_capsule_required")
        uri = source["info"]["artifact_uri"].rstrip("/") + "/lifecycle"
        raw = self.artifact(uri, "qualification.json")
        strict_json(raw)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("qualification_checksum_mismatch")
        qualification = Qualification.model_validate_json(raw)
        fixture = qualification.purpose == "lifecycle_mechanics_only"
        if (
            (model == TEST_MODEL) != fixture
            or model not in {MODEL, TEST_MODEL}
            or (fixture and self.environment != "test")
        ):
            raise ValueError("model_namespace_qualification_mismatch")
        files: dict[str, bytes] = {}
        for name, receipt in (
            ("model.json", qualification.model),
            ("config.json", qualification.config),
            ("signature.json", qualification.signature),
            ("input_example.json", qualification.input_example),
            *(
                ("gate_" + name + ".json", gate.report)
                for name, gate in qualification.gates.items()
            ),
        ):
            raw = self.artifact(
                uri, name, limit=MAX_MODEL if name == "model.json" else MAX_METADATA
            )
            if (len(raw), hashlib.sha256(raw).hexdigest()) != (receipt.size_bytes, receipt.sha256):
                raise ValueError("qualified_artifact_checksum_mismatch")
            strict_json(raw)
            files[name] = raw
        reports = {
            name: json.loads(files["gate_" + name + ".json"]) for name in qualification.gates
        }
        for name, gate in qualification.gates.items():
            report = reports[name]
            if (
                gate.status != "passed"
                or report.get("gate") != name
                or report.get("status") != gate.status
                or report.get("evidence_id") != qualification.evidence_id
                or report.get("model_sha256") != qualification.model.sha256
            ):
                raise ValueError("model_qualification_gate_not_passed_or_unbound")
        validity = reports["freshness_drift_compatibility"].get("valid_until")
        if not isinstance(validity, str):
            raise ValueError("qualification_current_compatibility_required")
        until = datetime.fromisoformat(validity)
        if until.utcoffset() is None or until <= datetime.now(UTC):
            raise ValueError("qualification_compatibility_expired")
        if (
            reports["freshness_drift_compatibility"].get("reference_id")
            != qualification.reference_id
        ):
            raise ValueError("qualification_reference_binding_mismatch")
        self.load_smoke(qualification, files, reports)
        return uri, qualification

    def load_smoke(
        self, qualification: Qualification, files: dict[str, bytes], reports: dict[str, Any]
    ) -> None:
        artifact = json.loads(files["model.json"])
        signature = json.loads(files["signature.json"])
        example = json.loads(files["input_example.json"])
        if qualification.purpose == "lifecycle_mechanics_only":
            if (
                set(artifact) != {"format", "bias"}
                or artifact["format"] != "mechanics-v1"
                or type(artifact["bias"]) not in {int, float}
            ):
                raise ValueError("invalid_mechanics_model")
            if (
                signature != {"input": "finite_scalar", "output": "nonnegative_units"}
                or not isinstance(example, list)
                or not 1 <= len(example) <= 32
            ):
                raise ValueError("invalid_mechanics_signature")
            if any(type(x) not in {int, float} or not math.isfinite(x) for x in example):
                raise ValueError("invalid_mechanics_input")
            output = [max(float(x) + float(artifact["bias"]), 0.0) for x in example]
        else:
            # Reuse the portable AI 04 adapter; no pickle, client URL or second metric evaluator.
            from retailops_ai.forecasting.features_contract import InputRow
            from retailops_ai.forecasting.model_contract import ModelPipeline
            from retailops_ai.forecasting.model_trees import ForecastAdapter
            from retailops_ai.forecasting.quality_contract import QualityManifest

            quality = QualityManifest.model_validate_json(
                json.dumps(reports["segments"]["quality_manifest"])
            )
            if (
                quality.descriptor.quality_status != "passed"
                or quality.descriptor.feature_set_id != qualification.feature_set_id
                or quality.quality_id != qualification.evaluation_id
            ):
                raise ValueError("qualified_model_quality_or_lineage_mismatch")
            if not isinstance(example, list) or not 1 <= len(example) <= 32:
                raise ValueError("qualified_model_example_limit")
            input_schema = InputRow.model_json_schema()
            if qualification.flavor == "baseline-json-v1":
                from retailops_ai.model_lifecycle.baseline import (
                    BaselineInput,
                    BaselinePipeline,
                    predict_examples,
                )

                baseline = BaselinePipeline.model_validate_json(files["model.json"])
                if (
                    baseline.feature_set_id != qualification.feature_set_id
                    or reports["segments"].get("model_id") != baseline.model_id
                ):
                    raise ValueError("qualified_baseline_binding_mismatch")
                input_schema = BaselineInput.model_json_schema()
                output = predict_examples(
                    baseline,
                    [BaselineInput.model_validate_json(json.dumps(row)) for row in example],
                )
            else:
                pipeline = ModelPipeline.model_validate_json(files["model.json"])
                if (
                    reports["segments"].get("model_id") != pipeline.model_id
                    or pipeline.descriptor.feature_set_id != qualification.feature_set_id
                    or pipeline.descriptor.split_id != qualification.split_id
                    or pipeline.descriptor.family != qualification.model_family
                    or pipeline.descriptor.code.dependency_lock_sha256
                    != qualification.dependency_lock_sha256
                ):
                    raise ValueError("qualified_model_pipeline_binding_mismatch")
                rows = [InputRow.model_validate_json(json.dumps(row)) for row in example]
                output = list(
                    ForecastAdapter(pipeline).predict(
                        rows, feature_set_id=qualification.feature_set_id
                    )
                )
            expected_signature = {
                "input_schema_sha256": canonical_sha256(input_schema),
                "feature_set_id": qualification.feature_set_id,
                "target_type": "observed_sales_units",
                "output": "nonnegative_units",
            }
            if signature != expected_signature:
                raise ValueError("qualified_model_signature_mismatch")
        if (
            any(not math.isfinite(x) or x < 0 for x in output)
            or canonical_sha256(output) != qualification.expected_output_sha256
        ):
            raise ValueError("qualified_model_load_smoke_failed")

    def version(self, model: str, version: str) -> dict[str, Any]:
        value = self.api(
            "model-versions/get?" + urllib.parse.urlencode({"name": model, "version": version})
        )["model_version"]
        if not isinstance(value, dict):
            raise ValueError("invalid_model_version")
        return value

    def validate(self, binding: Binding) -> None:
        value = self.version(binding.model_name, binding.model_version)
        uri, qualification = self.source(
            binding.mlflow_run_id, binding.qualification_sha256, binding.model_name
        )
        if (
            value["name"] != binding.model_name
            or value["version"] != binding.model_version
            or value["run_id"] != binding.mlflow_run_id
            or value["source"] != binding.source_uri
            or value.get("status") != "READY"
            or uri != binding.source_uri
            or qualification != binding.qualification
        ):
            raise ValueError("immutable_registry_binding_changed")

    def find(self, model: str, decision: str) -> list[str]:
        result: list[str] = []
        token: str | None = None
        for _ in range(100):
            query = {"filter": "name='" + model + "'", "max_results": "1000"}
            if token:
                query["page_token"] = token
            value = self.api("model-versions/search?" + urllib.parse.urlencode(query))
            for item in value.get("model_versions", []):
                tags = {t["key"]: t["value"] for t in item.get("tags", [])}
                if tags.get("retailops.registration_decision") == decision:
                    result.append(item["version"])
            token = value.get("next_page_token")
            if not token:
                return result
        raise ValueError("registry_search_limit")

    def create(self, model: str, run_id: str, source: str, decision: str, digest: str) -> str:
        try:
            self.api("registered-models/get?" + urllib.parse.urlencode({"name": model}))
        except ValueError as exc:
            if str(exc) != "mlflow_resource_missing":
                raise
            self.api("registered-models/create", {"name": model})
        value = self.api(
            "model-versions/create",
            {
                "name": model,
                "source": source,
                "run_id": run_id,
                "tags": [
                    {"key": "retailops.registration_decision", "value": decision},
                    {"key": "retailops.qualification_sha256", "value": digest},
                ],
            },
        )["model_version"]
        return str(value["version"])

    def set_alias(self, model: str, alias: str, version: str) -> None:
        self.api("registered-models/alias", {"name": model, "alias": alias, "version": version})
