"""Fresh CPU reload in a bounded process; the comparator never imports TensorFlow."""

import gc
import hashlib
import importlib
import io
import math
import os
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
import psutil  # type: ignore[import-untyped]

import retailops_ai
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.development_contract import DevelopmentPrediction
from retailops_ai.forecasting.model_contract import ResourceReceipt
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes, read_json
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy
from retailops_ai.tensorflow_challenger.dataset import Window, population_sha256, transform_window
from retailops_ai.tensorflow_challenger.pipeline import (
    LoadedChallenger,
    _terminate,
    verify_artifact,
)


def _supervise(root: Path, policy: ChallengerPolicy) -> ResourceReceipt:
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
        CUDA_VISIBLE_DEVICES="-1",
        TF_CPP_MIN_LOG_LEVEL="2",
        KERAS_BACKEND="tensorflow",
        TF_ENABLE_ONEDNN_OPTS="0",
        PYTHONPATH=str(Path(retailops_ai.__file__).parent.parent),
    )
    started, peak = time.monotonic(), 0
    cpus: dict[int, float] = {}
    with (root / "worker.log").open("xb") as log:
        process = subprocess.Popen(  # noqa: S603 -- fixed interpreter/module and private numeric job
            [
                sys.executable,
                "-m",
                "retailops_ai.evaluation_campaign.development_inference",
                str(root),
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            monitor = psutil.Process(process.pid)
            while process.poll() is None:
                rss = 0
                try:
                    for child in (monitor, *monitor.children(recursive=True)):
                        try:
                            rss += child.memory_info().rss
                            t = child.cpu_times()
                            cpus[child.pid] = max(cpus.get(child.pid, 0.0), t.user + t.system)
                        except psutil.NoSuchProcess:
                            continue
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error as exc:
                    raise SnapshotError("comparison_inference_monitor_unavailable") from exc
                peak = max(peak, rss)
                if (
                    time.monotonic() - started > policy.wall_seconds
                    or peak > policy.rss_bytes
                    or sum(cpus.values()) > policy.cpu_seconds
                ):
                    raise SnapshotError("comparison_inference_budget_exceeded")
                time.sleep(0.05)
            if process.returncode != 0:
                raise SnapshotError("comparison_inference_worker_failed")
        finally:
            _terminate(process)
    measured = read_json(root, "receipt.json")
    elapsed = time.monotonic() - started
    cpu = max(sum(cpus.values()), measured["cpu_seconds"])
    peak = max(peak, measured["peak_rss_bytes"])
    if elapsed > policy.wall_seconds or cpu > policy.cpu_seconds or peak > policy.rss_bytes:
        raise SnapshotError("comparison_inference_final_budget_exceeded")
    return ResourceReceipt(wall_seconds=elapsed, cpu_seconds=cpu, peak_rss_bytes=peak)


def tensorflow_predictions(
    artifact: Path, validation: tuple[Window, ...]
) -> tuple[tuple[DevelopmentPrediction, ...], dict[str, Any]]:
    manifest, state = verify_artifact(artifact)
    policy = ChallengerPolicy.model_validate_json(canonical_bytes(manifest["descriptor"]["policy"]))
    if (
        not validation
        or len(validation) > policy.max_windows
        or any(s.membership.role != "validation" for w in validation for s in w.samples)
    ):
        raise SnapshotError("comparison_inference_validation_budget_or_role")
    if manifest["descriptor"]["binding"]["validation_population_sha256"] != population_sha256(
        validation
    ):
        raise SnapshotError("comparison_inference_validation_binding")
    if len(validation) * (state.input_width + 28) * 4 > policy.max_matrix_bytes:
        raise SnapshotError("comparison_inference_matrix_budget")
    with tempfile.TemporaryDirectory(prefix="ai09-cpu-inference-") as temporary:
        root = Path(temporary).resolve()
        x = np.lib.format.open_memmap(
            root / "input.npy",
            mode="w+",
            dtype=np.float32,
            shape=(len(validation), state.input_width),
        )
        for i, window in enumerate(validation):
            x[i] = transform_window(window, state)
        x.flush()
        del x
        (root / "job.json").write_bytes(
            canonical_bytes(
                {
                    "artifact": str(artifact.resolve()),
                    "model_id": manifest["model_id"],
                    "rows": len(validation),
                    "policy": policy.model_dump(mode="json"),
                    "input_sha256": hashlib.sha256(
                        read_bytes(root, "input.npy", policy.max_matrix_bytes + 1024)
                    ).hexdigest(),
                }
            )
        )
        # Parent verification and preprocessing can leave cyclic reader/cache state behind.
        # Reclaim it before the framework process, while keeping all bound input objects alive.
        gc.collect()
        resources = _supervise(root, policy)
        measured = read_json(root, "receipt.json")
        raw = read_bytes(root, "output.npy", policy.max_matrix_bytes + 1024)
        if (
            measured["output_sha256"] != hashlib.sha256(raw).hexdigest()
            or measured["model_id"] != manifest["model_id"]
        ):
            raise SnapshotError("comparison_inference_output_binding")
        values = np.load(io.BytesIO(raw), allow_pickle=False)
        if (
            values.dtype != np.float32
            or values.shape != (len(validation), 14, 2)
            or not np.isfinite(values).all()
            or (values < 0).any()
        ):
            raise SnapshotError("comparison_inference_invalid_output")
        output = tuple(
            DevelopmentPrediction(
                **s.row.model_dump(include=set(DevelopmentPrediction.model_fields)),
                value=FunctionalForecast(
                    mean=float(points[s.row.horizon_days - 1, 0])
                    if s.membership.eligible
                    else None,
                    median=float(points[s.row.horizon_days - 1, 1])
                    if s.membership.eligible
                    else None,
                    interval=None,
                ),
            )
            for window, points in zip(validation, values, strict=True)
            for s in window.samples
        )
        return output, {"resources": resources.model_dump(mode="json"), "worker": measured}


def worker(root: Path) -> None:
    job = read_json(root, "job.json")
    policy = ChallengerPolicy.model_validate_json(canonical_bytes(job["policy"]))
    limit = max(1, math.ceil(policy.cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (limit, limit))
    raw = read_bytes(root, "input.npy", policy.max_matrix_bytes + 1024)
    if hashlib.sha256(raw).hexdigest() != job["input_sha256"]:
        raise SnapshotError("comparison_inference_input_checksum")
    x = np.load(io.BytesIO(raw), allow_pickle=False)
    if (
        x.dtype != np.float32
        or x.ndim != 2
        or x.shape[0] != job["rows"]
        or not 1 <= len(x) <= policy.max_windows
        or x.nbytes + len(x) * 28 * 4 > policy.max_matrix_bytes
        or not np.isfinite(x).all()
    ):
        raise SnapshotError("comparison_inference_invalid_matrix")
    started = time.monotonic()
    loaded = LoadedChallenger(Path(job["artifact"]))
    if (
        loaded.manifest["model_id"] != job["model_id"]
        or x.shape[1] != loaded.state.input_width
        or loaded.manifest["descriptor"]["policy"] != policy.model_dump(mode="json")
    ):
        raise SnapshotError("comparison_inference_model_binding")
    cold_load = time.monotonic() - started
    values = np.empty((len(x), 14, 2), dtype=np.float32)
    for begin in range(0, len(x), policy.batch_size):
        batch = x[begin : begin + policy.batch_size]
        predicted = (
            np.asarray(loaded.model(batch, training=False), dtype=np.float32)
            * loaded.state.target_scale
        )
        if (
            predicted.shape != (len(batch), 14, 2)
            or not np.isfinite(predicted).all()
            or (predicted < 0).any()
        ):
            raise SnapshotError("comparison_inference_invalid_output")
        values[begin : begin + policy.batch_size] = predicted
    prediction_seconds = time.monotonic() - started - cold_load
    np.save(root / "output.npy", values, allow_pickle=False)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    (root / "receipt.json").write_bytes(
        canonical_bytes(
            {
                "model_id": job["model_id"],
                "validation_windows": len(x),
                "cold_load_seconds_including_framework_import": cold_load,
                "batch_prediction_seconds": prediction_seconds,
                "peak_rss_bytes": int(
                    usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
                ),
                "cpu_seconds": usage.ru_utime + usage.ru_stime,
                "output_sha256": hashlib.sha256(
                    read_bytes(root, "output.npy", policy.max_matrix_bytes + 1024)
                ).hexdigest(),
                "visible_gpu_count": len(
                    importlib.import_module("tensorflow").config.get_visible_devices("GPU")
                ),
                "threads": 1,
                "final_test_accessed": False,
            }
        )
    )


if __name__ == "__main__":
    worker(Path(sys.argv[1]))
