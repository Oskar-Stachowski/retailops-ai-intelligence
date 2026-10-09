"""Fixed bounded training worker; inference artifacts contain numeric data only."""

import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import sklearn  # type: ignore[import-untyped]
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]

from retailops_ai.anomaly_detectors.codec import score_matrix
from retailops_ai.anomaly_detectors.contract import FitPolicy, Forest, Node, Resources, Tree
from retailops_ai.anomaly_detectors.worker_resources import worker_peak_rss_bytes
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, read_bytes


def main(root: Path) -> None:
    if sklearn.__version__ != "1.9.1":
        raise SnapshotError("anomaly_training_library_version")
    policy = FitPolicy.model_validate_json(read_bytes(root, "job.json", 65536))
    x = np.load(root / "x.npy", allow_pickle=False)
    probes = np.load(root / "probes.npy", allow_pickle=False)
    if (
        x.ndim != 2
        or x.shape[1] != len(policy.features) * 2
        or not policy.minimum_train_rows <= x.shape[0] <= policy.max_train_rows
        or x.nbytes + probes.nbytes > policy.max_matrix_bytes
        or probes.ndim != 2
        or probes.shape[1] != x.shape[1]
        or not np.isfinite(x).all()
        or not np.isfinite(probes).all()
    ):
        raise SnapshotError("anomaly_training_matrix_budget_or_shape")
    forest, receipt = fit_forest(x, probes, policy)
    (root / "forest.json").write_bytes(canonical_json(forest.model_dump(mode="json")))
    (root / "resources.json").write_text(json.dumps(receipt.model_dump(mode="json")))


def fit_forest(x: np.ndarray, probes: np.ndarray, policy: FitPolicy) -> tuple[Forest, Resources]:
    """Shared native fit/export math; callers enforce their versioned input budgets."""
    started = time.monotonic()
    fitted = IsolationForest(
        n_estimators=policy.n_estimators,
        max_samples=min(policy.max_samples, len(x)),
        max_features=policy.max_features,
        contamination=policy.contamination,
        random_state=policy.model_seed,
        n_jobs=1,
        bootstrap=False,
    ).fit(x)
    trees = []
    for estimator, selected in zip(fitted.estimators_, fitted.estimators_features_, strict=True):
        native = estimator.tree_
        trees.append(
            Tree(
                nodes=tuple(
                    Node(
                        sample_count=int(native.n_node_samples[index]),
                        feature=int(selected[native.feature[index]])
                        if native.children_left[index] >= 0
                        else None,
                        threshold=float(native.threshold[index])
                        if native.children_left[index] >= 0
                        else None,
                        left=int(native.children_left[index])
                        if native.children_left[index] >= 0
                        else None,
                        right=int(native.children_right[index])
                        if native.children_left[index] >= 0
                        else None,
                    )
                    for index in range(native.node_count)
                )
            )
        )
    forest = Forest(
        feature_count=x.shape[1],
        max_samples=int(fitted.max_samples_),
        native_offset=float(fitted.offset_),
        trees=tuple(trees),
    )
    # Probe actual held-out inputs and adjacent float32 values around root splits.
    boundary_rows = []
    for tree in forest.trees:
        node = tree.nodes[0]
        if node.feature is None or node.threshold is None:
            continue
        center = np.float32(node.threshold)
        for value in (
            np.nextafter(center, np.float32(-np.inf)),
            center,
            np.nextafter(center, np.float32(np.inf)),
        ):
            boundary = x[0].copy()
            boundary[node.feature] = float(value)
            boundary_rows.append(boundary)
    examples = np.concatenate(
        [x[:64], probes[:64], np.asarray(boundary_rows).reshape(-1, x.shape[1])]
    )
    restored = np.asarray(score_matrix(forest, [tuple(row) for row in examples]))
    error = float(np.max(np.abs(restored + fitted.score_samples(examples))))
    if error > 1e-12:
        raise SnapshotError("anomaly_native_portable_score_mismatch")
    rss = worker_peak_rss_bytes()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    receipt = Resources(
        wall_seconds=time.monotonic() - started,
        cpu_seconds=usage.ru_utime + usage.ru_stime,
        peak_rss_bytes=rss,
        native_max_score_error=error,
    )
    return forest, receipt


if __name__ == "__main__":
    main(Path(sys.argv[1]))
