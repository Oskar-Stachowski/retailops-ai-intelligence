"""Fixed CPU worker: real Keras fitting and supported MLflow Keras serialization."""

import importlib
import json
import math
import platform
import resource
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy


def main(root: Path) -> None:
    started = time.monotonic()
    policy = ChallengerPolicy.model_validate_json((root / "policy.json").read_bytes())
    cpu_limit = max(1, math.ceil(policy.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    tf = importlib.import_module("tensorflow")
    tf.config.set_visible_devices([], "GPU")
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)
    tf.keras.utils.set_random_seed(policy.initialization_seed)
    tf.config.experimental.enable_op_determinism()
    keras = importlib.import_module("keras")
    mlflow = importlib.import_module("mlflow")
    flavor = importlib.import_module("mlflow.keras")
    signature_module = importlib.import_module("mlflow.models")
    matrices = {
        name: np.load(root / (name + ".npy"), allow_pickle=False)
        for name in (
            "x_train",
            "y_train",
            "mask_train",
            "x_validation",
            "y_validation",
            "mask_validation",
        )
    }
    x = matrices["x_train"]
    inputs = keras.Input(shape=(x.shape[1],), dtype="float32", name="pit_history_and_covariates")
    hidden = inputs
    for units in policy.hidden_units:
        hidden = keras.layers.Dense(units, activation="relu")(hidden)
    output = keras.layers.Dense(28, activation="softplus")(hidden)
    output = keras.layers.Reshape((14, 2), name="mean_median_scaled_units")(output)
    model = keras.Model(inputs, output)
    optimizer = keras.optimizers.Adam(learning_rate=policy.learning_rate)

    def loss(values: Any, targets: Any, mask: Any) -> Any:
        error = values - targets[..., None]
        costs = tf.square(error[..., 0]) + tf.abs(error[..., 1])
        return tf.reduce_sum(costs * mask) / tf.reduce_sum(mask)

    history: list[dict[str, float | int]] = []
    best, best_epoch, stale = float("inf"), 0, 0
    best_weights = None
    train_started = time.monotonic()
    for epoch in range(policy.epochs):
        # Frozen order avoids tf.data's extra thread pool and makes the replay auditable.
        for begin in range(0, len(x), policy.batch_size):
            end = begin + policy.batch_size
            batch_mask = matrices["mask_train"][begin:end]
            if not batch_mask.any():
                continue
            with tf.GradientTape() as tape:
                prediction = model(x[begin:end], training=True)
                objective = loss(prediction, matrices["y_train"][begin:end], batch_mask)
            gradients = tape.gradient(objective, model.trainable_variables)
            optimizer.apply_gradients(zip(gradients, model.trainable_variables, strict=True))
        # Evaluate in bounded batches; validation is explicitly development-only.
        total, count = 0.0, 0.0
        for begin in range(0, len(matrices["x_validation"]), policy.batch_size):
            end = begin + policy.batch_size
            masks = matrices["mask_validation"][begin:end]
            if not masks.any():
                continue
            value = model(matrices["x_validation"][begin:end], training=False)
            total += float(loss(value, matrices["y_validation"][begin:end], masks)) * float(
                masks.sum()
            )
            count += float(masks.sum())
        score = total / count
        if not np.isfinite(score):
            raise ValueError("tensorflow_nonfinite_validation_loss")
        history.append({"epoch": epoch + 1, "validation_loss": score})
        if score < best:
            best, best_epoch, stale = score, epoch + 1, 0
            best_weights = model.get_weights()
        else:
            stale += 1
            if stale >= policy.patience:
                break
    if best_weights is None:
        raise ValueError("tensorflow_no_best_epoch")
    model.set_weights(best_weights)
    training_seconds = time.monotonic() - train_started
    example = x[: min(2, len(x))]
    example_output = np.asarray(model(example, training=False), dtype=np.float32)
    signature = signature_module.infer_signature(example, example_output)
    metadata = json.loads((root / "binding.json").read_text())
    flavor.save_model(
        model,
        str(root / "model"),
        signature=signature,
        pip_requirements=[
            "tensorflow==2.20.0" if platform.system() == "Darwin" else "tensorflow-cpu==2.20.0",
            "keras==3.11.3",
            "mlflow-skinny==3.4.0",
            "numpy==2.2.6",
            "pandas==2.2.3",
            "scipy==1.17.1",
        ],
        metadata=metadata,
    )
    # The supported flavor, normalization and full hashed lock form one checked bundle.
    for name in ("preprocessing.json", "dependencies.lock", "policy.json", "binding.json"):
        (root / "model" / name).write_bytes((root / name).read_bytes())
    cold_started = time.monotonic()
    reloaded = flavor.load_model(
        str(root / "model"), load_model_kwargs={"compile": False, "safe_mode": True}
    )
    cold_load_seconds = time.monotonic() - cold_started
    loaded = np.asarray(reloaded(example, training=False), dtype=np.float32)
    np.testing.assert_allclose(loaded, example_output, rtol=1e-6, atol=1e-6)
    inference_started = time.monotonic()
    predictions = np.concatenate(
        [
            np.asarray(
                reloaded(
                    matrices["x_validation"][start : start + policy.batch_size], training=False
                ),
                dtype=np.float32,
            )
            for start in range(0, len(matrices["x_validation"]), policy.batch_size)
        ]
    )
    inference_seconds = time.monotonic() - inference_started
    np.save(root / "validation_prediction.npy", predictions, allow_pickle=False)
    mlflow.set_tracking_uri((root / "tracking").as_uri())
    mlflow.set_experiment("retailops/ai09-development-tensorflow")
    with mlflow.start_run(
        tags={"retailops.scope": policy.scope, "retailops.lifecycle": "experimental", **metadata}
    ) as run:
        mlflow.log_params(
            {
                "initialization_seed": policy.initialization_seed,
                "data_seed": policy.data_seed,
                "epochs_budget": policy.epochs,
                "batch_size": policy.batch_size,
                "hidden_units": str(policy.hidden_units),
                "trials": policy.trials,
            }
        )
        mlflow.log_metrics(
            {
                "best_validation_loss": best,
                "training_seconds": training_seconds,
                "cold_load_seconds": cold_load_seconds,
                "batch_inference_seconds": inference_seconds,
            }
        )
        mlflow.log_artifacts(str(root / "model"), artifact_path="challenger")
        run_id = run.info.run_id
    receipt = {
        "status": "passed",
        "training_seconds": training_seconds,
        "worker_seconds": time.monotonic() - started,
        "cold_load_seconds": cold_load_seconds,
        "batch_inference_seconds": inference_seconds,
        "best_epoch": best_epoch,
        "epochs_run": len(history),
        "history": history,
        "mlflow_run_id": run_id,
        "tensorflow_version": tf.__version__,
        "keras_version": keras.__version__,
        "mlflow_version": mlflow.__version__,
        "system": platform.system(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "determinism_enabled": True,
        "determinism_scope": "fixed_versions_platform_and_initialization_seed_not_cross_platform_bitwise",
        "visible_gpu_count": len(tf.config.get_visible_devices("GPU")),
        "input_shape": [None, x.shape[1]],
        "output_shape": [None, 14, 2],
        "output_order": ["mean", "median"],
        "reload_rtol": 1e-6,
        "reload_atol": 1e-6,
        "interval_status": "not_ready",
        "promotion_allowed": False,
        "final_test_accessed": False,
        "peak_self_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        * (1 if platform.system() == "Darwin" else 1024),
        "self_cpu_seconds": resource.getrusage(resource.RUSAGE_SELF).ru_utime
        + resource.getrusage(resource.RUSAGE_SELF).ru_stime,
    }
    (root / "worker_receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
