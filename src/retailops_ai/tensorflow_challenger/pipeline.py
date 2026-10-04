"""Bounded isolated training, immutable attempts, checksummed CPU artifact and typed reload."""

import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import psutil  # type: ignore[import-untyped]
from numpy.typing import NDArray
from pydantic import ValidationError

import retailops_ai
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes, read_json
from retailops_ai.tensorflow_challenger.contract import (
    ChallengerManifest,
    ChallengerPolicy,
    ChallengerPrediction,
    Normalization,
)
from retailops_ai.tensorflow_challenger.dataset import (
    Window,
    fit_normalization,
    targets,
    transform_window,
)
from retailops_ai.tensorflow_challenger.dataset import (
    windows as validate_windows,
)

LOCK = "dependencies.lock"
DEFAULT_POLICY = ChallengerPolicy()


def environment_lock() -> bytes:
    resource = files("retailops_ai.tensorflow_challenger").joinpath(LOCK)
    if resource.is_file():
        return resource.read_bytes()
    root = Path(__file__).resolve().parents[3]
    return (root / "environments/tensorflow/uv.lock").read_bytes()


def implementation() -> dict[str, str]:
    return {
        name: hashlib.sha256(
            files("retailops_ai.tensorflow_challenger").joinpath(name).read_bytes()
        ).hexdigest()
        for name in (
            "contract.py",
            "dataset.py",
            "pipeline.py",
            "worker.py",
            "parents.py",
            "evaluation.py",
        )
    } | {
        name: hashlib.sha256(files("retailops_ai").joinpath(name).read_bytes()).hexdigest()
        for name in (
            "data_contracts/common.py",
            "data_contracts/identity.py",
            "evaluation_campaign/comparison.py",
            "forecasting/features_contract.py",
            "forecasting/manifest_contract.py",
            "forecasting/functional_preprocessing.py",
            "forecasting/preprocessing.py",
            "forecasting/manifests.py",
            "forecasting/functional_campaign.py",
            "forecasting/quality_v2.py",
            "forecasting/quality_v2_contract.py",
            "forecasting/manifest_io.py",
            "source_snapshot/files.py",
        )
    }


def _matrices(
    windows: tuple[Window, ...], state: Normalization
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
    x = np.asarray([transform_window(w, state) for w in windows], dtype=np.float32)
    labels = [targets(w, state) for w in windows]
    y = np.asarray([v for v, _ in labels], dtype=np.float32)
    mask = np.asarray([m for _, m in labels], dtype=np.float32)
    if not windows or not mask.any() or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise SnapshotError("tensorflow_no_evaluable_or_nonfinite_training_data")
    return x, y, mask


def _terminate(process: subprocess.Popen[bytes]) -> None:
    # Only the process tree created by this attempt is eligible for termination.
    try:
        monitor = psutil.Process(process.pid)
        descendants = monitor.children(recursive=True)
    except psutil.NoSuchProcess:
        descendants = []
    for child in reversed(descendants):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    if process.poll() is None:
        process.kill()
    process.wait()


def _supervise(root: Path, policy: ChallengerPolicy) -> dict[str, float | int]:
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT")
        if k in os.environ
    }
    env.update(
        {
            k: "1"
            for k in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "TF_NUM_INTRAOP_THREADS",
                "TF_NUM_INTEROP_THREADS",
            )
        }
    )
    env.update(
        {
            "CUDA_VISIBLE_DEVICES": "-1",
            "TF_CPP_MIN_LOG_LEVEL": "2",
            "KERAS_BACKEND": "tensorflow",
            "TF_ENABLE_ONEDNN_OPTS": "0",
            "MLFLOW_ENABLE_ARTIFACTS_PROGRESS_BAR": "false",
            "PYTHONPATH": str(Path(retailops_ai.__file__).parent.parent),
        }
    )
    started, peak, cpu = time.monotonic(), 0, 0.0
    observed_cpu: dict[int, float] = {}
    with (root / "worker.log").open("xb") as log:
        process = subprocess.Popen(  # noqa: S603 -- fixed interpreter/module and private numeric attempt
            [sys.executable, "-m", "retailops_ai.tensorflow_challenger.worker", str(root)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        monitor = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if time.monotonic() - started > policy.wall_seconds:
                    raise SnapshotError("tensorflow_wall_budget_exceeded")
                rss = 0
                try:
                    for child in (monitor, *monitor.children(recursive=True)):
                        try:
                            rss += child.memory_info().rss
                            times = child.cpu_times()
                            observed_cpu[child.pid] = max(
                                observed_cpu.get(child.pid, 0.0), times.user + times.system
                            )
                        except psutil.NoSuchProcess:
                            continue
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error as exc:
                    raise SnapshotError("tensorflow_resource_monitor_unavailable") from exc
                peak, cpu = max(peak, rss), sum(observed_cpu.values())
                if peak > policy.rss_bytes or cpu > policy.cpu_seconds:
                    raise SnapshotError("tensorflow_cpu_or_rss_budget_exceeded")
                time.sleep(0.05)
            if process.returncode != 0:
                raise SnapshotError("tensorflow_training_worker_failed")
        finally:
            _terminate(process)
    elapsed = time.monotonic() - started
    if elapsed > policy.wall_seconds:
        raise SnapshotError("tensorflow_final_wall_budget_exceeded")
    return {
        "wall_seconds": elapsed,
        "cpu_seconds": cpu,
        "peak_tree_rss_bytes": peak,
        "monitor_interval_seconds": 0.05,
        "cpu_threads": 1,
    }


def artifact_inventory(root: Path, maximum: int) -> dict[str, dict[str, int | str]]:
    inventory: dict[str, dict[str, int | str]] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SnapshotError("tensorflow_artifact_symlink")
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if total > maximum or len(inventory) >= 100:
            raise SnapshotError("tensorflow_artifact_budget_exceeded")
        raw = read_bytes(root, path.relative_to(root).as_posix(), maximum)
        inventory[path.relative_to(root).as_posix()] = {
            "size_bytes": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    if not {
        "MLmodel",
        "preprocessing.json",
        "dependencies.lock",
        "policy.json",
        "binding.json",
    }.issubset(inventory):
        raise SnapshotError("tensorflow_incomplete_model_bundle")
    return inventory


def fit_challenger(
    train: tuple[Window, ...],
    validation: tuple[Window, ...],
    *,
    fold: FoldPlan,
    feature_set_id: str,
    split_id: str,
    output: Path,
    policy: ChallengerPolicy = DEFAULT_POLICY,
) -> dict[str, Any]:
    if max(len(train), len(validation)) > policy.max_windows:
        raise SnapshotError("tensorflow_development_window_budget")
    # Validate role again at the public fitting boundary, including ineligible windows.
    for expected, windows in (("train", train), ("validation", validation)):
        if any(
            s.membership.role != expected
            or s.membership.fold != fold.name
            or fold.role(s.row.forecast_origin.date()) != expected
            for w in windows
            for s in w.samples
        ):
            raise SnapshotError("tensorflow_fit_requires_train_and_development_validation")
        validate_windows(
            ((s.row, s.membership, s.label, w.history) for w in windows for s in w.samples),
            fold=fold,
            role=expected,
            max_windows=policy.max_windows,
        )
    preparation_started = time.monotonic()
    state = fit_normalization(train, fold=fold, feature_set_id=feature_set_id, split_id=split_id)
    estimated_bytes = (len(train) + len(validation)) * (state.input_width + 28) * 4
    if estimated_bytes > policy.max_matrix_bytes:
        raise SnapshotError("tensorflow_matrix_budget_exceeded")
    matrices = dict(
        zip(
            ("x_train", "y_train", "mask_train", "x_validation", "y_validation", "mask_validation"),
            (*_matrices(train, state), *_matrices(validation, state)),
            strict=True,
        )
    )
    if sum(x.nbytes for x in matrices.values()) > policy.max_matrix_bytes:
        raise SnapshotError("tensorflow_matrix_budget_exceeded")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = output.resolve()
    binding = {
        "feature_set_id": feature_set_id,
        "split_id": split_id,
        "preprocessing_sha256": state.content_sha256(),
        "environment_lock_sha256": hashlib.sha256(environment_lock()).hexdigest(),
        "policy_sha256": canonical_sha256(policy.model_dump(mode="json")),
        "code_sha256": canonical_sha256(implementation()),
        "train_population_sha256": canonical_sha256(
            [s.row.model_dump(mode="json") for w in train for s in w.samples]
        ),
        "validation_population_sha256": canonical_sha256(
            [s.row.model_dump(mode="json") for w in validation for s in w.samples]
        ),
    }
    attempt: dict[str, Any] = {
        "status": "running",
        "binding": binding,
        "policy": policy.model_dump(mode="json"),
        "final_test_accessed": False,
        "promotion_allowed": False,
        "system": platform.system(),
        "machine": platform.machine(),
    }
    (output / "attempt.json").write_bytes(canonical_bytes(attempt))
    try:
        for name, matrix in matrices.items():
            np.save(output / (name + ".npy"), matrix, allow_pickle=False)
        (output / "policy.json").write_bytes(canonical_bytes(policy.model_dump(mode="json")))
        (output / "preprocessing.json").write_bytes(canonical_bytes(state.model_dump(mode="json")))
        (output / "binding.json").write_bytes(canonical_bytes(binding))
        (output / LOCK).write_bytes(environment_lock())
        preparation_seconds = time.monotonic() - preparation_started
        resources = _supervise(output, policy)
        worker = read_json(output, "worker_receipt.json")
        resources["peak_tree_rss_bytes"] = max(
            int(resources["peak_tree_rss_bytes"]), int(worker["peak_self_rss_bytes"])
        )
        resources["cpu_seconds"] = max(
            float(resources["cpu_seconds"]), float(worker["self_cpu_seconds"])
        )
        if (
            resources["peak_tree_rss_bytes"] > policy.rss_bytes
            or resources["cpu_seconds"] > policy.cpu_seconds
        ):
            raise SnapshotError("tensorflow_final_cpu_or_rss_budget_exceeded")
        inventory = artifact_inventory(output / "model", policy.artifact_bytes)
        descriptor = {
            "binding": binding,
            "policy": policy.model_dump(mode="json"),
            "files": inventory,
            "input_width": state.input_width,
            "output_order": ["mean", "median"],
        }
        manifest = {
            "schema_version": "ai09-tensorflow-artifact-1.0.0",
            "model_id": "model-sha256-" + canonical_sha256(descriptor),
            "descriptor": descriptor,
            "worker": worker,
            "resources": resources,
            "preparation_seconds": preparation_seconds,
            "artifact_bytes": sum(int(f["size_bytes"]) for f in inventory.values()),
            "status": "trained_and_reloaded_development_only",
            "deployment_status": "not_ready",
            "interval_status": "not_ready",
            "final_test_accessed": False,
            "promotion_allowed": False,
        }
        _verify_bundle(output, manifest)
        attempt["status"] = "succeeded"
        attempt["model_id"] = manifest["model_id"]
        (output / "attempt.json").write_bytes(canonical_bytes(attempt))
        with (output / "manifest.json").open("xb") as stream:
            stream.write(canonical_bytes(manifest))
            stream.flush()
            os.fsync(stream.fileno())
        return manifest
    except Exception as exc:
        attempt.update(
            status="failed",
            error_type=type(exc).__name__,
            error_code=str(exc) if isinstance(exc, SnapshotError) else "tensorflow_attempt_failed",
        )
        (output / "attempt.json").write_bytes(canonical_bytes(attempt))
        raise


def verify_artifact(root: Path) -> tuple[dict[str, Any], Normalization]:
    manifest = read_json(root, "manifest.json")
    attempt = read_json(root, "attempt.json")
    if attempt.get("status") != "succeeded" or attempt.get("model_id") != manifest.get("model_id"):
        raise SnapshotError("tensorflow_incomplete_or_failed_attempt")
    return _verify_bundle(root, manifest)


def _verify_bundle(root: Path, manifest: dict[str, Any]) -> tuple[dict[str, Any], Normalization]:
    try:
        manifest = ChallengerManifest.model_validate_json(json.dumps(manifest)).model_dump(
            mode="json"
        )
    except ValidationError as exc:
        raise SnapshotError("tensorflow_manifest_contract_invalid") from exc
    descriptor = manifest["descriptor"]
    policy = ChallengerPolicy.model_validate_json(json.dumps(descriptor["policy"]))
    if (
        manifest["model_id"] != "model-sha256-" + canonical_sha256(descriptor)
        or manifest.get("promotion_allowed") is not False
        or manifest.get("final_test_accessed") is not False
        or descriptor["files"] != artifact_inventory(root / "model", policy.artifact_bytes)
    ):
        raise SnapshotError("tensorflow_artifact_checksum_or_identity_mismatch")
    state = Normalization.model_validate_json(
        read_bytes(root / "model", "preprocessing.json", policy.artifact_bytes)
    )
    binding = descriptor["binding"]
    if (
        binding != read_json(root / "model", "binding.json")
        or binding["preprocessing_sha256"] != state.content_sha256()
        or binding["environment_lock_sha256"]
        != hashlib.sha256(read_bytes(root / "model", LOCK, policy.artifact_bytes)).hexdigest()
        or binding["policy_sha256"] != canonical_sha256(policy.model_dump(mode="json"))
        or read_json(root / "model", "policy.json") != policy.model_dump(mode="json")
        or descriptor["input_width"] != state.input_width
    ):
        raise SnapshotError("tensorflow_artifact_preprocessing_or_signature_mismatch")
    # Validate the actual supported MLflow input/output signature before loading any model.
    yaml = importlib.import_module("yaml")
    metadata = yaml.safe_load(read_bytes(root / "model", "MLmodel", policy.artifact_bytes))
    signature = metadata.get("signature", {})
    inputs = json.loads(signature.get("inputs", "[]"))
    outputs = json.loads(signature.get("outputs", "[]"))
    if (
        "keras" not in metadata.get("flavors", {})
        or metadata.get("metadata") != binding
        or len(inputs) != 1
        or inputs[0].get("tensor-spec") != {"dtype": "float32", "shape": [-1, state.input_width]}
        or len(outputs) != 1
        or outputs[0].get("tensor-spec") != {"dtype": "float32", "shape": [-1, 14, 2]}
    ):
        raise SnapshotError("tensorflow_mlflow_signature_mismatch")
    return manifest, state


class LoadedChallenger:
    def __init__(self, root: Path) -> None:
        self.manifest, self.state = verify_artifact(root)
        if (
            self.manifest["descriptor"]["binding"]["environment_lock_sha256"]
            != hashlib.sha256(environment_lock()).hexdigest()
        ):
            raise SnapshotError("tensorflow_reload_environment_lock_mismatch")
        if self.manifest["descriptor"]["binding"]["code_sha256"] != canonical_sha256(
            implementation()
        ):
            raise SnapshotError("tensorflow_reload_implementation_mismatch")
        tf = importlib.import_module("tensorflow")
        tf.config.set_visible_devices([], "GPU")
        if tf.config.threading.get_inter_op_parallelism_threads() != 1:
            tf.config.threading.set_inter_op_parallelism_threads(1)
        if tf.config.threading.get_intra_op_parallelism_threads() != 1:
            tf.config.threading.set_intra_op_parallelism_threads(1)
        self.model = importlib.import_module("mlflow.keras").load_model(
            str(root / "model"), load_model_kwargs={"compile": False, "safe_mode": True}
        )
        if tuple(self.model.input_shape) != (None, self.state.input_width) or tuple(
            self.model.output_shape
        ) != (None, 14, 2):
            raise SnapshotError("tensorflow_loaded_model_signature_mismatch")

    def predict(self, windows: tuple[Window, ...]) -> tuple[ChallengerPrediction, ...]:
        policy = ChallengerPolicy.model_validate_json(
            json.dumps(self.manifest["descriptor"]["policy"])
        )
        if not windows or len(windows) > policy.max_windows:
            raise SnapshotError("tensorflow_inference_window_budget")
        predictions: list[ChallengerPrediction] = []
        for start in range(0, len(windows), policy.batch_size):
            batch = windows[start : start + policy.batch_size]
            x = np.asarray([transform_window(w, self.state) for w in batch], dtype=np.float32)
            values = (
                np.asarray(self.model(x, training=False), dtype=np.float32)
                * self.state.target_scale
            )
            if (
                values.shape != (len(batch), 14, 2)
                or not np.isfinite(values).all()
                or (values < 0).any()
            ):
                raise SnapshotError("tensorflow_invalid_model_output")
            for w, outputs in zip(batch, values, strict=True):
                for s in w.samples:
                    point = outputs[s.row.horizon_days - 1]
                    predictions.append(
                        ChallengerPrediction(
                            **s.row.model_dump(
                                include=set(ChallengerPrediction.model_fields)
                                & set(type(s.row).model_fields)
                            ),
                            value=FunctionalForecast(
                                mean=float(point[0]) if s.membership.eligible else None,
                                median=float(point[1]) if s.membership.eligible else None,
                                interval=None,
                            ),
                        )
                    )
        return tuple(predictions)
