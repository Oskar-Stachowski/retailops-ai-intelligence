"""Later full-population conditional calibration with unchanged native equations.

The campaign controller must bind these rows to the complete mature calibration
role. This component neither selects a C after final outcomes nor opens a source.
"""

import warnings
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.evaluation_campaign import campaign_stockout_training as training
from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    CampaignStockoutConditionalRiskPipeline,
    CampaignStockoutConditionalSigmoid,
    CampaignStockoutRiskPipeline,
)
from retailops_ai.stockout_selection.pipeline import offsets
from retailops_ai.stockout_training.pipeline import Matrix, probabilities, raw_scores


def fit_conditional(
    base: CampaignStockoutRiskPipeline,
    rows: list[dict[str, Any]],
    outcomes: list[int],
    *,
    C: float,
    fit_known_at: datetime,
    batch_rows: int = training.BATCH_ROWS,
) -> CampaignStockoutConditionalRiskPipeline:
    training._rows(rows, batch_rows=batch_rows)
    base = CampaignStockoutRiskPipeline.model_validate_json(base.model_dump_json())
    if (
        type(C) is not float
        or C not in (0.01, 0.1, 1.0, 10.0)
        or fit_known_at.utcoffset() != timedelta(0)
        or fit_known_at <= base.fit_known_at
        or len(rows) != len(outcomes)
        or any(type(value) is not int for value in outcomes)
        or set(outcomes) != {0, 1}
        or min(outcomes.count(c) for c in (0, 1)) < 10
        or any(datetime.fromisoformat(row["as_of"]) >= fit_known_at for row in rows)
    ):
        raise ValueError("stockout_conditional_fit_roles_classes_or_cutoff")
    categories = tuple(sorted({row["category_id"] for row in rows}))
    stocks = tuple(sorted({row["stock_location_id"] for row in rows}))
    if max(len(categories), len(stocks)) > 32:
        raise ValueError("stockout_conditional_vocabulary_limit")
    matrix = np.empty((len(rows), 2 + len(categories) + len(stocks)), dtype=np.float64)
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        matrix[start : start + len(chunk), 0] = raw_scores(base, chunk)
        matrix[start : start + len(chunk), 1:] = offsets(chunk, categories, stocks)
    model = LogisticRegression(C=C, l1_ratio=0.0, tol=1e-8, max_iter=1000)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit(matrix, outcomes)
        if model.coef_[0, 0] <= 0:
            raise ValueError("stockout_conditional_nonpositive_raw_score_slope")
        state = CampaignStockoutConditionalSigmoid(
            fit_known_at=fit_known_at,
            calibration_rows=len(rows),
            calibration_keys_sha256=training.population_digest(rows),
            calibration_labels_sha256=training.population_digest(rows, outcomes),
            C=C,
            raw_score_slope=float(model.coef_[0, 0]),
            intercept=float(model.intercept_[0]),
            categories=categories,
            stock_locations=stocks,
            offset_weights=tuple(float(value) for value in model.coef_[0, 1:]),
        )
        for start in range(0, len(rows), batch_rows):
            batch = matrix[start : start + batch_rows]
            portable = probabilities(
                batch[:, 0] * state.raw_score_slope
                + batch[:, 1:] @ np.asarray(state.offset_weights)
                + state.intercept
            )
            if not np.allclose(portable, model.predict_proba(batch)[:, 1], rtol=1e-10, atol=1e-12):
                raise ValueError("stockout_conditional_prediction_parity")
    return CampaignStockoutConditionalRiskPipeline(base=base, calibrator=state)


def predict_conditional(
    pipeline: CampaignStockoutConditionalRiskPipeline,
    rows: list[dict[str, Any]],
    *,
    batch_rows: int = training.BATCH_ROWS,
) -> Matrix:
    training._rows(rows, batch_rows=batch_rows)
    pipeline = CampaignStockoutConditionalRiskPipeline.model_validate_json(
        pipeline.model_dump_json()
    )
    state = pipeline.calibrator
    if any(datetime.fromisoformat(row["as_of"]) < state.fit_known_at for row in rows):
        raise ValueError("stockout_conditional_calibrator_not_known_at_origin")
    result = np.empty(len(rows), dtype=np.float64)
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        score = (
            raw_scores(pipeline.base, chunk) * state.raw_score_slope
            + offsets(chunk, state.categories, state.stock_locations)
            @ np.asarray(state.offset_weights)
            + state.intercept
        )
        result[start : start + len(chunk)] = probabilities(score)
    return result
