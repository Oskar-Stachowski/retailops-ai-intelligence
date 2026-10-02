"""Bounded MLflow v12 approval capsules, separate from the original campaign evidence."""

import fcntl
import hashlib
import http.client
import os
import stat
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.v12_executor import inference_schema
from retailops_ai.forecast_jobs.v12_inference import LoadedV12Inference
from retailops_ai.model_lifecycle.engine import require_promoter
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    DEVELOPMENT_MODEL,
    MODEL,
    TEST_MODEL,
    V12Binding,
    V12RegistrySource,
    capsule_names,
)
from retailops_ai.model_lifecycle.v12_mlflow import _experiment, artifact_path
from retailops_ai.model_lifecycle.v12_release import receipt, verify_approved_capsule
from retailops_ai.model_lifecycle.v12_release_contracts import V12InferenceRelease
from retailops_ai.source_snapshot.files import (
    checked_directory,
    decode_json,
    read_bytes,
    relative_path,
)

EXPERIMENT = "retailops/forecast-v12-serving-approvals"
MANIFEST = "capsule_manifest.json"
API = "/api/2.0/mlflow/"
MAX_BYTES = 4 * 1024**2


def current_approval(approval: V12InferenceRelease) -> None:
    if not approval.reviewed_at <= datetime.now(UTC) < approval.qualification.valid_until:
        raise ValueError("v12_registry_approval_expired_or_future")


def _tags(run: dict[str, Any]) -> dict[str, str]:
    return {value["key"]: value["value"] for value in run["data"].get("tags", [])}


def _params(run: dict[str, Any]) -> dict[str, str]:
    return {value["key"]: value["value"] for value in run["data"].get("params", [])}


class MLflowV12Registry:
    def __init__(
        self, *, compose: bool = False, environment: str = "local", port: int | None = None
    ) -> None:
        self.transport = MLflowRegistry(compose=compose, environment=environment)
        if port is not None:
            if compose or type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("v12_registry_local_port")
            self.transport.port = port
        self.environment = environment

    def namespace(self, model: str) -> None:
        if model not in {MODEL, TEST_MODEL, DEVELOPMENT_MODEL} or (
            model == TEST_MODEL and self.environment != "test"
        ):
            raise ValueError("v12_registry_namespace_or_environment")

    def api(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith(API):
            raise ValueError("v12_registry_api_boundary")
        try:
            return self.transport.api(path.removeprefix(API), payload)
        except ValueError as error:
            if str(error) == "mlflow_resource_missing":
                raise ValueError("v12_mlflow_resource_missing") from None
            raise

    def _bytes(self, source: str, name: str) -> bytes:
        if not source.startswith("mlflow-artifacts:/"):
            raise ValueError("v12_registry_untrusted_artifact_uri")
        relative_path(source.removeprefix("mlflow-artifacts:/").lstrip("/"))
        path = artifact_path({"info": {"artifact_uri": source}}, name)
        return self.transport.request(path, limit=MAX_BYTES)

    def checksum(self, path: str, maximum: int) -> tuple[int, str]:
        if not path.startswith("/api/2.0/mlflow-artifacts/artifacts/") or maximum > MAX_BYTES:
            raise ValueError("v12_registry_capsule_byte_boundary")
        raw = self.transport.request(path, limit=maximum)
        return len(raw), hashlib.sha256(raw).hexdigest()

    def upload(self, path: str, root: Path, name: str, ref: V12ArtifactReceipt) -> None:
        if (
            not path.startswith("/api/2.0/mlflow-artifacts/artifacts/")
            or ref.size_bytes > MAX_BYTES
        ):
            raise ValueError("v12_registry_capsule_byte_boundary")
        raw = read_bytes(root, name)
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (ref.size_bytes, ref.sha256):
            raise ValueError("v12_registry_upload_source_changed")
        connection = http.client.HTTPConnection(
            self.transport.host, self.transport.port, timeout=60
        )
        try:
            connection.request("PUT", path, raw, {"Content-Type": "application/octet-stream"})
            response = connection.getresponse()
            body = response.read(MAX_BYTES + 1)
            if response.status != 200 or len(body) > MAX_BYTES:
                raise ValueError("v12_registry_upload_failed")
        finally:
            connection.close()

    def _campaign(self, source: V12RegistrySource) -> None:
        run = self.api(API + "runs/get?run_id=" + source.campaign_mlflow_run_id)["run"]
        tags, params = _tags(run), _params(run)
        pin = source.approval.qualification.pin
        accepted = source.approval.qualification.development_acceptance
        if accepted is not None:
            accepted.verify_pin(pin)
        expected_status = "not_ready" if accepted else "ready"
        expected_quality = "not_ready" if accepted else "passed"
        if (
            run["info"]["run_id"] != source.campaign_mlflow_run_id
            or run["info"]["status"] != "FINISHED"
            or tags.get("retailops.import_kind") != "v12_campaign_evidence"
            or tags.get("retailops.import_status") != "verified"
            or tags.get("retailops.original_model_status") != expected_status
            or tags.get("retailops.quality_status") != expected_quality
            or tags.get("retailops.independent_replay") != "passed"
            or any(
                tags.get("retailops." + name + "_eligible") != "false"
                for name in ("registration", "promotion", "serving")
            )
            or any(
                params.get(name) != getattr(pin, name)
                for name in (
                    "campaign_id",
                    "freeze_id",
                    "replay_id",
                    "code_sha256",
                    "dependency_lock_sha256",
                )
            )
            or params.get("original_run_id") != pin.run_id
            or params.get("run_manifest_sha256") != pin.manifest.sha256
        ):
            raise ValueError("v12_registry_campaign_import_binding")
        uri = run["info"]["artifact_uri"]
        raw = self._bytes(uri, "run_manifest.json")
        if (len(raw), hashlib.sha256(raw).hexdigest()) != (
            pin.manifest.size_bytes,
            pin.manifest.sha256,
        ):
            raise ValueError("v12_registry_campaign_manifest_checksum")
        manifest = decode_json(raw)
        descriptor = manifest["descriptor"]
        if (
            manifest["run_id"] != pin.run_id
            or pin.run_id != "functional-v12-run-sha256-" + canonical_sha256(descriptor)
            or descriptor["forecast_model_status"] != expected_status
            or descriptor["quality_qualification_status"] != expected_quality
            or any(
                descriptor[name] != getattr(pin, name)
                for name in ("campaign_id", "freeze_id", "replay_id")
            )
            or descriptor["code"]["code_sha256"] != pin.code_sha256
            or descriptor["code"]["dependency_lock_sha256"] != pin.dependency_lock_sha256
        ):
            raise ValueError("v12_registry_campaign_manifest_binding")

        def bound(name: str) -> dict[str, Any]:
            value = self._bytes(uri, name)
            if receipt(value).model_dump(mode="json") != descriptor["files"][name]:
                raise ValueError("v12_registry_campaign_artifact_changed")
            return decode_json(value)

        card, signature, freeze = (
            bound(name) for name in ("model_card.json", "signature.json", "campaign/freeze.json")
        )
        handoff, metrics, replay = (
            bound(name)
            for name in ("handoff.json", "campaign/metrics.json", "replay/replay_receipt.json")
        )
        recipe = bound(pin.recipe_path)
        reference = card["recipes"][pin.cohort_id][pin.fold.name]
        lineage = card["cohort_lineage"][pin.cohort_id]
        if (
            descriptor["files"][pin.recipe_path] != pin.recipe.model_dump(mode="json")
            or descriptor["files"]["signature.json"] != pin.signature.model_dump(mode="json")
            or reference["artifact"]["path"] != pin.recipe_path
            or reference["artifact"]["recipe_id"] != pin.recipe_id
            or any(
                reference["artifact"][name] != getattr(pin.recipe, name)
                for name in ("sha256", "size_bytes")
            )
            or any(
                lineage[name] != getattr(pin, name) for name in ("source_dataset_id", "snapshot_id")
            )
            or pin.fold.model_dump(mode="json") not in freeze["descriptor"]["split_policy"]["folds"]
            or recipe["recipe_id"] != pin.recipe_id
            or recipe["recipe_id"]
            != "functional-v12-recipe-sha256-"
            + canonical_sha256({key: value for key, value in recipe.items() if key != "recipe_id"})
            or recipe["fold"] != pin.fold.name
            or datetime.fromisoformat(recipe["selection_cutoff"]) != pin.fold.selection_cutoff
            or recipe["policy"] != freeze["descriptor"]["method_policy"]
            or recipe["policy"]["mean_variant"] == "hgb_blend"
            or pin.cohort_id not in recipe["support"]["cohort_ids"]
            or handoff["independent_replay"] != "passed"
            or handoff["all_preregistered_cohorts_included"] is not True
            or metrics["status"] != expected_quality
            or replay["replay_id"] != pin.replay_id
            or signature["deployable_service_contract"] is not False
            or signature["input_schema_sha256"] != canonical_sha256(signature["input_schema"])
            or canonical_sha256(inference_schema(signature["input_schema"]))
            != source.approval.qualification.source_policy.input_schema_sha256
        ):
            raise ValueError("v12_registry_recipe_replay_or_signature_binding")

    def source(
        self, run_id: str, digest: str, model: str, *, current: bool = True
    ) -> V12RegistrySource:
        self.namespace(model)
        # Validate operator supplied IDs before constructing a query or trusting metadata.
        from pydantic import TypeAdapter

        from retailops_ai.data_contracts.common import Sha256
        from retailops_ai.model_lifecycle.contracts import RunID

        TypeAdapter(RunID).validate_python(run_id)
        TypeAdapter(Sha256).validate_python(digest)
        run = self.api(API + "runs/get?run_id=" + run_id)["run"]
        tags = _tags(run)
        if (
            run["info"]["run_id"] != run_id
            or run["info"]["status"] != "FINISHED"
            or tags.get("retailops.import_kind") != "v12_inference_approval"
            or tags.get("retailops.import_status") != "verified"
            or tags.get("retailops.model_name") != model
            or tags.get("retailops.approval_sha256") != digest
        ):
            raise ValueError("v12_registry_finished_approval_required")
        uri = run["info"]["artifact_uri"].rstrip("/") + "/v12-release"
        inventory_raw = self._bytes(uri, MANIFEST)
        if hashlib.sha256(inventory_raw).hexdigest() != tags.get(
            "retailops.capsule_manifest_sha256"
        ):
            raise ValueError("v12_registry_capsule_manifest_checksum")
        inventory = decode_json(inventory_raw)
        if (
            set(inventory)
            != {"version", "model_name", "campaign_mlflow_run_id", "approval_id", "files"}
            or inventory["version"] != "v12-registry-capsule-1.0.0"
            or inventory["model_name"] != model
            or set(inventory["files"]) != capsule_names()
        ):
            raise ValueError("v12_registry_capsule_manifest_shape")
        with tempfile.TemporaryDirectory(prefix="ai05-v12-registry-read-") as temporary:
            root = Path(temporary).resolve()
            root.chmod(0o700)
            for name in sorted(capsule_names()):
                raw = self._bytes(uri, name)
                if receipt(raw).model_dump(mode="json") != inventory["files"][name]:
                    raise ValueError("v12_registry_approval_artifact_changed")
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                path.write_bytes(raw)
                path.chmod(0o600)
            approval = verify_approved_capsule(root, release_id=inventory["approval_id"])
            if current:
                current_approval(approval)
        source = V12RegistrySource.model_validate_json(
            canonical_bytes(
                dict(
                    model_name=model,
                    mlflow_run_id=run_id,
                    source_uri=uri,
                    approval_sha256=digest,
                    approval=approval.model_dump(mode="json"),
                    files=inventory["files"],
                    campaign_mlflow_run_id=inventory["campaign_mlflow_run_id"],
                )
            )
        )
        if (
            tags.get("retailops.approval_id") != approval.release_id
            or tags.get("retailops.campaign_mlflow_run_id") != source.campaign_mlflow_run_id
        ):
            raise ValueError("v12_registry_approval_tag_binding")
        self._campaign(source)
        return source

    def validate(self, binding: V12Binding, *, current: bool = True) -> None:
        source = self.source(
            binding.mlflow_run_id, binding.approval_sha256, binding.model_name, current=current
        )
        version = self.transport.version(binding.model_name, binding.model_version)
        if (
            version["name"] != binding.model_name
            or version["version"] != binding.model_version
            or version["run_id"] != binding.mlflow_run_id
            or version["source"] != binding.source_uri
            or version.get("status") != "READY"
            or source.model_dump(mode="json")
            != binding.model_dump(mode="json", exclude={"model_version"})
        ):
            raise ValueError("v12_registry_immutable_binding_changed")

    def aliases(self, model: str) -> dict[str, str]:
        self.namespace(model)
        try:
            result = self.api(
                API + "registered-models/get?" + urllib.parse.urlencode({"name": model})
            )
        except ValueError as error:
            if str(error) == "v12_mlflow_resource_missing":
                return {}
            raise
        rows = result["registered_model"].get("aliases", [])
        aliases = {row["alias"]: row["version"] for row in rows}
        if len(aliases) != len(rows) or set(aliases) - {"candidate", "champion", "rollback"}:
            raise ValueError("v12_registry_uncontrolled_alias")
        return aliases

    def find(self, model: str, decision: str) -> list[str]:
        self.namespace(model)
        return self.transport.find(model, decision)

    def create(self, source: V12RegistrySource, decision: str) -> str:
        self.namespace(source.model_name)
        return self.transport.create(
            source.model_name,
            source.mlflow_run_id,
            source.source_uri,
            decision,
            source.approval_sha256,
        )

    def set_alias(self, model: str, alias: str, version: str) -> None:
        self.namespace(model)
        if alias not in {"candidate", "champion", "rollback"}:
            raise ValueError("v12_registry_uncontrolled_alias")
        self.transport.set_alias(model, alias, version)


def publish_approval(
    root: Path,
    loaded: LoadedV12Inference,
    registry: MLflowV12Registry,
    *,
    campaign_mlflow_run_id: str,
    model: str,
    actor: Principal,
    work: Path,
) -> dict[str, Any]:
    """Upload only the small approved capsule; keep the original campaign tracking run unchanged."""
    require_promoter(actor)
    registry.namespace(model)
    approval = verify_approved_capsule(root, release_id=loaded.release.release_id)
    if approval != loaded.release or approval.qualification.pin != loaded.export.pin:
        raise ValueError("v12_registry_loaded_approval_changed")
    current_approval(approval)
    files = {name: read_bytes(root, name) for name in sorted(capsule_names())}
    references = {name: receipt(raw).model_dump(mode="json") for name, raw in files.items()}
    source = V12RegistrySource.model_validate_json(
        canonical_bytes(
            dict(
                model_name=model,
                mlflow_run_id="0" * 32,
                campaign_mlflow_run_id=campaign_mlflow_run_id,
                source_uri="mlflow-artifacts:/validation/v12-release",
                approval_sha256=references["release.json"]["sha256"],
                approval=approval.model_dump(mode="json"),
                files=references,
            )
        )
    )
    registry._campaign(source)  # No writes until the imported original evidence is bound.
    manifest_raw = (
        canonical_bytes(
            dict(
                version="v12-registry-capsule-1.0.0",
                model_name=model,
                approval_id=approval.release_id,
                campaign_mlflow_run_id=campaign_mlflow_run_id,
                files=references,
            )
        )
        + b"\n"
    )
    files[MANIFEST] = manifest_raw
    work = work.absolute()
    if work.is_relative_to(root.absolute()) or root.absolute().is_relative_to(work):
        raise ValueError("v12_registry_work_overlaps_capsule")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(work)
    if work.stat().st_mode & 0o077 or work.stat().st_uid != os.geteuid():
        raise ValueError("v12_registry_private_work_required")
    fd = os.open(
        work / (model + "-" + approval.release_id + ".lock"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("v12_registry_private_lock_required")
        fcntl.flock(lock, fcntl.LOCK_EX)
        experiment = _experiment(registry, EXPERIMENT)
        matches: list[dict[str, Any]] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            payload: dict[str, Any] = {"experiment_ids": [experiment], "max_results": 1000}
            if token:
                payload["page_token"] = token
            result = registry.api(API + "runs/search", payload)
            matches.extend(
                run
                for run in result.get("runs", [])
                if _tags(run).get("retailops.approval_id") == approval.release_id
                and _tags(run).get("retailops.model_name") == model
            )
            token = result.get("next_page_token")
            if not token:
                break
            if token in seen:
                raise ValueError("v12_registry_approval_search_cycle")
            seen.add(token)
        else:
            raise ValueError("v12_registry_approval_search_limit")
        tags = {
            "retailops.import_kind": "v12_inference_approval",
            "retailops.model_name": model,
            "retailops.approval_id": approval.release_id,
            "retailops.approval_sha256": source.approval_sha256,
            "retailops.campaign_mlflow_run_id": campaign_mlflow_run_id,
            "retailops.capsule_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "retailops.training_executed": "false",
        }
        if matches:
            if len(matches) != 1 or any(
                _tags(matches[0]).get(key) != value for key, value in tags.items()
            ):
                raise ValueError("v12_registry_duplicate_or_changed_approval_import")
            run = matches[0]
            resolved = registry.source(run["info"]["run_id"], source.approval_sha256, model)
            if resolved.approval != approval or resolved.files != source.files:
                raise ValueError("v12_registry_existing_approval_conflict")
            return dict(
                status="already_imported",
                mlflow_run_id=resolved.mlflow_run_id,
                approval_id=approval.release_id,
                approval_sha256=source.approval_sha256,
                registered=False,
            )
        now = int(datetime.now(UTC).timestamp() * 1000)
        run = registry.api(
            API + "runs/create",
            dict(
                experiment_id=experiment,
                start_time=now,
                run_name="v12-approval-" + model + "-" + approval.release_id,
                tags=[
                    {"key": key, "value": value}
                    for key, value in (tags | {"retailops.import_status": "uploading"}).items()
                ],
            ),
        )["run"]
        run_id = run["info"]["run_id"]
        try:
            with tempfile.TemporaryDirectory(prefix="ai05-v12-capsule-upload-") as temporary:
                staging = Path(temporary).resolve()
                for name, raw in files.items():
                    path = staging / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(raw)
                    path.chmod(0o600)
                    target = artifact_path(run, "v12-release/" + name)
                    ref = receipt(raw)
                    registry.upload(
                        target,
                        staging,
                        name,
                        V12ArtifactReceipt.model_validate_json(ref.model_dump_json()),
                    )
                    if registry.checksum(target, ref.size_bytes) != (ref.size_bytes, ref.sha256):
                        raise ValueError("v12_registry_uploaded_capsule_checksum")
            if verify_approved_capsule(root, release_id=approval.release_id) != approval:
                raise ValueError("v12_registry_capsule_changed_during_upload")
            current_approval(approval)
            registry._campaign(source)
            registry.api(
                API + "runs/set-tag",
                dict(run_id=run_id, key="retailops.import_status", value="verified"),
            )
            registry.api(
                API + "runs/update",
                dict(
                    run_id=run_id,
                    status="FINISHED",
                    end_time=int(datetime.now(UTC).timestamp() * 1000),
                ),
            )
        except Exception:
            try:
                registry.api(API + "runs/update", dict(run_id=run_id, status="FAILED"))
            except Exception:  # noqa: S110 - preserve the original failure without credential-bearing logs
                pass
            raise
        registry.source(run_id, source.approval_sha256, model)
        return dict(
            status="imported",
            mlflow_run_id=run_id,
            approval_id=approval.release_id,
            approval_sha256=source.approval_sha256,
            registered=False,
        )
