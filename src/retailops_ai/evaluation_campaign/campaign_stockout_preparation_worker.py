"""Full public preparation, original mature labels, and independent artifact reread."""

import hashlib
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_fit_artifact import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_public import (
    prepare_public_stockout,
    verify_public_stockout,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_artifact import (
    prepare_stockout_roles,
    verify_stockout_roles,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes
from retailops_ai.worker_resources import worker_peak_rss_bytes


def execute(root: Path, phase: str, request: dict[str, Any]) -> dict[str, Any]:
    plan = CampaignStockoutPreparationPlan.model_validate_json(canonical_bytes(request["plan"]))
    generated = CampaignGeneratedParentReceipt.model_validate_json(
        canonical_bytes(request["generated"])
    )
    if (
        generated.runtime != runtime_pin()
        or generated.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.operation_id != plan.generation_operation_id
    ):
        raise SnapshotError("stockout_preparation_worker_runtime_mismatch")
    public_plan = plan.public_plan(generated)
    snapshot, curated = Path(request["snapshot"]), Path(request["curated"])
    if phase == "public":
        manifest = prepare_public_stockout(snapshot, curated, root / "public", public_plan)
        return {"public_dataset_id": manifest["dataset_id"]}
    public_id = str(read(root / "public.json")["public_dataset_id"])
    role_plan = plan.role_plan(generated, public_id)
    if phase == "roles":
        manifest = prepare_stockout_roles(
            root / "public",
            snapshot,
            curated,
            Path(request["qualification"]),
            root / "bundle",
            role_plan,
            allow_evaluation_truth=True,
        )
        return {"dataset_id": manifest["dataset_id"]}
    if phase != "verify":
        raise SnapshotError("stockout_preparation_unknown_phase")
    dataset_id = str(read(root / "roles.json")["dataset_id"])
    verify_public_stockout(
        root / "public", expected_dataset_id=public_id, expected_plan=public_plan
    )
    verify_stockout_roles(
        root / "bundle",
        expected_dataset_id=dataset_id,
        expected_plan=role_plan,
        allow_evaluation_truth=True,
    )
    files, size = _bundle_inventory(root / "bundle", plan.max_role_bytes)
    return {
        "public_dataset_id": public_id,
        "dataset_id": dataset_id,
        "role_plan": role_plan.model_dump(mode="json"),
        "manifest_sha256": hashlib.sha256(read_bytes(root / "bundle", "manifest.json")).hexdigest(),
        "artifact_bytes": size,
        "artifact_files": files,
    }


def main(root: Path, phase: str) -> None:
    started, before = perf_counter(), runtime_pin()
    result = execute(root, phase, read(root / "request.json"))
    if runtime_pin() != before:
        raise SnapshotError("stockout_preparation_worker_runtime_changed")
    result.update(
        worker_peak_rss_bytes=worker_peak_rss_bytes(),
        worker_wall_seconds=perf_counter() - started,
    )
    write(root / (phase + ".json"), result)
