"""One measured Tune-only pass per frozen trial; framework models are never refitted."""

import hashlib
import importlib
import platform
import resource
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit_worker import _versions
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score import _verify_bundle
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScoreReceipt
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTunePlan
from retailops_ai.evaluation_campaign.campaign_tune_data import choose, paired_metrics
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastManifest
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes


def select(root: Path, request: dict[str, Any]) -> dict[str, Any]:
    plan = CampaignForecastTunePlan.model_validate_json(canonical_bytes(request["plan"]))
    _versions(plan)
    exported = CampaignDevelopmentExportReceipt.model_validate_json(
        canonical_bytes(request["exported"])
    )
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in request["scores"].items()
    }
    if set(scores) != set(plan.score_operation_ids) or set(request["bundles"]) != set(scores):
        raise SnapshotError("campaign_tune_worker_trial_inventory_mismatch")
    before = runtime_pin()
    if before.model_dump(mode="json") != request["runtime"]:
        raise SnapshotError("campaign_tune_worker_runtime_mismatch")
    dataset = Path(request["dataset"])
    raw = read_bytes(dataset, "manifest.json", 4 * 1024**2)
    manifest = PhysicalForecastManifest.model_validate_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != exported.manifest_sha256
        or manifest.dataset_id != exported.dataset_id
        or manifest.descriptor.recipe != exported.recipe
        or manifest.descriptor.runtime.code_sha256 != exported.runtime_code_sha256
        or exported.runtime_code_sha256 != before.code_sha256
    ):
        raise SnapshotError("campaign_tune_completed_export_manifest_mismatch")
    trials = []
    for operation in plan.score_operation_ids:
        score = scores[operation]
        if (
            score.operation_id != operation
            or score.plan.role != "tune"
            or score.plan.source_recipe_sha256 != plan.source_recipe_sha256
            or score.dataset_id != exported.dataset_id
            or score.export_receipt_sha256 != exported.content_sha256()
            or score.runtime_code_sha256 != before.code_sha256
        ):
            raise SnapshotError("campaign_tune_worker_score_binding_mismatch")
        bundle = Path(request["bundles"][operation])
        _verify_bundle(bundle, score)
        trials.append(paired_metrics(dataset, bundle, manifest, score, plan))
    selection = choose(trials, scores, plan)
    if runtime_pin() != before:
        raise SnapshotError("campaign_tune_worker_runtime_changed")
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    write(
        bundle / "parents.json",
        {
            "export_receipt_sha256": exported.content_sha256(),
            "scores": {
                key: scores[key].model_dump(mode="json") for key in plan.score_operation_ids
            },
        },
    )
    write(bundle / "metrics.json", {"trials": trials})
    write(bundle / "selection.json", selection.model_dump(mode="json"))
    return {
        **{
            key: trials[0][key]
            for key in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
                "baseline_predictions_sha256",
            )
        },
        "selection": selection.model_dump(mode="json"),
        "trial_count": len(trials),
        "full_tune_label_passes": len(trials),
        "calibration_label_passes": 0,
        "independent_or_final_label_passes": 0,
    }


def main(root: Path) -> None:
    started = perf_counter()
    request = read(root / "request.json")
    mlflow = importlib.import_module("mlflow")
    mlflow.set_tracking_uri((root / "tracking").as_uri())
    experiment = mlflow.create_experiment(
        "ai09-audited-tune-selection",
        artifact_location=(root / "tracking-artifacts").as_uri(),
    )
    with mlflow.start_run(experiment_id=experiment) as run:
        result = select(root, request)
        result["mlflow_run_id"] = run.info.run_id
        mlflow.log_params(
            {
                "role": "tune",
                "calibration_fitted": False,
                "trial_count": result["trial_count"],
                "status": result["selection"]["status"],
                "plan_sha256": CampaignForecastTunePlan.model_validate_json(
                    canonical_bytes(request["plan"])
                ).content_sha256(),
                "dataset_id": request["exported"]["dataset_id"],
            }
        )
        for name in ("plan.json", "parents.json", "metrics.json", "selection.json"):
            mlflow.log_artifact(str(root / "bundle" / name), artifact_path="tune")
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result.update(
            {
                "worker_seconds": perf_counter() - started,
                "worker_peak_rss_bytes": int(usage.ru_maxrss)
                * (1 if platform.system() == "Darwin" else 1024),
                "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
            }
        )
        mlflow.log_metrics(
            {
                key: result[key]
                for key in (
                    "rows",
                    "eligible_rows",
                    "full_tune_label_passes",
                    "worker_seconds",
                    "worker_peak_rss_bytes",
                    "worker_cpu_seconds",
                )
            }
        )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result.update(
        {
            "worker_seconds": perf_counter() - started,
            "worker_peak_rss_bytes": int(usage.ru_maxrss)
            * (1 if platform.system() == "Darwin" else 1024),
            "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
        }
    )
    write(root / "select.json", result)
