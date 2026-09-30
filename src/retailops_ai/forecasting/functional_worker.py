"""Private bounded fit process; only numeric matrices and allowlisted estimator recipes."""

import json
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import (  # type: ignore[import-untyped]
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.model_trees import TreePredictor, export_estimator
from retailops_ai.source_snapshot.files import SnapshotError, read_json, regular_file


def main() -> int:
    root = Path(sys.argv[1])
    job = read_json(root, "job.json")
    recipe = FunctionalPolicy.model_validate_json(json.dumps(job["policy"]))
    policy = recipe.model
    head = job["head"]
    if head not in recipe.heads:
        raise SnapshotError("functional_worker_unknown_head")
    family = "random_forest" if head == "rf_mean" else "hist_gradient_boosting"
    hgb = policy.hgb.model_dump()
    if head in ("hgb_median", "hgb_lower", "hgb_upper"):
        hgb.update(
            loss="quantile",
            quantile={"hgb_median": 0.5, "hgb_lower": 0.05, "hgb_upper": 0.95}[head],
        )
    for name in ("x.npy", "y.npy"):
        with regular_file(root, name) as stream:
            stream.seek(0, 2)
            if stream.tell() > policy.max_matrix_bytes + 1024:
                raise SnapshotError("forecast_worker_matrix_file_limit")
    with regular_file(root, "x.npy") as stream:
        x = np.load(stream, allow_pickle=False)
    with regular_file(root, "y.npy") as stream:
        y = np.load(stream, allow_pickle=False)
    if (
        x.dtype != np.float64
        or y.dtype != np.float64
        or x.ndim != 2
        or y.shape != (x.shape[0],)
        or x.shape[0] < 1
        or x.shape[0] > policy.max_train_rows
        or x.nbytes + y.nbytes > policy.max_matrix_bytes
        or not np.isfinite(x).all()
        or not np.isfinite(y).all()
        or (y < 0).any()
    ):
        raise SnapshotError("forecast_worker_invalid_training_matrix")
    started = time.monotonic()
    with threadpool_limits(limits=1):
        model = (
            RandomForestRegressor(**policy.rf.model_dump(), random_state=policy.random_state)
            if family == "random_forest"
            else HistGradientBoostingRegressor(**hgb, random_state=policy.random_state)
            if family == "hist_gradient_boosting"
            else None
        )
        if model is None:
            raise SnapshotError("forecast_worker_family_not_allowlisted")
        model.fit(x, y)
        stored = export_estimator(model, family, x.shape[1])
        native = np.maximum(model.predict(x), 0.0)
        portable = TreePredictor(stored).matrix(x)
        if not np.allclose(native, portable, rtol=1e-12, atol=1e-12):
            raise SnapshotError("forecast_portable_native_prediction_mismatch")
    raw = stored.model_dump_json().encode()
    if len(raw) > 128 * 1024**2:
        raise SnapshotError("forecast_worker_model_file_limit")
    (root / "estimator.json").write_bytes(raw)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    (root / "worker_receipt.json").write_text(
        json.dumps(
            {
                "peak_rss_bytes": int(
                    usage.ru_maxrss * (1 if platform.system() == "Darwin" else 1024)
                ),
                "cpu_seconds": usage.ru_utime + usage.ru_stime,
                "fit_seconds": time.monotonic() - started,
                "portable_max_absolute_error": float(np.max(np.abs(native - portable))),
                "training_rows": x.shape[0],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
