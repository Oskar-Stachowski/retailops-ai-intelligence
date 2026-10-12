"""Verify complete forecast artifacts without importing campaign orchestration."""

from pathlib import Path

from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastEncoding,
    CampaignForecastFitPlan,
    CampaignForecastFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import read
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, inventory, read_bytes


def _bundle_inventory(root: Path, maximum: int) -> tuple[dict[str, str], int]:
    hashes: dict[str, str] = {}
    total = 0
    names: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise SnapshotError("campaign_fit_invalid_model_artifact")
        if path.is_file():
            names.add(path.relative_to(root).as_posix())
    inventory(root, names)
    for name in sorted(names):
        size, digest = file_hash(root, name)
        hashes[name] = digest
        total += size
        if total > maximum:
            raise SnapshotError("campaign_fit_model_artifact_budget")
    if not hashes:
        raise SnapshotError("campaign_fit_model_artifact_empty")
    return hashes, total


def _verify_bundle_content(bundle: Path, receipt: CampaignForecastFitReceipt) -> None:
    from retailops_ai.forecasting.model_contract import LearnedEstimator

    files, size = _bundle_inventory(bundle, receipt.plan.max_artifact_bytes)
    if files != receipt.artifact_files or size != receipt.model_artifact_bytes:
        raise SnapshotError("campaign_fit_model_artifact_checksum_mismatch")
    plan = CampaignForecastFitPlan.model_validate_json(read_bytes(bundle, "plan.json"))
    encoding = CampaignForecastEncoding.model_validate_json(read_bytes(bundle, "encoding.json"))
    binding = read(bundle / "binding.json")
    if (
        plan != receipt.plan
        or encoding.content_sha256() != receipt.encoding_sha256
        or encoding.train_keys_sha256 != receipt.train_keys_sha256
        or encoding.train_rows != receipt.train_eligible_rows
        or binding.get("export_receipt_sha256") != receipt.export_receipt_sha256
        or binding.get("dataset_id") != receipt.dataset_id
        or binding.get("source_recipe_sha256") != receipt.plan.source_recipe_sha256
        or binding.get("runtime_code_sha256") != receipt.runtime_code_sha256
        or files.get("dependencies.lock") != receipt.plan.worker_environment_lock_sha256
    ):
        raise SnapshotError("campaign_fit_model_artifact_binding_mismatch")
    if plan.family != "tensorflow":
        heads = ("mean",) if plan.family == "rf" else ("mean", "median")
        for head in heads:
            estimator = LearnedEstimator.model_validate_json(
                read_bytes(bundle, head + ".json", plan.max_artifact_bytes)
            )
            if (
                estimator.family
                != ("random_forest" if plan.family == "rf" else "hist_gradient_boosting")
                or estimator.feature_count != len(encoding.output_columns) + 1
            ):
                raise SnapshotError("campaign_fit_model_artifact_tree_dimension_mismatch")
        if plan.family == "rf" and "median.json" in files:
            raise SnapshotError("campaign_fit_rf_has_no_fitted_median")
    elif "keras/MLmodel" not in files or "keras/data/model.keras" not in files:
        raise SnapshotError("campaign_fit_keras_flavor_artifact_missing")
    else:
        from retailops_ai.evaluation_campaign.campaign_forecast_artifacts import (
            verify_keras_signature,
        )

        verify_keras_signature(bundle, plan, encoding)
