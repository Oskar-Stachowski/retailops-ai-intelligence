"""Read strict portable stockout state without loading the campaign controller."""

import hashlib
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_fit_artifact import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read
from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    CampaignStockoutConditionalRiskPipeline,
    CampaignStockoutRiskPipeline,
)
from retailops_ai.evaluation_campaign.campaign_stockout_trial_contract import (
    CampaignStockoutTrialReceipt,
)
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes

Pipeline = CampaignStockoutRiskPipeline | CampaignStockoutConditionalRiskPipeline


def binding(receipt: CampaignStockoutTrialReceipt) -> dict[str, Any]:
    return dict(
        plan=receipt.plan.model_dump(mode="json"),
        preparation_receipt_sha256=receipt.prepared.content_sha256(),
        base_receipt_sha256=receipt.base_receipt_sha256,
        base_pipeline_sha256=receipt.base_pipeline_sha256,
    )


def verify_trial_artifact(root: Path, receipt: CampaignStockoutTrialReceipt) -> Pipeline:
    receipt = CampaignStockoutTrialReceipt.model_validate(receipt.model_dump(mode="python"))
    files, size = _bundle_inventory(root, receipt.plan.max_artifact_bytes)
    if files != receipt.artifact_files or size != receipt.model_artifact_bytes:
        raise SnapshotError("stockout_trial_artifact_checksum_mismatch")
    if read(root / "binding.json") != binding(receipt):
        raise SnapshotError("stockout_trial_artifact_binding_mismatch")
    raw = read_bytes(root, "pipeline.json", receipt.plan.max_artifact_bytes)
    cls = (
        CampaignStockoutRiskPipeline
        if receipt.plan.kind == "base"
        else CampaignStockoutConditionalRiskPipeline
    )
    pipeline = cls.model_validate_json(raw)
    base = pipeline if isinstance(pipeline, CampaignStockoutRiskPipeline) else pipeline.base
    if (base.family, base.variant) != (receipt.plan.family, receipt.plan.variant):
        raise SnapshotError("stockout_trial_artifact_recipe_mismatch")
    if isinstance(pipeline, CampaignStockoutRiskPipeline):
        rows = pipeline.preprocessing.train_rows
        keys = pipeline.preprocessing.train_keys_sha256
        labels = pipeline.train_labels_sha256
        clock = pipeline.fit_known_at
    else:
        state = pipeline.calibrator
        rows, keys, labels, clock = (
            state.calibration_rows,
            state.calibration_keys_sha256,
            state.calibration_labels_sha256,
            state.fit_known_at,
        )
        if (
            state.C != receipt.plan.calibrator_C
            or hashlib.sha256(canonical_bytes(base.model_dump(mode="json")) + b"\n").hexdigest()
            != receipt.base_pipeline_sha256
        ):
            raise SnapshotError("stockout_trial_original_base_or_calibrator_changed")
    if (rows, keys, labels, clock) != (
        receipt.role_file.rows,
        receipt.role_file.keys_sha256,
        receipt.role_file.labels_sha256,
        receipt.fit_known_at,
    ):
        raise SnapshotError("stockout_trial_full_role_fit_binding_mismatch")
    return pipeline
