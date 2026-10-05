"""Stockout registry uses the existing trusted MLflow transport, with its own byte verifier."""

import hashlib
import http.client
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import Sha256
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.model_lifecycle.contracts import DecisionID, Receipt, RunID
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.model_lifecycle.v12_mlflow import artifact_path
from retailops_ai.source_snapshot.files import decode_json, read_bytes
from retailops_ai.stockout_lifecycle.contract import (
    MODEL,
    TEST_MODEL,
    StockoutBinding,
    StockoutRegistrySource,
    Version,
    capsule_names,
)
from retailops_ai.stockout_lifecycle.release import (
    MAX_CAPSULE_BYTES,
    receipt,
    verify_approved_capsule,
)

API = "/api/2.0/mlflow/"
MANIFEST = "capsule_manifest.json"
MAX_METADATA = 1024**2


class MLflowStockoutRegistry:
    def __init__(
        self, *, compose: bool = False, environment: str = "local", port: int | None = None
    ) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("stockout_registry_environment")
        self.transport = MLflowRegistry(compose=compose, environment=environment)
        if port is not None:
            if compose or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("stockout_registry_local_port")
            self.transport.port = port
        self.environment = environment

    def namespace(self, model: str) -> None:
        if model not in {MODEL, TEST_MODEL} or (model == TEST_MODEL and self.environment != "test"):
            raise ValueError("stockout_registry_namespace_or_environment")

    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith(API):
            raise ValueError("stockout_registry_api_boundary")
        return self.transport.api(path.removeprefix(API), payload)

    def _bytes(self, uri: str, name: str, *, limit: int) -> bytes:
        path = artifact_path({"info": {"artifact_uri": uri}}, name)
        return self.transport.request(path, limit=limit)

    def checksum(self, path: str, maximum: int) -> tuple[int, str]:
        if (
            not path.startswith("/api/2.0/mlflow-artifacts/artifacts/")
            or not 0 < maximum <= MAX_CAPSULE_BYTES
        ):
            raise ValueError("stockout_registry_capsule_byte_boundary")
        raw = self.transport.request(path, limit=maximum)
        return len(raw), hashlib.sha256(raw).hexdigest()

    def upload(self, path: str, root: Path, name: str, reference: Receipt) -> None:
        if (
            not path.startswith("/api/2.0/mlflow-artifacts/artifacts/")
            or reference.size_bytes > MAX_CAPSULE_BYTES
        ):
            raise ValueError("stockout_registry_capsule_byte_boundary")
        raw = read_bytes(root, name, limit=MAX_CAPSULE_BYTES)
        if receipt(raw) != reference:
            raise ValueError("stockout_registry_upload_source_changed")
        connection = http.client.HTTPConnection(
            self.transport.host, self.transport.port, timeout=60
        )
        try:
            connection.request("PUT", path, raw, {"Content-Type": "application/octet-stream"})
            response = connection.getresponse()
            body = response.read(MAX_METADATA + 1)
            if response.status != 200 or len(body) > MAX_METADATA:
                raise ValueError("stockout_registry_upload_failed")
        finally:
            connection.close()

    def source(
        self, run_id: str, digest: str, model: str, *, current: bool = True
    ) -> StockoutRegistrySource:
        self.namespace(model)
        TypeAdapter(RunID).validate_python(run_id)
        TypeAdapter(Sha256).validate_python(digest)
        run = self.api(API + "runs/get?run_id=" + run_id)["run"]
        rows = run["data"].get("tags", [])
        tags = {row["key"]: row["value"] for row in rows}
        if (
            len(tags) != len(rows)
            or run["info"]["run_id"] != run_id
            or run["info"]["status"] != "FINISHED"
            or tags.get("retailops.import_kind") != "stockout_inference_approval"
            or tags.get("retailops.import_status") != "verified"
            or tags.get("retailops.model_name") != model
            or tags.get("retailops.approval_sha256") != digest
            or tags.get("retailops.training_executed") != "false"
        ):
            raise ValueError("stockout_registry_finished_approval_required")
        uri = run["info"]["artifact_uri"].rstrip("/") + "/stockout-release"
        raw_manifest = self._bytes(uri, MANIFEST, limit=MAX_METADATA)
        if hashlib.sha256(raw_manifest).hexdigest() != tags.get(
            "retailops.capsule_manifest_sha256"
        ):
            raise ValueError("stockout_registry_capsule_manifest_checksum")
        manifest = decode_json(raw_manifest, limit=MAX_METADATA)
        final = model == MODEL
        names = capsule_names(final=final)
        if (
            set(manifest) != {"version", "model_name", "approval_id", "files"}
            or manifest["version"] != "stockout-registry-capsule-1.0.0"
            or manifest["model_name"] != model
            or set(manifest["files"]) != names
        ):
            raise ValueError("stockout_registry_capsule_manifest_shape")
        references = {
            name: Receipt.model_validate_json(canonical_bytes(ref))
            for name, ref in manifest["files"].items()
        }
        if (
            references["approval.json"].sha256 != digest
            or sum(ref.size_bytes for ref in references.values()) > MAX_CAPSULE_BYTES
        ):
            raise ValueError("stockout_registry_capsule_inventory_budget_or_pin")
        with tempfile.TemporaryDirectory(prefix="ai08-stockout-registry-read-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            for name in sorted(names):
                reference = references[name]
                raw = self._bytes(uri, name, limit=reference.size_bytes)
                if receipt(raw) != reference:
                    raise ValueError("stockout_registry_approval_artifact_changed")
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(raw)
                path.chmod(0o600)
            approval = verify_approved_capsule(
                root, approval_id=manifest["approval_id"], current=current
            )
        if tags.get("retailops.approval_id") != approval.release_id:
            raise ValueError("stockout_registry_approval_tag_binding")
        return StockoutRegistrySource.model_validate_json(
            canonical_bytes(
                dict(
                    model_name=model,
                    mlflow_run_id=run_id,
                    source_uri=uri,
                    approval_sha256=digest,
                    approval=approval.model_dump(mode="json"),
                    files=manifest["files"],
                )
            )
        )

    def validate(self, binding: StockoutBinding, *, current: bool = True) -> None:
        binding = StockoutBinding.model_validate_json(binding.model_dump_json())
        source = self.source(
            binding.mlflow_run_id, binding.approval_sha256, binding.model_name, current=current
        )
        version = self.transport.version(binding.model_name, binding.model_version)
        if (
            version.get("name") != binding.model_name
            or version.get("version") != binding.model_version
            or version.get("run_id") != binding.mlflow_run_id
            or version.get("source") != binding.source_uri
            or version.get("status") != "READY"
            or source.model_dump(mode="json")
            != binding.model_dump(mode="json", exclude={"model_version"})
        ):
            raise ValueError("stockout_registry_immutable_binding_changed")

    def aliases(self, model: str) -> dict[str, str]:
        self.namespace(model)
        try:
            result = self.api(
                API + "registered-models/get?" + urllib.parse.urlencode({"name": model})
            )
        except ValueError as error:
            if str(error) == "mlflow_resource_missing":
                return {}
            raise
        rows = result["registered_model"].get("aliases", [])
        aliases = {row["alias"]: row["version"] for row in rows}
        if len(aliases) != len(rows) or set(aliases) - {"candidate", "champion", "rollback"}:
            raise ValueError("stockout_registry_uncontrolled_alias")
        for version in aliases.values():
            TypeAdapter(Version).validate_python(version)
        return aliases

    def find(self, model: str, decision: str) -> list[str]:
        self.namespace(model)
        TypeAdapter(DecisionID).validate_python(decision)
        versions = self.transport.find(model, decision)
        for version in versions:
            TypeAdapter(Version).validate_python(version)
        if len(set(versions)) != len(versions):
            raise ValueError("stockout_registry_duplicate_version_search")
        return versions

    def create(self, source: StockoutRegistrySource, decision: str) -> str:
        source = StockoutRegistrySource.model_validate_json(source.model_dump_json())
        self.namespace(source.model_name)
        TypeAdapter(DecisionID).validate_python(decision)
        if self.source(source.mlflow_run_id, source.approval_sha256, source.model_name) != source:
            raise ValueError("stockout_registry_source_changed_before_registration")
        result = self.transport.create(
            source.model_name,
            source.mlflow_run_id,
            source.source_uri,
            decision,
            source.approval_sha256,
        )
        return TypeAdapter(Version).validate_python(result)

    def set_alias(self, model: str, alias: str, version: str) -> None:
        self.namespace(model)
        TypeAdapter(Version).validate_python(version)
        if alias not in {"candidate", "champion", "rollback"}:
            raise ValueError("stockout_registry_uncontrolled_alias")
        self.transport.set_alias(model, alias, version)
