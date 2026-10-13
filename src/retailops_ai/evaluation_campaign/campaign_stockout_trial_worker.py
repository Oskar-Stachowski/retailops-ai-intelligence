"""Fit and cold-reload native stockout models on exactly one authorized mature role."""

import hashlib
import os
import re
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_stockout_calibration as calibration
from retailops_ai.evaluation_campaign import campaign_stockout_training as training
from retailops_ai.evaluation_campaign.campaign_fit_artifact import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_artifact import load_stockout_role
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    CampaignStockoutRoleFile,
)
from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    CampaignStockoutConditionalRiskPipeline,
    CampaignStockoutRiskPipeline,
)
from retailops_ai.evaluation_campaign.campaign_stockout_trial_artifact import (
    Pipeline,
    verify_trial_artifact,
)
from retailops_ai.evaluation_campaign.campaign_stockout_trial_contract import (
    CampaignStockoutTrialPlan,
    CampaignStockoutTrialReceipt,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes
from retailops_ai.stockout_selection.pipeline import offsets
from retailops_ai.stockout_training import pipeline as native
from retailops_ai.worker_resources import worker_peak_rss_bytes


def _reload_probabilities(model: Pipeline, rows: list[dict[str, Any]]) -> native.Matrix:
    """A numerical reload probe on fit inputs, never a chronological quality score.

    Reuse native transforms, signed estimator scores, offsets and sigmoid. No
    timestamp is rewritten to pretend that the fitted model existed earlier.
    The probe is kept outside the published model bundle; later trials cannot
    access an earlier role's row-level predictions through that bundle.
    """
    base = model if isinstance(model, CampaignStockoutRiskPipeline) else model.base
    result = np.empty(len(rows), dtype=np.float64)
    with threadpool_limits(limits=1):
        for start in range(0, len(rows), training.BATCH_ROWS):
            chunk = rows[start : start + training.BATCH_ROWS]
            scores = native.signed_scores(
                base.estimator, native.transform(chunk, base.preprocessing)
            )
            if isinstance(model, CampaignStockoutConditionalRiskPipeline):
                state = model.calibrator
                scores = (
                    scores * state.raw_score_slope
                    + offsets(chunk, state.categories, state.stock_locations)
                    @ np.asarray(state.offset_weights)
                    + state.intercept
                )
            result[start : start + len(chunk)] = native.probabilities(scores)
    if not np.isfinite(result).all() or not ((result >= 0) & (result <= 1)).all():
        raise SnapshotError("stockout_trial_invalid_reload_probabilities")
    return result


def execute(root: Path, phase: str, request: dict[str, Any]) -> dict[str, Any]:
    if phase not in ("fit", "reload"):
        raise SnapshotError("stockout_trial_unknown_phase")
    plan = CampaignStockoutTrialPlan.model_validate_json(canonical_bytes(request["plan"]))
    prepared = CampaignStockoutPreparationReceipt.model_validate_json(
        canonical_bytes(request["prepared"])
    )
    if (
        prepared.generated.runtime != runtime_pin()
        or plan.preparation_operation_id != prepared.operation_id
        or plan.source_recipe_sha256 != prepared.plan.source_recipe_sha256
    ):
        raise SnapshotError("stockout_trial_worker_runtime_or_preparation_mismatch")
    base_receipt = None
    base_model = None
    if plan.kind == "conditional":
        base_receipt = CampaignStockoutTrialReceipt.model_validate_json(
            canonical_bytes(request["base"])
        )
        if (
            base_receipt.plan.kind != "base"
            or base_receipt.operation_id != plan.base_operation_id
            or base_receipt.prepared != prepared
            or (base_receipt.plan.family, base_receipt.plan.variant) != (plan.family, plan.variant)
        ):
            raise SnapshotError("stockout_trial_worker_original_base_mismatch")
        if phase == "fit":
            base_model = verify_trial_artifact(Path(request["base_bundle"]), base_receipt)
            if not isinstance(base_model, CampaignStockoutRiskPipeline):
                raise SnapshotError("stockout_trial_requires_uncalibrated_base")
    rows, outcomes, role = load_stockout_role(
        Path(request["dataset"]),
        role=plan.role,
        expected_dataset_id=prepared.dataset_id,
        expected_plan=prepared.role_plan,
        allow_evaluation_truth=True,
    )
    role_file = CampaignStockoutRoleFile.model_validate_json(canonical_bytes(role["file"]))
    if role_file.sha256 != prepared.artifact_files[plan.role + ".jsonl"]:
        raise SnapshotError("stockout_trial_original_role_file_changed")
    if phase == "fit":
        if plan.kind == "base":
            model: Pipeline = training.fit_model(
                rows,
                outcomes,
                family=plan.family,
                variant=plan.variant,
                fit_known_at=plan.fit_clock(prepared),
                policy=plan.policy,
            )
        else:
            if (
                not isinstance(base_model, CampaignStockoutRiskPipeline)
                or plan.calibrator_C is None
            ):
                raise SnapshotError("stockout_trial_original_base_required")
            model = calibration.fit_conditional(
                base_model,
                rows,
                outcomes,
                C=plan.calibrator_C,
                fit_known_at=plan.fit_clock(prepared),
            )
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "pipeline.json", model.model_dump(mode="json"))
        write(
            bundle / "binding.json",
            dict(
                plan=plan.model_dump(mode="json"),
                preparation_receipt_sha256=prepared.content_sha256(),
                base_receipt_sha256=base_receipt.content_sha256() if base_receipt else None,
                base_pipeline_sha256=base_receipt.pipeline_sha256 if base_receipt else None,
            ),
        )
        files, size = _bundle_inventory(bundle, plan.max_artifact_bytes)
        receipt = CampaignStockoutTrialReceipt(
            protocol_sha256=prepared.protocol_sha256,
            operation_id=request["operation_id"],
            reservation_id=request["reservation_id"],
            plan=plan,
            prepared=prepared,
            base_receipt_sha256=base_receipt.content_sha256() if base_receipt else None,
            base_pipeline_sha256=base_receipt.pipeline_sha256 if base_receipt else None,
            role_file=role_file,
            fit_known_at=plan.fit_clock(prepared),
            pipeline_sha256=files["pipeline.json"],
            model_artifact_bytes=size,
            artifact_files=files,
            worker_evidence={},
        )
        verify_trial_artifact(bundle, receipt)
        raw = _reload_probabilities(model, rows).astype("<f8").tobytes()
        descriptor = os.open(
            root / "reload-reference.f64",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        return dict(
            receipt=receipt.model_dump(mode="json"),
            reload_reference_sha256=hashlib.sha256(raw).hexdigest(),
        )
    fitted = read(root / "fit.json")
    receipt = CampaignStockoutTrialReceipt.model_validate_json(canonical_bytes(fitted["receipt"]))
    if (
        receipt.plan != plan
        or receipt.prepared != prepared
        or receipt.role_file != role_file
        or receipt.operation_id != request["operation_id"]
        or receipt.reservation_id != request["reservation_id"]
        or receipt.base_receipt_sha256 != (base_receipt.content_sha256() if base_receipt else None)
        or receipt.base_pipeline_sha256 != (base_receipt.pipeline_sha256 if base_receipt else None)
    ):
        raise SnapshotError("stockout_trial_cold_reload_binding_mismatch")
    model = verify_trial_artifact(root / "bundle", receipt)
    actual = _reload_probabilities(model, rows)
    raw = read_bytes(root, "reload-reference.f64", len(rows) * 8)
    if (
        len(raw) != len(rows) * 8
        or hashlib.sha256(raw).hexdigest() != fitted["reload_reference_sha256"]
    ):
        raise SnapshotError("stockout_trial_reload_reference_changed")
    expected = np.frombuffer(raw, dtype="<f8")
    if not np.isfinite(expected).all() or not np.allclose(actual, expected, rtol=1e-10, atol=1e-12):
        raise SnapshotError("stockout_trial_cold_reload_parity_failed")
    return dict(
        receipt=receipt.model_dump(mode="json"),
        full_role_rows=len(rows),
        cold_reload_passed=True,
        maximum_absolute_delta=float(np.max(np.abs(actual - expected))),
        relative_tolerance=1e-10,
        absolute_tolerance=1e-12,
    )


def main(root: Path, phase: str) -> None:
    started, before = perf_counter(), runtime_pin()
    try:
        result = execute(root, phase, read(root / "request.json"))
    except Exception as error:
        code = str(error)
        if isinstance(error, ConvergenceWarning):
            code = "stockout_trial_fit_did_not_converge"
        elif re.fullmatch(r"(?:stockout|campaign_stockout)_[a-z0-9_]{1,100}", code) is None:
            code = "stockout_trial_worker_error"
        write(
            root / (phase + "-error.json"),
            dict(
                error_code=code,
                worker_peak_rss_bytes=worker_peak_rss_bytes(),
                worker_wall_seconds=perf_counter() - started,
            ),
        )
        raise
    if runtime_pin() != before:
        raise SnapshotError("stockout_trial_worker_runtime_changed")
    result.update(
        worker_peak_rss_bytes=worker_peak_rss_bytes(), worker_wall_seconds=perf_counter() - started
    )
    write(root / (phase + ".json"), result)
