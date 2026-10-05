"""Fit a later conditional sigmoid and export its entire non-executable state."""

import warnings
from datetime import datetime
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline, ConditionalSigmoid
from retailops_ai.stockout_training.contract import MAX_ROWS, RiskPipeline
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256
from retailops_ai.stockout_training.pipeline import Matrix, probabilities, raw_scores


def offsets(
    rows: list[dict[str, Any]], categories: tuple[str, ...], locations: tuple[str, ...]
) -> Matrix:
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("stockout_conditional_row_limit")
    return np.asarray(
        [
            [
                *(float(r["category_id"] == c) for c in categories),
                *(float(r["stock_location_id"] == s) for s in locations),
                float(r["values"]["history_constrained_days"] > 0),
            ]
            for r in rows
        ],
        dtype=np.float64,
    )


def fit_conditional(
    base: RiskPipeline,
    rows: list[dict[str, Any]],
    outcomes: list[int],
    *,
    C: float,
    fit_known_at: datetime,
) -> ConditionalRiskPipeline:
    if (
        base.sigmoid is not None
        or C not in (0.01, 0.1, 1.0, 10.0)
        or fit_known_at <= base.fit_known_at
        or len(rows) != len(outcomes)
        or set(outcomes) != {0, 1}
        or min(outcomes.count(c) for c in (0, 1)) < 10
        or any(datetime.fromisoformat(r["as_of"]) >= fit_known_at for r in rows)
    ):
        raise ValueError("stockout_conditional_fit_roles_classes_or_cutoff")
    categories = tuple(sorted({r["category_id"] for r in rows}))
    stocks = tuple(sorted({r["stock_location_id"] for r in rows}))
    if max(len(categories), len(stocks)) > 32:
        raise ValueError("stockout_conditional_vocabulary_limit")
    matrix = np.column_stack((raw_scores(base, rows), offsets(rows, categories, stocks)))
    model = LogisticRegression(C=C, l1_ratio=0.0, tol=1e-8, max_iter=1000)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit(matrix, outcomes)
        if model.coef_[0, 0] <= 0:
            raise ValueError("stockout_conditional_nonpositive_raw_score_slope")
        state = ConditionalSigmoid.model_validate(
            dict(
                fit_known_at=fit_known_at,
                calibration_rows=len(rows),
                calibration_keys_sha256=keys_sha256(rows),
                calibration_labels_sha256=labels_sha256(rows, outcomes),
                C=C,
                raw_score_slope=float(model.coef_[0, 0]),
                intercept=float(model.intercept_[0]),
                categories=categories,
                stock_locations=stocks,
                offset_weights=tuple(float(v) for v in model.coef_[0, 1:]),
            )
        )
        portable = probabilities(
            matrix[:, 0] * state.raw_score_slope
            + matrix[:, 1:] @ np.asarray(state.offset_weights)
            + state.intercept
        )
        if not np.allclose(portable, model.predict_proba(matrix)[:, 1], rtol=1e-10, atol=1e-12):
            raise ValueError("stockout_conditional_prediction_parity")
    return ConditionalRiskPipeline(base=base, calibrator=state)


def predict_conditional(pipeline: ConditionalRiskPipeline, rows: list[dict[str, Any]]) -> Matrix:
    pipeline = ConditionalRiskPipeline.model_validate_json(pipeline.model_dump_json())
    state = pipeline.calibrator
    if any(datetime.fromisoformat(r["as_of"]) < state.fit_known_at for r in rows):
        raise ValueError("stockout_conditional_calibrator_not_known_at_origin")
    score = (
        raw_scores(pipeline.base, rows) * state.raw_score_slope
        + offsets(rows, state.categories, state.stock_locations) @ np.asarray(state.offset_weights)
        + state.intercept
    )
    return probabilities(score)
