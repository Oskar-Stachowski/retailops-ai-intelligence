"""Complete-population CPU fitting, portable trees, Keras flavor and fresh-process reload."""

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import resource
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np


def _package_path() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _versions(plan: Any) -> dict[str, str]:
    from retailops_ai.tensorflow_challenger.pipeline import environment_lock

    if hashlib.sha256(environment_lock()).hexdigest() != plan.worker_environment_lock_sha256:
        raise ValueError("campaign_fit_worker_environment_lock_mismatch")
    expected = {
        "numpy": "2.2.6",
        "scipy": "1.17.1",
        "scikit-learn": "1.9.1",
        "mlflow-skinny": "3.4.0",
        "keras": "3.11.3",
        "tensorflow" if platform.system() == "Darwin" else "tensorflow-cpu": "2.20.0",
    }
    actual = {name: importlib.metadata.version(name) for name in expected}
    if actual != expected:
        raise ValueError("campaign_fit_worker_installed_versions_mismatch")
    return actual


def prepare(root: Path, request: dict[str, Any], plan: Any) -> dict[str, Any]:
    from contextlib import closing

    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.evaluation_campaign import campaign_fit_data as data
    from retailops_ai.evaluation_campaign.campaign_export_contract import (
        CampaignDevelopmentExportReceipt,
    )
    from retailops_ai.evaluation_campaign.campaign_generation_worker import write
    from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastManifest
    from retailops_ai.evaluation_campaign.physical_forecast import _index
    from retailops_ai.forecasting.manifests import verify_feature_set
    from retailops_ai.source_snapshot.files import read_bytes

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
        raise ValueError("campaign_fit_completed_export_manifest_mismatch")
    # This verifies feature/history covariates. It deliberately does not open
    # tune/calibration/development-evaluation outcome files during fitting.
    features = verify_feature_set(dataset / "features")
    if (
        features.feature_set_id != manifest.descriptor.feature_set_id
        or features.descriptor != manifest.descriptor.feature_descriptor
    ):
        raise ValueError("campaign_fit_feature_descriptor_mismatch")
    bundle, arrays = root / "bundle", root / "arrays"
    bundle.mkdir(mode=0o700)
    arrays.mkdir(mode=0o700)
    with closing(_index(root / "fit.sqlite", plan.max_index_bytes)) as db:
        counts = data.index_roles(db, dataset, manifest, plan)
        encoding = data.fit_encoding(db, plan)
        write(bundle / "encoding.json", encoding.model_dump(mode="json"))
        factory = data.tensorflow_matrices if plan.family == "tensorflow" else data.tree_matrices
        matrices = {role: factory(db, role, encoding, plan, arrays) for role in data.ROLES}
        db.commit()
    total = sum(np.load(p, allow_pickle=False, mmap_mode="r").nbytes for p in arrays.iterdir())
    if total > plan.max_matrix_bytes:
        raise ValueError("campaign_fit_combined_matrix_budget")
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    write(
        bundle / "binding.json",
        {
            "export_receipt_sha256": exported.content_sha256(),
            "dataset_id": exported.dataset_id,
            "feature_set_id": manifest.descriptor.feature_set_id,
            "source_recipe_sha256": plan.source_recipe_sha256,
            "runtime_code_sha256": exported.runtime_code_sha256,
            "source_parameters_sha256": canonical_sha256(exported.recipe.source.source_parameters),
        },
    )
    return {"counts": counts, "matrices": matrices, "encoding_sha256": encoding.content_sha256()}


def _arrays(root: Path, role: str, plan: Any) -> dict[str, Any]:
    from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastEncoding
    from retailops_ai.source_snapshot.files import read_bytes, regular_file

    result = {}
    names = ("x", "y", "mask") if plan.family == "tensorflow" else ("x", "y")
    for name in names:
        filename = role + "-" + name + ".npy"
        with regular_file(root / "arrays", filename) as stream:
            if os.fstat(stream.fileno()).st_size > plan.max_matrix_bytes + 4096:
                raise ValueError("campaign_fit_worker_array_file_limit")
        value = np.load(root / "arrays" / filename, allow_pickle=False, mmap_mode="r")
        expected = np.float32 if plan.family == "tensorflow" else np.float64
        if value.dtype != expected or value.ndim not in (1, 2):
            raise ValueError("campaign_fit_worker_array_dtype_or_rank")
        for start in range(0, len(value), 4096):
            if not np.isfinite(value[start : start + 4096]).all():
                raise ValueError("campaign_fit_worker_nonfinite_array")
        result[name] = value
    if plan.family == "tensorflow":
        if (
            result["x"].ndim != 2
            or not 1 <= len(result["x"]) <= plan.max_windows
            or result["y"].shape != (len(result["x"]), 14)
            or result["mask"].shape != result["y"].shape
            or not np.isin(result["mask"], [0.0, 1.0]).all()
            or not result["mask"].sum(axis=1).min() > 0
        ):
            raise ValueError("campaign_fit_worker_tensorflow_shapes_or_masks")
    elif (
        result["x"].ndim != 2
        or not 1 <= len(result["x"]) <= plan.max_train_rows
        or result["y"].shape != (len(result["x"]),)
    ):
        raise ValueError("campaign_fit_worker_tree_shapes")
    if (result["y"] < 0).any():
        raise ValueError("campaign_fit_worker_negative_label")
    encoding = CampaignForecastEncoding.model_validate_json(
        read_bytes(root / "bundle", "encoding.json")
    )
    width = (
        encoding.tensorflow_width
        if plan.family == "tensorflow"
        else len(encoding.output_columns) + 1
    )
    if result["x"].shape[1] != width:
        raise ValueError("campaign_fit_worker_encoding_dimension_mismatch")
    return result


def _tree_fit(root: Path, plan: Any) -> dict[str, Any]:
    from sklearn.ensemble import (  # type: ignore[import-untyped]
        HistGradientBoostingRegressor,
        RandomForestRegressor,
    )
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

    from retailops_ai.data_contracts.identity import canonical_bytes
    from retailops_ai.evaluation_campaign.campaign_fit_data import _memmap
    from retailops_ai.evaluation_campaign.campaign_generation_worker import write
    from retailops_ai.forecasting.model_trees import TreePredictor, export_estimator

    train, validation = _arrays(root, "train", plan), _arrays(root, "early_stopping", plan)
    heads = ("mean",) if plan.family == "rf" else ("mean", "median")
    costs = {}
    for head in heads:
        options = plan.hgb.model_dump()
        if head == "median":
            options.update(loss="quantile", quantile=0.5)
        started = perf_counter()
        with threadpool_limits(limits=1):
            native = (
                RandomForestRegressor(**plan.rf.model_dump(), random_state=plan.initialization_seed)
                if plan.family == "rf"
                else HistGradientBoostingRegressor(**options, random_state=plan.initialization_seed)
            )
            native.fit(train["x"], train["y"])
            training_seconds = perf_counter() - started
            family = "random_forest" if plan.family == "rf" else "hist_gradient_boosting"
            estimator = export_estimator(native, family, train["x"].shape[1])
            raw = canonical_bytes(estimator.model_dump(mode="json"))
            if len(raw) > plan.max_artifact_bytes:
                raise ValueError("campaign_fit_tree_artifact_budget")
            write(root / "bundle" / (head + ".json"), estimator.model_dump(mode="json"))
            portable = TreePredictor(estimator)
            maximum = 0.0
            for start in range(0, len(train["x"]), 4096):
                batch = train["x"][start : start + 4096]
                first, second = np.maximum(native.predict(batch), 0), portable.matrix(batch)
                np.testing.assert_allclose(first, second, rtol=1e-12, atol=1e-12)
                maximum = max(maximum, float(np.max(np.abs(first - second))))
            prediction = _memmap(
                root, head + "-prediction.npy", np.float64, (len(validation["x"]),)
            )
            inference_started = perf_counter()
            for start in range(0, len(prediction), 4096):
                prediction[start : start + 4096] = portable.matrix(
                    validation["x"][start : start + 4096]
                )
            prediction.flush()
            costs[head] = {
                "training_seconds": training_seconds,
                "batch_inference_seconds": perf_counter() - inference_started,
                "portable_max_absolute_error": maximum,
                "portable_verified_train_rows": len(train["x"]),
            }
            del native, portable
    return {"heads": list(heads), "head_costs": costs, "rf_median_supported": False}


def _tensorflow_fit(root: Path, plan: Any) -> dict[str, Any]:
    from retailops_ai.evaluation_campaign.campaign_fit_data import _memmap

    tf = importlib.import_module("tensorflow")
    tf.config.set_visible_devices([], "GPU")
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)
    tf.keras.utils.set_random_seed(plan.initialization_seed)
    tf.config.experimental.enable_op_determinism()
    keras = importlib.import_module("keras")
    flavor = importlib.import_module("mlflow.keras")
    signatures = importlib.import_module("mlflow.models")
    train, validation = _arrays(root, "train", plan), _arrays(root, "early_stopping", plan)
    inputs = keras.Input(shape=(train["x"].shape[1],), dtype="float32", name="asof_inputs")
    hidden = inputs
    for units in plan.hidden_units:
        hidden = keras.layers.Dense(units, activation="relu")(hidden)
    output = keras.layers.Reshape((14, 2))(keras.layers.Dense(28, activation="softplus")(hidden))
    model = keras.Model(inputs, output)
    optimizer = keras.optimizers.Adam(learning_rate=plan.learning_rate)

    def loss(values: Any, targets: Any, mask: Any) -> Any:
        error = values - targets[..., None]
        return tf.reduce_sum(
            (tf.square(error[..., 0]) + tf.abs(error[..., 1])) * mask
        ) / tf.reduce_sum(mask)

    best, best_epoch, stale = float("inf"), 0, 0
    best_weights, history = None, []
    started = perf_counter()
    for epoch in range(plan.epochs):
        for start in range(0, len(train["x"]), plan.batch_size):
            batch = slice(start, start + plan.batch_size)
            with tf.GradientTape() as tape:
                objective = loss(
                    model(train["x"][batch], training=True), train["y"][batch], train["mask"][batch]
                )
            gradients = tape.gradient(objective, model.trainable_variables)
            optimizer.apply_gradients(zip(gradients, model.trainable_variables, strict=True))
        total, count = 0.0, 0.0
        for start in range(0, len(validation["x"]), plan.batch_size):
            batch = slice(start, start + plan.batch_size)
            mask = validation["mask"][batch]
            value = model(validation["x"][batch], training=False)
            total += float(loss(value, validation["y"][batch], mask)) * float(mask.sum())
            count += float(mask.sum())
        score = total / count
        if not np.isfinite(score):
            raise ValueError("campaign_fit_nonfinite_validation_loss")
        history.append({"epoch": epoch + 1, "validation_loss": score})
        if score < best:
            best, best_epoch, stale, best_weights = score, epoch + 1, 0, model.get_weights()
        else:
            stale += 1
            if stale >= plan.patience:
                break
    if best_weights is None:
        raise ValueError("campaign_fit_no_best_epoch")
    model.set_weights(best_weights)
    training_seconds = perf_counter() - started
    example = np.asarray(train["x"][: min(2, len(train["x"]))])
    flavor.save_model(
        model,
        str(root / "bundle" / "keras"),
        signature=signatures.infer_signature(example, np.asarray(model(example, training=False))),
        pip_requirements=[name + "==" + version for name, version in _versions(plan).items()],
        metadata={
            "retailops.plan_sha256": plan.content_sha256(),
            "retailops.scope": "ai09-campaign-development",
        },
    )
    prediction = _memmap(
        root, "tensorflow-prediction.npy", np.float32, (len(validation["x"]), 14, 2)
    )
    inference_started = perf_counter()
    for start in range(0, len(prediction), plan.batch_size):
        batch = slice(start, start + plan.batch_size)
        prediction[batch] = np.asarray(
            model(validation["x"][batch], training=False), dtype=np.float32
        )
    prediction.flush()
    return {
        "heads": ["mean", "median"],
        "training_seconds": training_seconds,
        "batch_inference_seconds": perf_counter() - inference_started,
        "best_epoch": best_epoch,
        "epochs_run": len(history),
        "history": history,
        "determinism_enabled": True,
        "determinism_scope": "same_versions_platform_seed_not_cross_platform_bitwise",
        "visible_gpu_count": len(tf.config.get_visible_devices("GPU")),
    }


def fit(root: Path, plan: Any) -> dict[str, Any]:
    from retailops_ai.evaluation_campaign.campaign_generation_worker import read
    from retailops_ai.tensorflow_challenger.pipeline import environment_lock

    versions = _versions(plan)
    (root / "bundle" / "dependencies.lock").write_bytes(environment_lock())
    (root / "bundle" / "dependencies.lock").chmod(0o600)
    mlflow = importlib.import_module("mlflow")
    mlflow.set_tracking_uri((root / "tracking").as_uri())
    mlflow.set_experiment("retailops/ai09-campaign-development")
    preparation = read(root / "prepare.json")
    with mlflow.start_run(
        tags={
            "retailops.scope": "ai09-campaign-development",
            "retailops.lifecycle": "experimental",
            "retailops.family": plan.family,
        }
    ) as run:
        mlflow.log_params(
            {
                "family": plan.family,
                "initialization_seed": plan.initialization_seed,
                "plan_sha256": plan.content_sha256(),
                "population": plan.training_population,
                "encoding_sha256": preparation["encoding_sha256"],
                "worker_environment_lock_sha256": plan.worker_environment_lock_sha256,
            }
        )
        result = (
            _tensorflow_fit(root, plan) if plan.family == "tensorflow" else _tree_fit(root, plan)
        )
        metrics = {
            "train_eligible_rows": preparation["matrices"]["train"].get(
                "eligible_rows", preparation["matrices"]["train"].get("rows")
            ),
            "early_stopping_eligible_rows": preparation["matrices"]["early_stopping"].get(
                "eligible_rows", preparation["matrices"]["early_stopping"].get("rows")
            ),
        }
        if plan.family == "tensorflow":
            metrics.update(
                {
                    name: result[name]
                    for name in (
                        "training_seconds",
                        "batch_inference_seconds",
                        "best_epoch",
                        "epochs_run",
                    )
                }
            )
            for epoch in result["history"]:
                mlflow.log_metric(
                    "early_stopping_validation_loss", epoch["validation_loss"], step=epoch["epoch"]
                )
        else:
            for head, costs in result["head_costs"].items():
                metrics.update({head + "_" + name: value for name, value in costs.items()})
        mlflow.log_metrics(metrics)
        for item in (root / "bundle").rglob("*"):
            item.chmod(0o700 if item.is_dir() else 0o600)
        mlflow.log_metric(
            "artifact_bytes",
            sum(item.stat().st_size for item in (root / "bundle").rglob("*") if item.is_file()),
        )
        mlflow.log_artifacts(str(root / "bundle"), artifact_path="candidate")
        result["mlflow_run_id"] = run.info.run_id
    return {
        **result,
        "dependency_versions": versions,
        "system": platform.system(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def reload(root: Path, plan: Any) -> dict[str, Any]:
    from retailops_ai.forecasting.model_contract import LearnedEstimator
    from retailops_ai.forecasting.model_trees import TreePredictor
    from retailops_ai.source_snapshot.files import read_bytes

    started = perf_counter()
    _versions(plan)
    validation = _arrays(root, "early_stopping", plan)
    if plan.family == "tensorflow":
        tf = importlib.import_module("tensorflow")
        tf.config.set_visible_devices([], "GPU")
        tf.config.threading.set_inter_op_parallelism_threads(1)
        tf.config.threading.set_intra_op_parallelism_threads(1)
        flavor = importlib.import_module("mlflow.keras")
        model = flavor.load_model(
            str(root / "bundle" / "keras"), load_model_kwargs={"compile": False, "safe_mode": True}
        )
        saved = np.load(root / "tensorflow-prediction.npy", allow_pickle=False, mmap_mode="r")
        load_seconds = perf_counter() - started
        inference_started = perf_counter()
        for start in range(0, len(saved), plan.batch_size):
            batch = slice(start, start + plan.batch_size)
            actual = np.asarray(model(validation["x"][batch], training=False), dtype=np.float32)
            np.testing.assert_allclose(actual, saved[batch], rtol=1e-6, atol=1e-6)
        count = int(validation["mask"].sum())
    else:
        heads = ("mean",) if plan.family == "rf" else ("mean", "median")
        models = {
            head: TreePredictor(
                LearnedEstimator.model_validate_json(
                    read_bytes(root / "bundle", head + ".json", plan.max_artifact_bytes)
                )
            )
            for head in heads
        }
        load_seconds = perf_counter() - started
        inference_started = perf_counter()
        for head, model in models.items():
            saved = np.load(root / (head + "-prediction.npy"), allow_pickle=False, mmap_mode="r")
            for start in range(0, len(saved), 4096):
                batch = slice(start, start + 4096)
                np.testing.assert_allclose(
                    model.matrix(validation["x"][batch]), saved[batch], rtol=1e-12, atol=1e-12
                )
        count = len(validation["x"])
    return {
        "fresh_process_cold_load_seconds": load_seconds,
        "batch_inference_seconds": perf_counter() - inference_started,
        "reload_verified_eligible_rows": count,
        "reload_all_early_stopping_keys_verified": True,
    }


def main(phase: str, root: Path) -> None:
    _package_path()
    from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
    from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write

    request = read(root / "request.json")
    plan = CampaignForecastFitPlan.model_validate_json(json.dumps(request["plan"]))
    resource.setrlimit(
        resource.RLIMIT_CPU, (plan.resources.wall_seconds, plan.resources.wall_seconds)
    )
    started = perf_counter()
    if phase == "prepare":
        result = prepare(root, request, plan)
    elif phase == "fit":
        result = fit(root, plan)
    elif phase == "reload":
        mlflow = importlib.import_module("mlflow")
        mlflow.set_tracking_uri((root / "tracking").as_uri())
        with mlflow.start_run(run_id=read(root / "fit.json")["mlflow_run_id"]):
            result = reload(root, plan)
    else:
        raise ValueError("campaign_fit_unknown_phase")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    evidence = {
        **result,
        "phase": phase,
        "worker_seconds": perf_counter() - started,
        "worker_peak_rss_bytes": int(usage.ru_maxrss)
        * (1 if platform.system() == "Darwin" else 1024),
        "worker_cpu_seconds": usage.ru_utime + usage.ru_stime,
    }
    if phase != "prepare":
        mlflow = importlib.import_module("mlflow")
        mlflow.set_tracking_uri((root / "tracking").as_uri())
        run_id = (
            result["mlflow_run_id"] if phase == "fit" else read(root / "fit.json")["mlflow_run_id"]
        )
        with mlflow.start_run(run_id=run_id):
            mlflow.log_metrics(
                {
                    phase + "_" + name: evidence[name]
                    for name in ("worker_seconds", "worker_cpu_seconds", "worker_peak_rss_bytes")
                }
            )
            if phase == "reload":
                mlflow.log_metrics(
                    {
                        name: result[name]
                        for name in (
                            "fresh_process_cold_load_seconds",
                            "batch_inference_seconds",
                            "reload_verified_eligible_rows",
                        )
                    }
                )
    write(root / (phase + ".json"), evidence)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]).resolve())
