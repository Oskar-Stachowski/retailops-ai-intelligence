"""Fresh CPU inference over the complete authorized role, with bounded window batches."""

import hashlib
import importlib
import json
import os
import platform
import resource
import sys
import zlib
from contextlib import closing
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_fit import _verify_bundle_content
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastEncoding,
    CampaignForecastFitReceipt,
    Family,
)
from retailops_ai.evaluation_campaign.campaign_fit_worker import _versions
from retailops_ai.evaluation_campaign.campaign_forecast_inference import (
    EMPTY as EMPTY,
)
from retailops_ai.evaluation_campaign.campaign_forecast_inference import (
    InferenceRecord,
    InferenceWindow,
    infer_functionals,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
    CampaignForecastScorePlan,
)
from retailops_ai.evaluation_campaign.campaign_score_data import Window, batches, index_role
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastExample,
    PhysicalForecastManifest,
)
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.evaluation_campaign.physical_versions import check_index
from retailops_ai.forecasting.manifests import verify_feature_set
from retailops_ai.forecasting.model_contract import LearnedEstimator
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_bytes


def prepare(root: Path, request: dict[str, Any], plan: CampaignForecastScorePlan) -> dict[str, Any]:
    from retailops_ai.evaluation_campaign.campaign_export_contract import (
        CampaignDevelopmentExportReceipt,
    )

    dataset = Path(request["dataset"])
    exported = CampaignDevelopmentExportReceipt.model_validate_json(json.dumps(request["exported"]))
    raw = read_bytes(dataset, "manifest.json", 4 * 1024**2)
    manifest = PhysicalForecastManifest.model_validate_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != exported.manifest_sha256
        or manifest.dataset_id != exported.dataset_id
        or manifest.descriptor.recipe != exported.recipe
        or manifest.descriptor.runtime.code_sha256 != exported.runtime_code_sha256
    ):
        raise SnapshotError("campaign_score_completed_export_manifest_mismatch")
    features = verify_feature_set(dataset / "features")
    if (
        features.feature_set_id != manifest.descriptor.feature_set_id
        or features.descriptor != manifest.descriptor.feature_descriptor
    ):
        raise SnapshotError("campaign_score_feature_descriptor_mismatch")
    with closing(_index(root / "score.sqlite", plan.max_index_bytes)) as db:
        result = index_role(db, dataset, manifest, plan)
        db.commit()
    return result


def load_models(
    request: dict[str, Any],
) -> tuple[dict[Family, CampaignForecastEncoding], dict[str, Any]]:
    encodings: dict[Family, CampaignForecastEncoding] = {}
    models: dict[str, Any] = {}
    for family in FAMILIES:
        bundle = Path(request["bundles"][family])
        receipt = CampaignForecastFitReceipt.model_validate_json(
            json.dumps(request["fits"][family])
        )
        _versions(receipt.plan)
        _verify_bundle_content(bundle, receipt)
        encodings[family] = CampaignForecastEncoding.model_validate_json(
            read_bytes(bundle, "encoding.json")
        )
    if (
        len(
            {(e.train_keys_sha256, e.train_labels_sha256, e.train_rows) for e in encodings.values()}
        )
        != 1
    ):
        raise SnapshotError("campaign_score_fitted_training_population_mismatch")
    for family in FAMILIES:
        bundle = Path(request["bundles"][family])
        receipt = CampaignForecastFitReceipt.model_validate_json(
            json.dumps(request["fits"][family])
        )
        if family != "tensorflow":
            for head in ("mean",) if family == "rf" else ("mean", "median"):
                models[family + ":" + head] = TreePredictor(
                    LearnedEstimator.model_validate_json(
                        read_bytes(bundle, head + ".json", receipt.plan.max_artifact_bytes)
                    )
                )
        else:
            tf = importlib.import_module("tensorflow")
            tf.config.set_visible_devices([], "GPU")
            tf.config.threading.set_inter_op_parallelism_threads(1)
            tf.config.threading.set_intra_op_parallelism_threads(1)
            flavor = importlib.import_module("mlflow.keras")
            models["tensorflow"] = flavor.load_model(
                str(bundle / "keras"), load_model_kwargs={"compile": False, "safe_mode": True}
            )
    return encodings, models


def predict_batch(
    batch: list[Window], encodings: dict[Family, CampaignForecastEncoding], models: dict[str, Any]
) -> list[CampaignForecastRawPrediction]:
    inputs = []
    for records, history in batch:
        for _, _, example in records:
            if example.outcome is None:
                raise SnapshotError("campaign_score_role_outcome_missing")
            if example.membership.role not in ("tune", "calibration"):
                raise SnapshotError("campaign_score_prediction_requires_tune_or_calibration")
        inputs.append(
            InferenceWindow(
                tuple(
                    InferenceRecord(key, row, example.outcome.eligible)
                    for key, row, example in records
                    if example.outcome is not None
                ),
                history,
            )
        )
    functionals = infer_functionals(inputs, encodings, models)
    predictions = []
    for records, _ in batch:
        for key, row, example in records:
            outcome = example.outcome
            if outcome is None:
                raise SnapshotError("campaign_score_role_outcome_missing")
            role = example.membership.role
            if role not in ("tune", "calibration"):
                raise SnapshotError("campaign_score_prediction_requires_tune_or_calibration")
            predictions.append(
                CampaignForecastRawPrediction(
                    **row.model_dump(include=set(ForecastKey.model_fields)),
                    role=role,
                    example_sha256=canonical_sha256(example.model_dump(mode="json")),
                    eligible=outcome.eligible,
                    exclusion_reasons=outcome.reasons,
                    values=functionals[key],
                )
            )
    return predictions


def predict(root: Path, request: dict[str, Any], plan: CampaignForecastScorePlan) -> dict[str, Any]:
    started = perf_counter()
    encodings, models = load_models(request)
    cold_load = perf_counter() - started
    inference_seconds = 0.0
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    with closing(_index(root / "score.sqlite", plan.max_index_bytes)) as db:
        db.execute("CREATE TABLE predictions(key BLOB PRIMARY KEY,body BLOB)")
        for batch in batches(db, plan.batch_windows):
            inference_started = perf_counter()
            predictions = predict_batch(batch, encodings, models)
            inference_seconds += perf_counter() - inference_started
            for prediction in predictions:
                key = membership_key(prediction)
                db.execute(
                    "INSERT INTO predictions VALUES(?,?)",
                    (key, zlib.compress(canonical_bytes(prediction.model_dump(mode="json")), 1)),
                )
            check_index(db, plan.max_index_bytes)
        expected = read(root / "prepare.json")
        metrics, keys, eligible_keys = RawMetrics(), hashlib.sha256(), hashlib.sha256()
        rows = eligible_rows = output_bytes = 0
        descriptor = os.open(
            bundle / "predictions.jsonl",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            for key, body, example_raw in db.execute(
                "SELECT p.key,p.body,e.body FROM predictions p JOIN examples e ON p.key=e.key ORDER BY p.key"
            ):
                row = CampaignForecastRawPrediction.model_validate_json(zlib.decompress(body))
                example = PhysicalForecastExample.model_validate_json(zlib.decompress(example_raw))
                if (
                    row.example_sha256 != canonical_sha256(example.model_dump(mode="json"))
                    or example.outcome is None
                    or row.eligible != example.outcome.eligible
                    or row.exclusion_reasons != example.outcome.reasons
                ):
                    raise SnapshotError("campaign_score_prediction_example_binding")
                metrics.add(row, example.outcome.label.observed_sales_units)
                raw = canonical_bytes(row.model_dump(mode="json")) + b"\n"
                output_bytes += len(raw)
                if output_bytes > plan.max_output_bytes:
                    raise SnapshotError("campaign_score_output_budget")
                stream.write(raw)
                keys.update(key + b"\n")
                rows += 1
                if row.eligible:
                    eligible_rows += 1
                    eligible_keys.update(key + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if (rows, eligible_rows, keys.hexdigest(), eligible_keys.hexdigest()) != (
            expected["rows"],
            expected["eligible_rows"],
            expected["keys_sha256"],
            expected["eligible_keys_sha256"],
        ):
            raise SnapshotError("campaign_score_dropped_or_changed_common_key")
        db.commit()
    write(bundle / "metrics.json", metrics.result())
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    fits = {
        f: CampaignForecastFitReceipt.model_validate_json(json.dumps(request["fits"][f]))
        for f in FAMILIES
    }
    write(
        bundle / "parents.json",
        {
            "export_receipt_sha256": canonical_sha256(request["exported"]),
            "fit_receipt_sha256": {f: fits[f].content_sha256() for f in FAMILIES},
            "model_artifact_sha256": {f: fits[f].model_artifact_sha256 for f in FAMILIES},
        },
    )
    return {
        **{
            k: expected[k]
            for k in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
            )
        },
        "fresh_process_cold_load_seconds": cold_load,
        "batch_inference_seconds": inference_seconds,
        "all_models_share_all_role_keys": True,
    }


def main(phase: str, root: Path) -> None:
    request = read(root / "request.json")
    plan = CampaignForecastScorePlan.model_validate_json(json.dumps(request["plan"]))
    resource.setrlimit(
        resource.RLIMIT_CPU, (plan.resources.wall_seconds, plan.resources.wall_seconds)
    )
    started = perf_counter()
    if phase == "prepare":
        result = prepare(root, request, plan)
    elif phase == "predict":
        mlflow = importlib.import_module("mlflow")
        mlflow.set_tracking_uri((root / "tracking").as_uri())
        mlflow.set_experiment("ai09-campaign-raw-forecast-score")
        with mlflow.start_run() as run:
            mlflow.log_params(
                {
                    "plan_sha256": plan.content_sha256(),
                    "role": plan.role,
                    "scope": "raw_development_diagnostic_not_qualification",
                }
            )
            result = predict(root, request, plan)
            result["mlflow_run_id"] = run.info.run_id
            mlflow.log_metrics(
                {
                    k: result[k]
                    for k in (
                        "rows",
                        "eligible_rows",
                        "fresh_process_cold_load_seconds",
                        "batch_inference_seconds",
                    )
                }
            )
            # The complete immutable payload stays in the audited artifact directory.
            # Tracking binds it by digest instead of duplicating a multi-GiB dataset.
            mlflow.log_param(
                "predictions_sha256", file_hash(root / "bundle", "predictions.jsonl")[1]
            )
            for name in ("metrics.json", "plan.json", "parents.json"):
                mlflow.log_artifact(str(root / "bundle" / name), artifact_path="diagnostic")
    else:
        raise SnapshotError("campaign_score_unknown_phase")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    evidence = {
        **result,
        "phase": phase,
        "worker_seconds": perf_counter() - started,
        "worker_peak_rss_bytes": int(usage.ru_maxrss)
        * (1 if platform.system() == "Darwin" else 1024),
        "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
    }
    if phase == "predict":
        with mlflow.start_run(run_id=result["mlflow_run_id"]):
            mlflow.log_metrics(
                {
                    k: evidence[k]
                    for k in ("worker_seconds", "worker_peak_rss_bytes", "worker_cpu_seconds")
                }
            )
    write(root / (phase + ".json"), evidence)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]).resolve())
