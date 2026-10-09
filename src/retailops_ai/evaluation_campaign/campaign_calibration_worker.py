"""Measured residual calibration on its own role; no model or preprocessing refit."""

import hashlib
import importlib
import resource
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_calibration import _selected_score
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
)
from retailops_ai.evaluation_campaign.campaign_calibration_data import fit
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit_worker import _versions
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score import _verify_bundle
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastManifest
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes
from retailops_ai.worker_resources import worker_peak_rss_bytes


def calibrate(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    plan = CampaignForecastCalibrationPlan.model_validate_json(canonical_bytes(request["plan"]))
    _versions(plan)
    exported = CampaignDevelopmentExportReceipt.model_validate_json(
        canonical_bytes(request["exported"])
    )
    tune = CampaignForecastTuneReceipt.model_validate_json(canonical_bytes(request["tune"]))
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in request["scores"].items()
    }
    tune_scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in request["tune_scores"].items()
    }
    selected = _selected_score(plan, exported, tune, tune_scores, scores)
    before = runtime_pin()
    if (
        before.model_dump(mode="json") != request["runtime"]
        or exported.runtime_code_sha256 != before.code_sha256
    ):
        raise SnapshotError("campaign_calibration_worker_runtime_mismatch")
    dataset = Path(request["dataset"])
    raw = read_bytes(dataset, "manifest.json", 4 * 1024**2)
    manifest = PhysicalForecastManifest.model_validate_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != exported.manifest_sha256
        or manifest.dataset_id != exported.dataset_id
        or manifest.descriptor.recipe != exported.recipe
        or manifest.descriptor.runtime.code_sha256 != before.code_sha256
    ):
        raise SnapshotError("campaign_calibration_completed_export_manifest_mismatch")
    parent = Path(request["bundle"])
    _verify_bundle(parent, selected)
    calibration, population = fit(root, dataset, parent, manifest, selected, tune.selection, plan)
    if runtime_pin() != before:
        raise SnapshotError("campaign_calibration_worker_runtime_changed")
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    write(
        bundle / "parents.json",
        {key: request[key] for key in ("exported", "tune", "tune_scores", "scores")},
    )
    write(bundle / "population.json", population)
    write(bundle / "calibration.json", calibration.model_dump(mode="json"))
    return population | {"calibration": calibration.model_dump(mode="json")}


def main(root: Path) -> None:
    started = perf_counter()
    request = read(root / "request.json")
    mlflow = importlib.import_module("mlflow")
    mlflow.set_tracking_uri((root / "tracking").as_uri())
    experiment = mlflow.create_experiment(
        "ai09-audited-forecast-calibration",
        artifact_location=(root / "tracking-artifacts").as_uri(),
    )
    with mlflow.start_run(experiment_id=experiment) as run:
        result = calibrate(root, request)
        result["mlflow_run_id"] = run.info.run_id
        mlflow.log_params(
            {
                "role": "calibration",
                "calibration_fitted": result["calibration"]["calibration_fitted"],
                "status": result["calibration"]["status"],
                "model_reselected": False,
                "plan_sha256": CampaignForecastCalibrationPlan.model_validate_json(
                    canonical_bytes(request["plan"])
                ).content_sha256(),
                "dataset_id": request["exported"]["dataset_id"],
            }
        )
        for name in ("plan.json", "parents.json", "population.json", "calibration.json"):
            mlflow.log_artifact(str(root / "bundle" / name), artifact_path="calibration")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        mlflow.log_metrics(
            {
                "rows": result["rows"],
                "eligible_rows": result["eligible_rows"],
                "full_calibration_label_passes": 1,
                "worker_seconds": perf_counter() - started,
                "worker_peak_rss_bytes": worker_peak_rss_bytes(),
                "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
            }
        )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result.update(
        {
            "worker_seconds": perf_counter() - started,
            "worker_peak_rss_bytes": worker_peak_rss_bytes(),
            "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
        }
    )
    write(root / "calibrate.json", result)
