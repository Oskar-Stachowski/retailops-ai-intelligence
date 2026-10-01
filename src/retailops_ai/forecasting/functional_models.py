"""Bounded fitting of separately declared conditional means and quantiles."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from importlib.resources import files
from pathlib import Path

import numpy as np
import psutil  # type: ignore[import-untyped]
from numpy.typing import NDArray

import retailops_ai
from retailops_ai.forecasting.functional_contract import FunctionalPolicy, Head
from retailops_ai.forecasting.model_contract import (
    MAX_MODEL_BYTES,
    LearnedEstimator,
    ResourceReceipt,
)
from retailops_ai.forecasting.models import decode_model_json, model_code
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes, read_json


def functional_code() -> dict[str, str]:
    return {
        **model_code().code_files,
        **{
            "forecasting/" + name: hashlib.sha256(
                files("retailops_ai.forecasting").joinpath(name).read_bytes()
            ).hexdigest()
            for name in (
                "functional_contract.py",
                "functional_models.py",
                "functional_worker.py",
                "functional_preprocessing.py",
                "functional_recipe.py",
                "functional_campaign.py",
                "quality_v2.py",
                "quality_v2_contract.py",
                "features_store.py",
                "quality_contract.py",
                "quality_metrics.py",
            )
        },
    }


def fit_worker(
    x: NDArray[np.float64], y: NDArray[np.float64], head: Head, recipe: FunctionalPolicy
) -> tuple[LearnedEstimator, ResourceReceipt]:
    policy = recipe.model
    family = "random_forest" if head == "rf_mean" else "hist_gradient_boosting"
    if (
        x.ndim != 2
        or y.shape != (x.shape[0],)
        or x.shape[0] > policy.max_train_rows
        or x.nbytes + y.nbytes > policy.max_matrix_bytes
    ):
        raise SnapshotError("forecast_model_training_matrix_budget")
    with tempfile.TemporaryDirectory(prefix="forecast-fit-") as temporary:
        root = Path(temporary).resolve()
        np.save(root / "x.npy", x, allow_pickle=False)
        np.save(root / "y.npy", y, allow_pickle=False)
        (root / "job.json").write_text(
            json.dumps({"head": head, "policy": recipe.model_dump(mode="json")})
        )
        environment = {
            **os.environ,
            **{
                name: "1"
                for name in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "BLIS_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "LOKY_MAX_CPU_COUNT",
                )
            },
        }
        environment["PYTHONPATH"] = str(Path(retailops_ai.__file__).parent.parent)
        started = time.monotonic()
        peak, cpu = 0, 0.0
        process = subprocess.Popen(  # noqa: S603 -- fixed module, interpreter and private numeric job
            [sys.executable, "-m", "retailops_ai.forecasting.functional_worker", str(root)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # noqa: S603 -- fixed interpreter/module and private numeric job
        monitor = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if time.monotonic() - started > policy.fit_wall_seconds:
                    raise SnapshotError("forecast_model_fit_wall_budget_exceeded")
                try:
                    peak = max(peak, monitor.memory_info().rss)
                    times = monitor.cpu_times()
                    cpu = max(cpu, times.user + times.system)
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as exc:
                    raise SnapshotError("forecast_model_resource_monitor_unavailable") from exc
                if peak > policy.fit_rss_bytes or cpu > policy.fit_cpu_seconds:
                    raise SnapshotError("forecast_model_fit_resource_budget_exceeded")
                time.sleep(policy.monitor_interval_seconds)
            if process.returncode != 0:
                raise SnapshotError("forecast_model_fit_worker_failed")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        receipt = read_json(root, "worker_receipt.json")
        peak, cpu = max(peak, receipt["peak_rss_bytes"]), max(cpu, receipt["cpu_seconds"])
        elapsed = time.monotonic() - started
        if (
            peak > policy.fit_rss_bytes
            or cpu > policy.fit_cpu_seconds
            or elapsed > policy.fit_wall_seconds
        ):
            raise SnapshotError("forecast_model_fit_final_budget_exceeded")
        raw = read_bytes(root, "estimator.json", MAX_MODEL_BYTES)
        decode_model_json(raw)
        estimator = LearnedEstimator.model_validate_json(raw)
        if estimator.family != family or estimator.feature_count != x.shape[1]:
            raise SnapshotError("forecast_worker_output_binding_mismatch")
        return estimator, ResourceReceipt(
            wall_seconds=elapsed, cpu_seconds=cpu, peak_rss_bytes=peak
        )
