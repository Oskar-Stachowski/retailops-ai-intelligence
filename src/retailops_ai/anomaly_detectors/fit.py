"""Train-only imputation and a resource-monitored real Isolation Forest subprocess."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import psutil  # type: ignore[import-untyped]

import retailops_ai
from retailops_ai.anomaly_detectors.codec import fit_fills, transform
from retailops_ai.anomaly_detectors.contract import (
    MAX_MODEL_BYTES,
    FitPolicy,
    Forest,
    Pipeline,
    Resources,
)
from retailops_ai.qualified_anomalies.contract import ModelRow
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    json_sha256,
    read_bytes,
)


def fit_pipeline(
    rows: list[ModelRow], probes: list[ModelRow], policy: FitPolicy
) -> tuple[Pipeline, Resources]:
    policy = FitPolicy.model_validate_json(policy.model_dump_json())
    if not policy.minimum_train_rows <= len(rows) <= policy.max_train_rows:
        raise SnapshotError("anomaly_training_row_budget")
    safe = [ModelRow.model_validate(row.model_dump(mode="python")) for row in rows]
    fills = fit_fills(safe, policy)
    x = np.asarray([transform(row, fills) for row in safe], dtype=np.float64)
    values = np.asarray([transform(row, fills) for row in probes[:64]], dtype=np.float64).reshape(
        -1, x.shape[1]
    )
    if x.nbytes + values.nbytes > policy.max_matrix_bytes:
        raise SnapshotError("anomaly_training_matrix_budget")
    with tempfile.TemporaryDirectory(prefix="anomaly-forest-fit-") as tmp:
        root = Path(tmp).resolve()
        np.save(root / "x.npy", x, allow_pickle=False)
        np.save(root / "probes.npy", values, allow_pickle=False)
        (root / "job.json").write_bytes(canonical_json(policy.model_dump(mode="json")))
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
        process = subprocess.Popen(  # noqa: S603 -- fixed interpreter/module, private numeric inputs
            [sys.executable, "-m", "retailops_ai.anomaly_detectors.worker", str(root)],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        monitor = psutil.Process(process.pid)
        started = time.monotonic()
        peak = 0
        cpu = 0.0
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                try:
                    peak = max(peak, monitor.memory_info().rss)
                    usage = monitor.cpu_times()
                    cpu = max(cpu, usage.user + usage.system)
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as exc:
                    raise SnapshotError("anomaly_fit_resource_monitor_unavailable") from exc
                if (
                    elapsed > policy.fit_wall_seconds
                    or peak > policy.fit_rss_bytes
                    or cpu > policy.fit_cpu_seconds
                ):
                    raise SnapshotError("anomaly_fit_resource_budget")
                time.sleep(0.05)
            _, stderr = process.communicate()
            if process.returncode != 0:
                raise SnapshotError(
                    "anomaly_fit_worker_failed: " + stderr.decode(errors="replace")[-4096:]
                )
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stderr is not None:
                process.stderr.close()
        forest = Forest.model_validate_json(read_bytes(root, "forest.json", MAX_MODEL_BYTES))
        receipt = Resources.model_validate_json(read_bytes(root, "resources.json", 65536))
        receipt = receipt.model_copy(
            update={
                "wall_seconds": time.monotonic() - started,
                "cpu_seconds": max(cpu, receipt.cpu_seconds),
                "peak_rss_bytes": max(peak, receipt.peak_rss_bytes),
            }
        )
        if (
            receipt.wall_seconds > policy.fit_wall_seconds
            or receipt.cpu_seconds > policy.fit_cpu_seconds
            or receipt.peak_rss_bytes > policy.fit_rss_bytes
        ):
            raise SnapshotError("anomaly_fit_final_resource_budget")
    return Pipeline(
        training_rows=len(safe),
        training_rows_sha256=json_sha256([row.model_dump(mode="json") for row in safe]),
        fills=fills,
        forest=forest,
    ), receipt
