"""Fresh, bounded evaluation phases; inference receives covariates, never actuals."""

import hashlib
import importlib
import json
import os
import platform
import resource
import sqlite3
import sys
import zlib
from contextlib import closing, nullcontext
from pathlib import Path
from time import perf_counter
from typing import Any

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign.campaign_calibration_data import interval
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastEvaluationPrediction,
    CampaignForecastFrozenConfiguration,
    CampaignForecastReference,
    CampaignForecastTrialBinding,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_metrics import (
    EvaluationSegment,
    ForecastMetrics,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportReceipt,
    FinalForecastManifest,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitReceipt
from retailops_ai.evaluation_campaign.campaign_forecast_inference import infer_functionals
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.evaluation_campaign.campaign_score_worker import load_models
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key, runtime_pin
from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastManifest
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.forecasting.manifests import verify_feature_set
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, read_bytes, regular_file

POPULATION = (
    "rows",
    "eligible_rows",
    "keys_sha256",
    "eligible_keys_sha256",
    "role_population_sha256",
)


def _readonly(path: Path) -> sqlite3.Connection:
    with regular_file(path.parent, path.name) as stream:
        if os.fstat(stream.fileno()).st_mode & 0o077:
            raise SnapshotError("campaign_evaluation_private_index_required")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA cache_size=-4096")
    db.execute("PRAGMA temp_store=FILE")
    return db


def _indexes(paths: tuple[Path, ...], plan: CampaignForecastEvaluationPlan, extra: int = 0) -> None:
    if type(extra) is not int or extra < 0:
        raise SnapshotError("campaign_evaluation_invalid_index_overhead")
    size = extra
    for path in paths:
        with regular_file(path.parent, path.name) as stream:
            size += os.fstat(stream.fileno()).st_size
    if size > plan.max_index_bytes:
        raise SnapshotError("campaign_evaluation_combined_index_budget")


def prepare(
    root: Path, request: dict[str, Any], plan: CampaignForecastEvaluationPlan
) -> dict[str, Any]:
    dataset = Path(request["dataset"])
    exported = (
        CampaignFinalExportReceipt if plan.phase == "final" else CampaignDevelopmentExportReceipt
    ).model_validate_json(canonical_bytes(request["exported"]))
    raw = read_bytes(dataset, "manifest.json", 4 * 1024**2)
    manifest = (
        FinalForecastManifest if plan.phase == "final" else PhysicalForecastManifest
    ).model_validate_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != exported.manifest_sha256
        or manifest.dataset_id != exported.dataset_id
        or manifest.descriptor.recipe != exported.recipe
        or manifest.descriptor.runtime.code_sha256 != exported.runtime_code_sha256
        or exported.operation_id != plan.export_operation_id
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("campaign_evaluation_completed_export_manifest_mismatch")
    features = verify_feature_set(dataset / "features")
    if (
        features.feature_set_id != manifest.descriptor.feature_set_id
        or features.descriptor != manifest.descriptor.feature_descriptor
    ):
        raise SnapshotError("campaign_evaluation_feature_descriptor_mismatch")
    with (
        closing(_index(root / "inputs.sqlite", plan.max_index_bytes)) as inputs,
        closing(_index(root / "actuals.sqlite", plan.max_index_bytes)) as actuals,
    ):
        result = data.index_role(inputs, actuals, dataset, manifest, plan)
    result["inputs_sha256"] = file_hash(root, "inputs.sqlite")[1]
    result["actuals_sha256"] = file_hash(root, "actuals.sqlite")[1]
    result["full_role_label_file_passes"] = 1
    return result


def _input_path(request: dict[str, Any]) -> Path:
    path = Path(request["inputs"])
    if file_hash(path.parent, path.name)[1] != request["population"]["inputs_sha256"]:
        raise SnapshotError("campaign_evaluation_prepared_input_checksum_mismatch")
    return path


def _configuration(
    request: dict[str, Any], plan: CampaignForecastEvaluationPlan
) -> CampaignForecastFrozenConfiguration:
    configuration = CampaignForecastFrozenConfiguration.model_validate_json(
        canonical_bytes(request["configuration"])
    )
    if (
        configuration.content_sha256() != plan.frozen_configuration_sha256
        or configuration.quality_policy_sha256 != plan.quality_policy.content_sha256()
        or configuration.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
        or configuration.runtime_code_sha256 != request["runtime"]["code_sha256"]
        or plan.phase == "development"
        and configuration.development_source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("campaign_evaluation_frozen_configuration_plan_mismatch")
    return configuration


def _trial(
    request: dict[str, Any], configuration: CampaignForecastFrozenConfiguration
) -> CampaignForecastTrialBinding:
    trial = CampaignForecastTrialBinding.model_validate_json(canonical_bytes(request["trial"]))
    if trial not in configuration.trials:
        raise SnapshotError("campaign_evaluation_trial_not_in_frozen_configuration")
    return trial


def _checksum(path: Path, expected: str) -> None:
    if file_hash(path.parent, path.name)[1] != expected:
        raise SnapshotError("campaign_evaluation_phase_index_checksum_mismatch")


def predict(
    root: Path, request: dict[str, Any], plan: CampaignForecastEvaluationPlan
) -> dict[str, Any]:
    # This separate request intentionally contains no dataset, actual-index path,
    # outcome body or measured target. The trusted model call takes matrices only.
    if set(request) - {
        "plan",
        "runtime",
        "population",
        "trial",
        "fits",
        "bundles",
        "inputs",
        "index_overhead_bytes",
        "frozen_configuration_sha256",
    }:
        raise SnapshotError("campaign_evaluation_inference_request_contains_outcomes")
    if request["frozen_configuration_sha256"] != plan.frozen_configuration_sha256:
        raise SnapshotError("campaign_evaluation_inference_configuration_mismatch")
    trial = CampaignForecastTrialBinding.model_validate_json(canonical_bytes(request["trial"]))
    for family in FAMILIES:
        fit = CampaignForecastFitReceipt.model_validate_json(
            canonical_bytes(request["fits"][family])
        )
        if (
            fit.operation_id != trial.fit_operation_ids[family]
            or fit.plan.family != family
            or fit.content_sha256() != trial.fit_receipt_sha256[family]
            or fit.model_artifact_sha256 != trial.model_artifact_sha256[family]
            or fit.encoding_sha256 != trial.encoding_sha256[family]
            or fit.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
            or fit.runtime_code_sha256 != request["runtime"]["code_sha256"]
        ):
            raise SnapshotError("campaign_evaluation_frozen_trial_fit_binding_mismatch")
    input_path = _input_path(request)
    started = perf_counter()
    encodings, models = load_models(request)
    cold_load = perf_counter() - started
    inference = 0.0
    with (
        closing(_readonly(input_path)) as inputs,
        closing(_index(root / "predictions.sqlite", plan.max_index_bytes)) as predictions,
    ):
        predictions.execute("CREATE TABLE predictions(key BLOB PRIMARY KEY,body BLOB)")
        for batch in data.batches(inputs, plan):
            started_batch = perf_counter()
            values = infer_functionals([window.inference() for window in batch], encodings, models)
            inference += perf_counter() - started_batch
            for window in batch:
                for record in window.records:
                    row = CampaignForecastTrialPrediction(
                        **record.row.model_dump(include=set(ForecastKey.model_fields)),
                        role=plan.role,
                        trial_tune_score_operation_id=trial.tune_score_operation_id,
                        example_sha256=record.example_sha256,
                        eligible=record.eligible,
                        exclusion_reasons=record.exclusion_reasons,
                        values=values[record.key],
                    )
                    predictions.execute(
                        "INSERT INTO predictions VALUES(?,?)",
                        (
                            record.key,
                            zlib.compress(canonical_bytes(row.model_dump(mode="json")), 1),
                        ),
                    )
            predictions.commit()
            _indexes(
                (Path(request["inputs"]), root / "predictions.sqlite"),
                plan,
                request["index_overhead_bytes"],
            )
        digest, baseline, keys, eligible_keys = (hashlib.sha256() for _ in range(4))
        rows = eligible = 0
        for key, body in predictions.execute("SELECT key,body FROM predictions ORDER BY key"):
            row = CampaignForecastTrialPrediction.model_validate_json(zlib.decompress(body))
            raw = canonical_bytes(row.model_dump(mode="json"))
            digest.update(raw + b"\n")
            baseline.update(
                canonical_bytes(
                    [
                        row.model_dump(mode="json", include=set(ForecastKey.model_fields)),
                        [v.model_dump(mode="json") for v in row.values[:3]],
                    ]
                )
                + b"\n"
            )
            keys.update(key + b"\n")
            rows += 1
            if row.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
        expected = request["population"]
        if (rows, eligible, keys.hexdigest(), eligible_keys.hexdigest()) != tuple(
            expected[k] for k in POPULATION[:4]
        ):
            raise SnapshotError("campaign_evaluation_trial_dropped_common_key")
    _checksum(input_path, request["population"]["inputs_sha256"])
    _indexes((input_path, root / "predictions.sqlite"), plan, request["index_overhead_bytes"])
    return {
        **{k: expected[k] for k in POPULATION},
        "prediction_trace_sha256": digest.hexdigest(),
        "baseline_trace_sha256": baseline.hexdigest(),
        "prediction_index_sha256": file_hash(root, "predictions.sqlite")[1],
        "fresh_process_cold_load_seconds": cold_load,
        "batch_inference_seconds": inference,
        "actual_index_passes": 0,
        "all_models_share_all_role_keys": True,
    }


def _selected(configuration: CampaignForecastFrozenConfiguration, head: str) -> tuple[str, int]:
    choice = (
        configuration.calibration.selection.mean
        if head == "mean"
        else configuration.calibration.selection.median
    )
    if choice is None:
        raise SnapshotError("campaign_evaluation_selected_functional_missing")
    return choice.score_operation_id or configuration.trials[
        0
    ].tune_score_operation_id, MODELS.index(choice.model)


def consume(
    root: Path, request: dict[str, Any], plan: CampaignForecastEvaluationPlan
) -> dict[str, Any]:
    configuration = _configuration(request, plan)
    trial = _trial(request, configuration)
    first = trial == configuration.trials[0]
    choices = {head: _selected(configuration, head) for head in ("mean", "median")}
    actual_path = Path(request["actuals"])
    if (
        file_hash(actual_path.parent, actual_path.name)[1]
        != request["population"]["actuals_sha256"]
    ):
        raise SnapshotError("campaign_evaluation_prepared_actual_checksum_mismatch")
    prediction_path = root / "predictions.sqlite"
    _checksum(prediction_path, request["predicted"]["prediction_index_sha256"])
    with (
        closing(_readonly(root / "predictions.sqlite")) as predicted,
        closing(_readonly(actual_path)) as actuals,
        closing(_index(Path(request["projection"]), plan.max_index_bytes)) as projection,
    ):
        if first:
            projection.execute(
                "CREATE TABLE selected(key BLOB PRIMARY KEY,identity BLOB,reference BLOB,mean REAL,median REAL,mean_seen INTEGER DEFAULT 0,median_seen INTEGER DEFAULT 0)"
            )
            projection.execute("CREATE TABLE consumed(trial TEXT PRIMARY KEY,trace TEXT)")
        completed = [
            item[0] for item in projection.execute("SELECT trial FROM consumed ORDER BY rowid")
        ]
        if completed != [
            item.tune_score_operation_id
            for item in configuration.trials[: configuration.trials.index(trial)]
        ]:
            raise SnapshotError("campaign_evaluation_trials_missing_repeated_or_out_of_order")
        statistics = {
            (str(h), model): ForecastMetrics() for h in (0, *range(1, 15)) for model in MODELS
        }
        counts = {str(h): [0, 0] for h in (0, *range(1, 15))}
        digest, baseline = hashlib.sha256(), hashlib.sha256()
        rows = eligible = 0
        for (key, body), actual in zip(
            predicted.execute("SELECT key,body FROM predictions ORDER BY key"),
            data.actuals(actuals, plan),
            strict=True,
        ):
            row = CampaignForecastTrialPrediction.model_validate_json(zlib.decompress(body))
            if (
                key != actual.key
                or membership_key(row) != key
                or row.role != plan.role
                or row.trial_tune_score_operation_id != trial.tune_score_operation_id
                or row.example_sha256 != actual.example_sha256
                or row.eligible != actual.eligible
            ):
                raise SnapshotError("campaign_evaluation_trial_actual_binding_mismatch")
            identity = canonical_bytes(
                row.model_dump(mode="json", exclude={"values", "trial_tune_score_operation_id"})
            )
            raw_baselines = canonical_bytes([v.model_dump(mode="json") for v in row.values[:3]])
            digest.update(canonical_bytes(row.model_dump(mode="json")) + b"\n")
            baseline.update(
                canonical_bytes(
                    [
                        row.model_dump(mode="json", include=set(ForecastKey.model_fields)),
                        [v.model_dump(mode="json") for v in row.values[:3]],
                    ]
                )
                + b"\n"
            )
            if first:
                projection.execute(
                    "INSERT INTO selected(key,identity,reference) VALUES(?,?,?)",
                    (key, identity, raw_baselines),
                )
            else:
                found = projection.execute(
                    "SELECT identity,reference FROM selected WHERE key=?", (key,)
                ).fetchone()
                if found is None or found != (identity, raw_baselines):
                    raise SnapshotError("campaign_evaluation_trial_identity_or_baselines_differ")
            for head in ("mean", "median"):
                operation, index = choices[head]
                if operation == trial.tune_score_operation_id:
                    # Both statements are fixed; no request can supply a column name.
                    seen = projection.execute(
                        "SELECT mean_seen FROM selected WHERE key=?"
                        if head == "mean"
                        else "SELECT median_seen FROM selected WHERE key=?",
                        (key,),
                    ).fetchone()[0]
                    if seen:
                        raise SnapshotError("campaign_evaluation_selected_head_written_twice")
                    projection.execute(
                        "UPDATE selected SET mean=?,mean_seen=1 WHERE key=?"
                        if head == "mean"
                        else "UPDATE selected SET median=?,median_seen=1 WHERE key=?",
                        (getattr(row.values[index], head), key),
                    )
            rows += 1
            eligible += int(row.eligible)
            for horizon in ("0", str(row.horizon_days)):
                counts[horizon][0] += 1
                counts[horizon][1] += int(row.eligible)
                if row.eligible and actual.actual is not None:
                    for model, forecast in zip(MODELS, row.values, strict=True):
                        statistics[horizon, model].add(
                            forecast, actual.actual, plan.quality_policy.nominal_coverage
                        )
            if rows % 256 == 0:
                projection.commit()
                _indexes(
                    (
                        Path(request["inputs"]),
                        actual_path,
                        Path(request["projection"]),
                        root / "predictions.sqlite",
                    ),
                    plan,
                )
        projection.commit()
        _indexes(
            (
                Path(request["inputs"]),
                actual_path,
                Path(request["projection"]),
                root / "predictions.sqlite",
            ),
            plan,
        )
        expected = request["population"]
        if (
            rows != expected["rows"]
            or eligible != expected["eligible_rows"]
            or digest.hexdigest() != request["predicted"]["prediction_trace_sha256"]
            or baseline.hexdigest() != request["predicted"]["baseline_trace_sha256"]
        ):
            raise SnapshotError("campaign_evaluation_consumed_trace_or_population_mismatch")
        _checksum(actual_path, expected["actuals_sha256"])
        _checksum(prediction_path, request["predicted"]["prediction_index_sha256"])
        projection.execute(
            "INSERT INTO consumed VALUES(?,?)", (trial.tune_score_operation_id, digest.hexdigest())
        )
        projection.commit()
        _indexes(
            (Path(request["inputs"]), actual_path, Path(request["projection"]), prediction_path),
            plan,
        )
    metrics = {
        "trial_tune_score_operation_id": trial.tune_score_operation_id,
        "role": plan.role,
        "rows": rows,
        "eligible_rows": eligible,
        "prediction_trace_sha256": digest.hexdigest(),
        "baseline_trace_sha256": baseline.hexdigest(),
        "architecture_reselected": False,
        "quality_qualified": False,
        "final_test_accessed": plan.phase == "final",
        "stage_ready": False,
        "raw_learned_intervals_calibrated": False,
        "segments": [
            {
                "horizon": "all" if h == "0" else h,
                "rows": n[0],
                "eligible_rows": n[1],
                "models": {model: statistics[h, model].result() for model in MODELS},
            }
            for h, n in counts.items()
        ],
    }
    path = Path(request["metrics_output"])
    write(path, metrics)
    return {
        **{k: expected[k] for k in POPULATION},
        "prediction_trace_sha256": digest.hexdigest(),
        "baseline_trace_sha256": baseline.hexdigest(),
        "metrics_sha256": file_hash(path.parent, path.name)[1],
        "actual_index_passes": 1,
        "selected_heads_written": True,
    }


def finalize(
    root: Path, request: dict[str, Any], plan: CampaignForecastEvaluationPlan
) -> dict[str, Any]:
    configuration = _configuration(request, plan)
    configuration_sha256 = plan.frozen_configuration_sha256
    selection = configuration.calibration.selection
    if (
        selection.median is None
        or selection.baseline_mean is None
        or selection.baseline_median is None
        or selection.baseline_interval is None
    ):
        raise SnapshotError("campaign_evaluation_frozen_reference_missing")
    segments = {
        str(h): EvaluationSegment(
            "global" if h == 0 else "horizon",
            retained_median_baseline=selection.median.score_operation_id is None,
            policy=plan.quality_policy,
            max_rows=plan.max_rows,
        )
        for h in (0, *range(1, 15))
    }
    actual_path = Path(request["actuals"])
    if (
        file_hash(actual_path.parent, actual_path.name)[1]
        != request["population"]["actuals_sha256"]
    ):
        raise SnapshotError("campaign_evaluation_prepared_actual_checksum_mismatch")
    bundle = Path(request["bundle"])
    keys, eligible_keys = hashlib.sha256(), hashlib.sha256()
    rows = eligible = output_bytes = 0
    descriptor = os.open(
        bundle / "predictions.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with (
        os.fdopen(descriptor, "wb") as stream,
        closing(_readonly(Path(request["projection"]))) as projection,
        closing(_readonly(actual_path)) as actuals,
    ):
        if [
            item[0] for item in projection.execute("SELECT trial FROM consumed ORDER BY rowid")
        ] != [trial.tune_score_operation_id for trial in configuration.trials]:
            raise SnapshotError("campaign_evaluation_all_frozen_trials_required")
        for record, actual in zip(
            projection.execute(
                "SELECT key,identity,reference,mean,median,mean_seen,median_seen FROM selected ORDER BY key"
            ),
            data.actuals(actuals, plan),
            strict=True,
        ):
            key, identity, references, mean, median, mean_seen, median_seen = record
            value = json.loads(identity)
            if (
                (mean_seen, median_seen) != (1, 1)
                or key != actual.key
                or value["example_sha256"] != actual.example_sha256
                or value["eligible"] != actual.eligible
            ):
                raise SnapshotError("campaign_evaluation_selected_prediction_incomplete")
            baselines = tuple(
                FunctionalForecast.model_validate_json(canonical_bytes(v))
                for v in json.loads(references)
            )
            if len(baselines) != 3:
                raise SnapshotError("campaign_evaluation_reference_inventory_mismatch")
            band = baselines[MODELS.index(selection.baseline_interval)]
            candidate = FunctionalForecast(
                mean=mean,
                median=median,
                interval=interval(configuration.calibration, value["horizon_days"], median)
                if median is not None
                else None,
            )
            reference = CampaignForecastReference(
                mean=baselines[MODELS.index(selection.baseline_mean)].mean,
                median=baselines[MODELS.index(selection.baseline_median)].median,
                interval=band.interval,
                interval_center=band.median if band.interval is not None else None,
            )
            row = CampaignForecastEvaluationPrediction.model_validate_json(
                canonical_bytes(
                    value
                    | {
                        "frozen_configuration_sha256": configuration_sha256,
                        "candidate": candidate.model_dump(mode="json"),
                        "reference": reference.model_dump(mode="json"),
                    }
                )
            )
            if row.role != plan.role or membership_key(row) != key:
                raise SnapshotError("campaign_evaluation_selected_key_or_role_mismatch")
            for horizon in ("0", str(row.horizon_days)):
                segments[horizon].add(row, actual.actual)
            raw = canonical_bytes(row.model_dump(mode="json")) + b"\n"
            output_bytes += len(raw)
            if output_bytes > plan.max_output_bytes:
                raise SnapshotError("campaign_evaluation_output_budget")
            stream.write(raw)
            rows += 1
            keys.update(key + b"\n")
            if row.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    expected = request["population"]
    _checksum(actual_path, expected["actuals_sha256"])
    if (rows, eligible, keys.hexdigest(), eligible_keys.hexdigest()) != tuple(
        expected[k] for k in POPULATION[:4]
    ):
        raise SnapshotError("campaign_evaluation_composite_population_mismatch")
    metrics = {
        "role": plan.role,
        "scope": "complete_key_global_horizon_component_pending_critical_segments_and_uncertainty",
        "critical_segment_inventory_complete": False,
        "block_uncertainty_complete": False,
        "quality_qualified": False,
        "final_test_accessed": plan.phase == "final",
        "stage_ready": False,
        "segments": [
            {"horizon": "all" if h == "0" else h, **segment.result()}
            for h, segment in segments.items()
        ],
    }
    write(bundle / "metrics.json", metrics)
    return {
        **{k: expected[k] for k in POPULATION},
        "actual_index_passes": 1,
        "all_frozen_trials_compared": True,
        "architecture_reselected": False,
    }


def _usage(started: float) -> dict[str, float | int]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "worker_seconds": perf_counter() - started,
        "worker_peak_rss_bytes": int(usage.ru_maxrss)
        * (1 if platform.system() == "Darwin" else 1024),
        "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
    }


def main(phase: str, root: Path) -> None:
    request = read(root / "request.json")
    plan = CampaignForecastEvaluationPlan.model_validate_json(canonical_bytes(request["plan"]))
    before = runtime_pin()
    if before.model_dump(mode="json") != request["runtime"]:
        raise SnapshotError("campaign_evaluation_worker_runtime_mismatch")
    resource.setrlimit(
        resource.RLIMIT_CPU, (plan.resources.wall_seconds, plan.resources.wall_seconds)
    )
    started = perf_counter()
    functions = {"prepare": prepare, "predict": predict, "consume": consume, "finalize": finalize}
    if phase not in functions:
        raise SnapshotError("campaign_evaluation_unknown_worker_phase")
    # Only prediction imports the locked MLflow/TF environment. Core preparation
    # and aggregation remain independent of it and retain no fitted model.
    mlflow: Any = None
    if phase == "predict":
        mlflow = importlib.import_module("mlflow")
        mlflow.set_tracking_uri((root / "tracking").as_uri())
        experiment = mlflow.create_experiment(
            "ai09-frozen-forecast-evaluation-" + plan.role,
            artifact_location=(root / "tracking-artifacts").as_uri(),
        )
        context = mlflow.start_run(experiment_id=experiment)
    else:
        context = nullcontext()
    with context as run:
        result = functions[phase](root, request, plan)
        if mlflow is not None:
            result["mlflow_run_id"] = run.info.run_id
            mlflow.log_params(
                {
                    "role": plan.role,
                    "plan_sha256": plan.content_sha256(),
                    "frozen_configuration_sha256": plan.frozen_configuration_sha256,
                    "trial_tune_score_operation_id": request["trial"]["tune_score_operation_id"],
                    "prediction_trace_sha256": result["prediction_trace_sha256"],
                    "baseline_trace_sha256": result["baseline_trace_sha256"],
                    "models_or_preprocessing_refitted": False,
                    "architecture_reselected": False,
                    **{
                        "fit_receipt_sha256_" + family: request["trial"]["fit_receipt_sha256"][
                            family
                        ]
                        for family in FAMILIES
                    },
                }
            )
            mlflow.log_metrics(
                {
                    **_usage(started),
                    **{
                        key: result[key]
                        for key in (
                            "rows",
                            "eligible_rows",
                            "fresh_process_cold_load_seconds",
                            "batch_inference_seconds",
                            "actual_index_passes",
                        )
                    },
                }
            )
    if runtime_pin() != before:
        raise SnapshotError("campaign_evaluation_worker_runtime_changed")
    result.update({"phase": phase, **_usage(started)})
    write(root / (phase + ".json"), result)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]).resolve())
