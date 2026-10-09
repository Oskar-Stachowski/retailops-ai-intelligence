"""Full declared numerical matrix, real native forest, unchanged portable math."""

import hashlib
import resource
import sys
from pathlib import Path

import numpy as np
import sklearn  # type: ignore[import-untyped]

from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.worker import fit_forest
from retailops_ai.anomaly_detectors.worker_resources import worker_peak_rss_bytes
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    decode_json,
    read_bytes,
    regular_file,
)


def matrix_hash(path: Path, maximum: int) -> str:
    digest = hashlib.sha256()
    size = 0
    with regular_file(path.parent, path.name) as stream:
        while chunk := stream.read(64 * 1024):
            size += len(chunk)
            if size > maximum:
                raise SnapshotError("anomaly_census_matrix_file_budget")
            digest.update(chunk)
    return digest.hexdigest()


def main(root: Path) -> None:
    job = decode_json(read_bytes(root, "job.json", 65536))
    policy = CensusFitPolicy.model_validate_json(canonical_json(job["policy"]))
    if sklearn.__version__ != "1.9.1":
        raise SnapshotError("anomaly_training_library_version")
    for name in ("x.npy", "probes.npy"):
        if (root / name).is_symlink() or matrix_hash(
            root / name, policy.max_matrix_bytes + 1024
        ) != job[name]:
            raise SnapshotError("anomaly_census_matrix_binding")
    x = np.load(root / "x.npy", mmap_mode="r", allow_pickle=False)
    probes = np.load(root / "probes.npy", mmap_mode="r", allow_pickle=False)
    if (
        x.dtype != np.float64
        or probes.dtype != np.float64
        or x.ndim != 2
        or probes.ndim != 2
        or x.shape != (job["rows"], len(policy.features) * 2)
        or not policy.minimum_train_rows <= x.shape[0] <= policy.max_train_rows
        or probes.shape[0] > 64
        or probes.shape[1] != x.shape[1]
        or x.nbytes + probes.nbytes > policy.max_matrix_bytes
        or not np.isfinite(x).all()
        or not np.isfinite(probes).all()
    ):
        raise SnapshotError("anomaly_census_matrix_shape_or_values")
    forest, receipt = fit_forest(x, probes, policy)
    for name in ("x.npy", "probes.npy"):
        if matrix_hash(root / name, policy.max_matrix_bytes + 1024) != job[name]:
            raise SnapshotError("anomaly_census_matrix_changed_during_fit")
    (root / "forest.json").write_bytes(canonical_json(forest.model_dump(mode="json")))
    usage = resource.getrusage(resource.RUSAGE_SELF)
    receipt = receipt.model_copy(
        update={
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_bytes": worker_peak_rss_bytes(),
        }
    )
    (root / "resources.json").write_bytes(canonical_json(receipt.model_dump(mode="json")))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
