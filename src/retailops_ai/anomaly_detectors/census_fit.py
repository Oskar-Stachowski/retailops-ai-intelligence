"""Full numerical training and capacity thresholds with bounded disk matrices.

The caller must supply already authorized, causally eligible rows and measure
its complete feature preparation and journal operation. This numerical layer
does not open Sources, select membership, use truth or grant final access.
"""

import hashlib
import math
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import numpy as np
import psutil  # type: ignore[import-untyped]

import retailops_ai
from retailops_ai.anomaly_detectors.census_contract import (
    CensusFill,
    CensusFitPolicy,
    CensusPipeline,
    CensusThreshold,
)
from retailops_ai.anomaly_detectors.codec import number, transform
from retailops_ai.anomaly_detectors.contract import MAX_MODEL_BYTES, Forest, Resources
from retailops_ai.anomaly_detectors.rows import NumericalRow, validate_row
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    read_bytes,
    regular_file,
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    with regular_file(path.parent, path.name) as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > 268435456 + 1024:
                raise SnapshotError("anomaly_census_matrix_file_budget")
            digest.update(chunk)
    return digest.hexdigest()


def fit_census_pipeline(
    rows: Iterable[NumericalRow],
    row_count: int,
    probes: list[NumericalRow],
    policy: CensusFitPolicy,
    *,
    scratch: Path,
) -> tuple[CensusPipeline, Resources]:
    """Fit all declared rows; native IF subsampling remains its frozen algorithm.

    No input row is discarded before fit. Train medians use every known train
    value; probes never affect imputation or fit. Hashing preserves the native
    JSON-array identity without retaining all Python rows in memory.
    """
    policy = CensusFitPolicy.model_validate_json(policy.model_dump_json())
    width = len(policy.features)
    if (
        type(row_count) is not int
        or not policy.minimum_train_rows <= row_count <= policy.max_train_rows
        or len(probes) > 64
        or (row_count + len(probes)) * width * 2 * 8 > policy.max_matrix_bytes
    ):
        raise SnapshotError("anomaly_census_training_budget")
    required_disk = (row_count + len(probes)) * width * 2 * 8 + MAX_MODEL_BYTES + 128 * 1024
    if shutil.disk_usage(scratch).free < required_disk + policy.minimum_free_disk_bytes:
        raise SnapshotError("anomaly_census_fit_host_reserve")
    started, parent_cpu_start = time.monotonic(), time.process_time()
    child_usage_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu_start = child_usage_start.ru_utime + child_usage_start.ru_stime
    owner = psutil.Process()
    child: subprocess.Popen[bytes] | None = None
    peak, parent_peak, child_cpu = 0, 0, 0.0

    def check() -> None:
        nonlocal peak, parent_peak, child_cpu
        rss = owner.memory_info().rss
        parent_peak = max(parent_peak, rss)
        if child is not None and child.poll() is None:
            try:
                process = psutil.Process(child.pid)
                children = [process, *process.children(recursive=True)]
                usage = 0.0
                for item in children:
                    rss += item.memory_info().rss
                    cpu = item.cpu_times()
                    usage += cpu.user + cpu.system
                child_cpu = max(child_cpu, usage)
            except psutil.NoSuchProcess:
                pass
        peak = max(peak, rss)
        if (
            time.monotonic() - started > policy.fit_wall_seconds
            or time.process_time() - parent_cpu_start + child_cpu > policy.fit_cpu_seconds
            or peak > policy.fit_rss_bytes
        ):
            raise SnapshotError("anomaly_census_fit_resource_budget")
        if (
            psutil.virtual_memory().available < policy.minimum_available_memory_bytes
            or shutil.disk_usage(scratch).free < policy.minimum_free_disk_bytes
        ):
            raise SnapshotError("anomaly_census_fit_host_reserve")

    check()
    with tempfile.TemporaryDirectory(prefix="anomaly-census-fit-", dir=scratch) as directory:
        root = Path(directory)
        matrix = np.lib.format.open_memmap(
            root / "x.npy", mode="w+", dtype=np.float64, shape=(row_count, width * 2)
        )
        digest = hashlib.sha256(b"[")
        observed = 0
        for index, value in enumerate(rows):
            if index >= row_count:
                raise SnapshotError("anomaly_census_extra_training_row")
            safe = validate_row(value.model_dump(mode="python"))
            digest.update((b"," if index else b"") + canonical_json(safe.model_dump(mode="json")))
            for column, name in enumerate(policy.features):
                raw = getattr(safe, name)
                matrix[index, column] = np.nan if raw is None else number(raw)
                matrix[index, column + width] = float(raw is None)
            observed += 1
            if observed % 1024 == 0:
                check()
        if observed != row_count:
            raise SnapshotError("anomaly_census_missing_training_row")
        digest.update(b"]")
        fills = []
        for column, name in enumerate(policy.features):
            values = matrix[:, column]
            known = np.sort(values[~np.isnan(values)])
            count = len(known)
            # Match statistics.median exactly, including odd signed-zero inputs.
            middle = count // 2
            fill = (
                float(known[middle])
                if count % 2
                else (float(known[middle - 1]) + float(known[middle])) / 2
                if count
                else 0.0
            )
            fills.append(
                CensusFill(
                    name=name,
                    value=fill,
                    known_count=count,
                    reason="train_median" if count else "entirely_missing_constant_zero",
                )
            )
            values[np.isnan(values)] = fill
            del known
            check()
        matrix.flush()
        del matrix, values
        probe_matrix = np.asarray(
            [transform(p, tuple(fills)) for p in probes], dtype=np.float64
        ).reshape(-1, width * 2)
        np.save(root / "probes.npy", probe_matrix, allow_pickle=False)
        del probe_matrix
        job = {
            "policy": policy.model_dump(mode="json"),
            "rows": row_count,
            "x.npy": _hash(root / "x.npy"),
            "probes.npy": _hash(root / "probes.npy"),
        }
        (root / "job.json").write_bytes(canonical_json(job))
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
            "PYTHONPATH": str(Path(retailops_ai.__file__).parent.parent),
        }
        with (root / "worker.stderr").open("wb") as stderr:
            child = subprocess.Popen(  # noqa: S603 -- fixed worker and owned numeric files
                [sys.executable, "-m", "retailops_ai.anomaly_detectors.census_worker", str(root)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                env=environment,
                start_new_session=True,
            )
            try:
                while child.poll() is None:
                    check()
                    if (root / "worker.stderr").stat().st_size > 65536:
                        raise SnapshotError("anomaly_census_worker_output_budget")
                    time.sleep(0.05)
                if child.returncode != 0:
                    detail = read_bytes(root, "worker.stderr", 65536).decode(errors="replace")
                    raise SnapshotError("anomaly_census_worker_failed: " + detail[-4096:])
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        forest = Forest.model_validate_json(read_bytes(root, "forest.json", MAX_MODEL_BYTES))
        cost = Resources.model_validate_json(read_bytes(root, "resources.json", 65536))
        if any(_hash(root / name) != job[name] for name in ("x.npy", "probes.npy")):
            raise SnapshotError("anomaly_census_fit_input_changed")
        check()
        child_usage_end = resource.getrusage(resource.RUSAGE_CHILDREN)
        reaped_child_cpu = child_usage_end.ru_utime + child_usage_end.ru_stime - child_cpu_start
        receipt = Resources(
            wall_seconds=time.monotonic() - started,
            cpu_seconds=time.process_time()
            - parent_cpu_start
            + max(child_cpu, cost.cpu_seconds, reaped_child_cpu),
            # The sum of separate peaks is conservative even if a short child
            # peak occurred between samples while the parent retained inputs.
            peak_rss_bytes=max(peak, parent_peak + cost.peak_rss_bytes),
            native_max_score_error=cost.native_max_score_error,
        )
        if (
            receipt.wall_seconds > policy.fit_wall_seconds
            or receipt.cpu_seconds > policy.fit_cpu_seconds
            or receipt.peak_rss_bytes > policy.fit_rss_bytes
        ):
            raise SnapshotError("anomaly_census_fit_final_resource_budget")
        return CensusPipeline(
            training_rows=row_count,
            training_rows_sha256=digest.hexdigest(),
            fills=tuple(fills),
            forest=forest,
        ), receipt


def census_capacity_threshold(
    scores: Iterable[float],
    count: int,
    policy: CensusFitPolicy,
    *,
    scratch: Path,
) -> CensusThreshold | None:
    """Exact native order statistic and tie policy over the complete validation set."""
    policy = CensusFitPolicy.model_validate_json(policy.model_dump_json())
    if type(count) is not int or not 0 <= count <= policy.max_train_rows:
        raise SnapshotError("anomaly_census_validation_budget")
    with tempfile.TemporaryDirectory(prefix="anomaly-census-threshold-", dir=scratch) as tmp:
        values = np.memmap(
            Path(tmp) / "scores.bin", mode="w+", dtype=np.float64, shape=(max(1, count),)
        )
        digest, observed = hashlib.sha256(b"["), 0
        for index, score in enumerate(scores):
            if (
                index >= count
                or type(score) not in (int, float)
                or not math.isfinite(score)
                or score < 0
            ):
                raise SnapshotError("anomaly_census_validation_count_or_score")
            digest.update((b"," if index else b"") + canonical_json(score))
            values[index] = score
            observed += 1
        if observed != count:
            raise SnapshotError("anomaly_census_missing_validation_score")
        digest.update(b"]")
        if count < policy.minimum_validation_rows:
            return None
        values.sort()
        allowed = int(Decimal(str(policy.validation_alert_fraction)) * count)
        high = int(Decimal(str(policy.validation_high_fraction)) * count)
        return CensusThreshold(
            validation_rows=count,
            validation_scores_sha256=digest.hexdigest(),
            allowed_alerts=allowed,
            allowed_high_alerts=high,
            threshold=float(values[count - allowed - 1]),
            high_threshold=float(values[count - high - 1]),
        )
