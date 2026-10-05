"""Idempotent private approval import; registration and promotion remain explicit decisions."""

import fcntl
import hashlib
import os
import stat
import tempfile
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.engine import require_promoter
from retailops_ai.model_lifecycle.v12_mlflow import artifact_path
from retailops_ai.source_snapshot.files import checked_directory, read_bytes
from retailops_ai.stockout_lifecycle.contract import StockoutRegistrySource, capsule_names
from retailops_ai.stockout_lifecycle.registry import API, MANIFEST, MLflowStockoutRegistry
from retailops_ai.stockout_lifecycle.release import receipt, verify_approved_capsule

EXPERIMENT = "retailops/stockout-serving-approvals"


def _tags(run: dict[str, Any]) -> dict[str, str]:
    rows = run["data"].get("tags", [])
    tags = {row["key"]: row["value"] for row in rows}
    if len(tags) != len(rows):
        raise ValueError("stockout_publish_duplicate_run_tags")
    return tags


def _experiment(registry: MLflowStockoutRegistry) -> str:
    try:
        return str(
            registry.api(
                API
                + "experiments/get-by-name?"
                + urllib.parse.urlencode({"experiment_name": EXPERIMENT})
            )["experiment"]["experiment_id"]
        )
    except ValueError as error:
        if str(error) != "mlflow_resource_missing":
            raise
    return str(registry.api(API + "experiments/create", dict(name=EXPERIMENT))["experiment_id"])


def publish_approval(
    root: Path,
    registry: MLflowStockoutRegistry,
    *,
    approval_id: str,
    model: str,
    actor: Principal,
    work: Path,
) -> dict[str, Any]:
    require_promoter(actor)
    registry.namespace(model)
    approval = verify_approved_capsule(root, approval_id=approval_id)
    final = approval.qualification.purpose == "qualified_stockout"
    files = {
        name: read_bytes(root, name, limit=16 * 1024**2)
        for name in sorted(capsule_names(final=final))
    }
    references = {name: receipt(raw).model_dump(mode="json") for name, raw in files.items()}
    # Validate the purpose/namespace and full receipts before creating an MLflow run.
    source = StockoutRegistrySource.model_validate_json(
        canonical_bytes(
            dict(
                model_name=model,
                mlflow_run_id="0" * 32,
                source_uri="mlflow-artifacts:/validation/stockout-release",
                approval_sha256=references["approval.json"]["sha256"],
                approval=approval.model_dump(mode="json"),
                files=references,
            )
        )
    )
    manifest_raw = canonical_bytes(
        dict(
            version="stockout-registry-capsule-1.0.0",
            model_name=model,
            approval_id=approval.release_id,
            files=references,
        )
    )
    files[MANIFEST] = manifest_raw
    tags = {
        "retailops.import_kind": "stockout_inference_approval",
        "retailops.model_name": model,
        "retailops.approval_id": approval.release_id,
        "retailops.approval_sha256": source.approval_sha256,
        "retailops.capsule_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "retailops.training_executed": "false",
    }
    work = work.absolute()
    if work.is_relative_to(root.absolute()) or root.absolute().is_relative_to(work):
        raise ValueError("stockout_publish_work_overlaps_capsule")
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(work)
    if work.stat().st_mode & 0o077 or work.stat().st_uid != os.geteuid():
        raise ValueError("stockout_publish_private_work_required")
    fd = os.open(
        work / (model + "-" + approval.release_id + ".lock"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
            raise ValueError("stockout_publish_private_lock_required")
        fcntl.flock(lock, fcntl.LOCK_EX)
        experiment = _experiment(registry)
        matches: list[dict[str, Any]] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(100):
            payload: dict[str, Any] = dict(experiment_ids=[experiment], max_results=1000)
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
                raise ValueError("stockout_publish_approval_search_cycle")
            seen.add(token)
        else:
            raise ValueError("stockout_publish_approval_search_limit")
        if matches:
            if len(matches) != 1 or any(
                _tags(matches[0]).get(key) != value for key, value in tags.items()
            ):
                raise ValueError("stockout_publish_duplicate_or_changed_import")
            found = registry.source(matches[0]["info"]["run_id"], source.approval_sha256, model)
            if found.approval != approval or found.files != source.files:
                raise ValueError("stockout_publish_existing_import_conflict")
            return dict(
                status="already_imported",
                mlflow_run_id=found.mlflow_run_id,
                approval_id=approval.release_id,
                approval_sha256=source.approval_sha256,
                registered=False,
                promoted=False,
            )
        if verify_approved_capsule(root, approval_id=approval.release_id) != approval:
            raise ValueError("stockout_publish_capsule_changed_before_import")
        run = registry.api(
            API + "runs/create",
            dict(
                experiment_id=experiment,
                start_time=int(datetime.now(UTC).timestamp() * 1000),
                run_name="stockout-approval-" + model + "-" + approval.release_id,
                tags=[
                    {"key": key, "value": value}
                    for key, value in (tags | {"retailops.import_status": "uploading"}).items()
                ],
            ),
        )["run"]
        run_id = run["info"]["run_id"]
        try:
            with tempfile.TemporaryDirectory(prefix="ai08-stockout-capsule-upload-") as temporary:
                staging = Path(temporary).resolve()
                staging.chmod(0o700)
                for name, raw in files.items():
                    path = staging / name
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    path.write_bytes(raw)
                    path.chmod(0o600)
                    target = artifact_path(run, "stockout-release/" + name)
                    reference = receipt(raw)
                    registry.upload(target, staging, name, reference)
                    if registry.checksum(target, reference.size_bytes) != (
                        reference.size_bytes,
                        reference.sha256,
                    ):
                        raise ValueError("stockout_publish_uploaded_capsule_checksum")
            if verify_approved_capsule(root, approval_id=approval.release_id) != approval:
                raise ValueError("stockout_publish_capsule_changed_during_import")
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
            except Exception:  # noqa: S110 - preserve original failure, never log credentials
                pass
            raise
        registry.source(run_id, source.approval_sha256, model)
        return dict(
            status="imported",
            mlflow_run_id=run_id,
            approval_id=approval.release_id,
            approval_sha256=source.approval_sha256,
            registered=False,
            promoted=False,
        )
