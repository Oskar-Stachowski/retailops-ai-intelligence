"""Full native stockout base fitting, retaining AI08 equations and portable state.

This numerical component does not open Source, qualify label maturity, reserve
an operation, calibrate or authorize final access. The enclosing campaign
adapter must verify the entire eligible TRAIN membership and measure resources.
No row sampling or fitting on later roles occurs here. AI08 modules are reused
for bounded numeric conversion, transforms and signed portable prediction.
"""

import hashlib
import warnings
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    MAX_STOCKOUT_TRAINING_ROWS,
    CampaignStockoutPreprocessing,
    CampaignStockoutRiskPipeline,
)
from retailops_ai.forecasting.model_trees import export_estimator
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.split import key
from retailops_ai.stockout_training import pipeline as native
from retailops_ai.stockout_training.contract import (
    CATEGORIES,
    MAX_ROWS,
    Family,
    LinearEstimator,
    TrainingPolicy,
    Variant,
    columns,
)

BATCH_ROWS = 512


def _rows(rows: list[dict[str, Any]], *, batch_rows: int) -> None:
    if (
        not 1 <= len(rows) <= MAX_STOCKOUT_TRAINING_ROWS
        or type(batch_rows) is not int
        or not 1 <= batch_rows <= MAX_ROWS
    ):
        raise ValueError("campaign_stockout_rows_or_batch_limit")
    previous = None
    for row in rows:
        current = key(row)
        if any(not isinstance(value, str) or not value for value in current):
            raise ValueError("campaign_stockout_complete_sorted_unique_keys_required")
        origin = datetime.fromisoformat(current[2].replace("Z", "+00:00"))
        if origin.utcoffset() != timedelta(0):
            raise ValueError("campaign_stockout_UTC_origin_required")
        physical_key = (current[0], current[1], origin)
        if previous is not None and physical_key <= previous:
            raise ValueError("campaign_stockout_complete_sorted_unique_keys_required")
        previous = physical_key


def population_digest(rows: list[dict[str, Any]], outcomes: list[int] | None = None) -> str:
    """Exactly the native JSON-array hash without a second full key/label list."""
    if outcomes is not None and len(outcomes) != len(rows):
        raise ValueError("campaign_stockout_outcome_count")
    digest = hashlib.sha256(b"[")
    for position, row in enumerate(rows):
        if position:
            digest.update(b",")
        value = key(row) if outcomes is None else (*key(row), outcomes[position])
        digest.update(canonical_json(value))
    digest.update(b"]")
    return digest.hexdigest()


def fit_preprocessing(
    rows: list[dict[str, Any]], variant: Variant, *, scaled: bool, batch_rows: int = BATCH_ROWS
) -> CampaignStockoutPreprocessing:
    _rows(rows, batch_rows=batch_rows)
    selected = columns(variant)
    raw = np.empty((len(rows), len(selected)), dtype=np.float64)
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        raw[start : start + len(chunk)] = native.numeric(chunk, selected)
    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    values = imputer.fit_transform(raw)
    values = np.column_stack((values, np.isnan(raw).astype(np.float64)))
    scaler = StandardScaler().fit(values) if scaled else None
    categories = {name: tuple(sorted({row[name] for row in rows})) for name in CATEGORIES}
    if any(not values or len(values) > 32 for values in categories.values()):
        raise ValueError("stockout_training_category_vocabulary_limit")
    return CampaignStockoutPreprocessing(
        numeric_columns=selected,
        medians=tuple(float(v) for v in imputer.statistics_),
        means=tuple(float(v) for v in scaler.mean_) if scaler else (0.0,) * values.shape[1],
        scales=tuple(float(v) for v in scaler.scale_) if scaler else (1.0,) * values.shape[1],
        category_values=categories,
        scaled=scaled,
        output_columns=(
            *selected,
            *("missing:" + name for name in selected),
            *(f"{name}={v}" for name in CATEGORIES for v in categories[name]),
        ),
        train_rows=len(rows),
        train_keys_sha256=population_digest(rows),
    )


def transform(
    rows: list[dict[str, Any]],
    state: CampaignStockoutPreprocessing,
    *,
    batch_rows: int = BATCH_ROWS,
) -> native.Matrix:
    _rows(rows, batch_rows=batch_rows)
    state = CampaignStockoutPreprocessing.model_validate_json(state.model_dump_json())
    matrix = np.empty((len(rows), len(state.output_columns)), dtype=np.float64)
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        matrix[start : start + len(chunk)] = native.transform(chunk, state)
    return matrix


def fit_model(
    rows: list[dict[str, Any]],
    outcomes: list[int],
    *,
    family: Family,
    variant: Variant,
    fit_known_at: datetime,
    policy: TrainingPolicy,
    batch_rows: int = BATCH_ROWS,
) -> CampaignStockoutRiskPipeline:
    policy = TrainingPolicy.model_validate_json(policy.model_dump_json())
    _rows(rows, batch_rows=batch_rows)
    if (
        family not in ("logistic_regression", "hist_gradient_boosting")
        or len(outcomes) != len(rows)
        or any(type(value) is not int for value in outcomes)
        or set(outcomes) != {0, 1}
        or fit_known_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("campaign_stockout_binary_population_family_or_fit_clock")
    if any(datetime.fromisoformat(row["as_of"]) >= fit_known_at for row in rows):
        raise ValueError("stockout_training_origin_after_fit_cutoff")
    preprocessing = fit_preprocessing(
        rows, variant, scaled=family == "logistic_regression", batch_rows=batch_rows
    )
    matrix = transform(rows, preprocessing, batch_rows=batch_rows)
    # Same frozen estimators as AI08; no replacement learner or new search grid.
    model = (
        LogisticRegression(
            C=policy.lr_C,
            l1_ratio=0.0,
            max_iter=policy.lr_max_iter,
            tol=policy.lr_tol,
            random_state=policy.random_state,
        )
        if family == "logistic_regression"
        else HistGradientBoostingClassifier(
            max_iter=policy.hgb_max_iter,
            max_leaf_nodes=policy.hgb_max_leaf_nodes,
            min_samples_leaf=policy.hgb_min_samples_leaf,
            learning_rate=policy.hgb_learning_rate,
            l2_regularization=policy.hgb_l2_regularization,
            early_stopping=False,
            categorical_features=None,
            random_state=policy.random_state,
        )
    )
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit(matrix, outcomes)
        estimator = (
            LinearEstimator(
                weights=tuple(float(value) for value in model.coef_[0]),
                intercept=float(model.intercept_[0]),
            )
            if family == "logistic_regression"
            else export_estimator(model, "hist_gradient_boosting", matrix.shape[1])
        )
        for start in range(0, len(matrix), batch_rows):
            batch = matrix[start : start + batch_rows]
            portable = native.probabilities(native.signed_scores(estimator, batch))
            if not np.allclose(portable, model.predict_proba(batch)[:, 1], rtol=1e-10, atol=1e-12):
                raise ValueError("stockout_portable_prediction_parity_failed")
    return CampaignStockoutRiskPipeline(
        family=family,
        variant=variant,
        fit_known_at=fit_known_at,
        preprocessing=preprocessing,
        estimator=estimator,
        train_labels_sha256=population_digest(rows, outcomes),
    )


def predict(
    model: CampaignStockoutRiskPipeline,
    rows: list[dict[str, Any]],
    *,
    batch_rows: int = BATCH_ROWS,
) -> native.Matrix:
    """Uncalibrated probability; the later calibrator is a separate fitted artifact."""
    _rows(rows, batch_rows=batch_rows)
    model = CampaignStockoutRiskPipeline.model_validate_json(model.model_dump_json())
    values = np.empty(len(rows), dtype=np.float64)
    for start in range(0, len(rows), batch_rows):
        chunk = rows[start : start + batch_rows]
        values[start : start + len(chunk)] = native.predict(model, chunk, calibrated=False)
    return values
