"""Train-only preprocessing and signed binary logits, exported as portable JSON."""

import warnings
from datetime import datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from retailops_ai.forecasting.model_contract import LearnedEstimator
from retailops_ai.forecasting.model_trees import export_estimator
from retailops_ai.stockout_training.contract import (
    CATEGORIES,
    MAX_ROWS,
    Family,
    LinearEstimator,
    Preprocessing,
    RiskPipeline,
    Sigmoid,
    TrainingPolicy,
    Variant,
    columns,
)
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256

Matrix = NDArray[np.float64]


def numeric(rows: list[dict[str, Any]], selected: tuple[str, ...]) -> Matrix:
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError("stockout_training_matrix_row_limit")
    result = np.asarray(
        [[np.nan if r["values"][c] is None else r["values"][c] for c in selected] for r in rows],
        dtype=np.float64,
    )
    if np.isinf(result).any() or np.any(np.abs(result[np.isfinite(result)]) > 1e15):
        raise ValueError("stockout_training_numeric_value_invalid")
    return result


def fit_preprocessing(
    rows: list[dict[str, Any]], variant: Variant, *, scaled: bool
) -> Preprocessing:
    selected = columns(variant)
    raw = numeric(rows, selected)
    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    values = imputer.fit_transform(raw)
    # All columns have an indicator, including columns with no missing value in train.
    values = np.column_stack((values, np.isnan(raw).astype(np.float64)))
    scaler = StandardScaler().fit(values) if scaled else None
    categories = {c: tuple(sorted({r[c] for r in rows})) for c in CATEGORIES}
    if any(len(v) > 32 for v in categories.values()):
        raise ValueError("stockout_training_category_vocabulary_limit")
    return Preprocessing(
        numeric_columns=selected,
        medians=tuple(float(v) for v in imputer.statistics_),
        means=tuple(float(v) for v in scaler.mean_) if scaler else (0.0,) * values.shape[1],
        scales=tuple(float(v) for v in scaler.scale_) if scaler else (1.0,) * values.shape[1],
        category_values=categories,
        scaled=scaled,
        output_columns=(
            *selected,
            *("missing:" + c for c in selected),
            *(f"{c}={v}" for c in CATEGORIES for v in categories[c]),
        ),
        train_rows=len(rows),
        train_keys_sha256=keys_sha256(rows),
    )


def transform(rows: list[dict[str, Any]], state: Preprocessing) -> Matrix:
    raw = numeric(rows, state.numeric_columns)
    missing = np.isnan(raw)
    imputed = np.where(missing, np.asarray(state.medians), raw)
    values = (np.column_stack((imputed, missing.astype(np.float64))) - state.means) / state.scales
    categorical = np.asarray(
        [[float(r[c] == v) for c in CATEGORIES for v in state.category_values[c]] for r in rows],
        dtype=np.float64,
    )
    result = np.column_stack((values, categorical))
    if not np.isfinite(result).all():
        raise ValueError("stockout_training_transformed_value_invalid")
    return result


def signed_scores(state: LinearEstimator | LearnedEstimator, matrix: Matrix) -> Matrix:
    """HGB classifier leaves are signed log odds; forecast's zero clipping is inapplicable."""
    if isinstance(state, LinearEstimator):
        result = np.asarray(matrix @ np.asarray(state.weights) + state.intercept, dtype=np.float64)
    else:
        result = np.full(len(matrix), state.intercept, dtype=np.float64)
        for tree in state.trees:
            nodes = tree.nodes
            indices = np.zeros(len(matrix), dtype=np.int64)
            features = np.asarray([n.feature if n.feature is not None else -1 for n in nodes])
            thresholds = np.asarray([n.threshold if n.threshold is not None else 0 for n in nodes])
            left = np.asarray([n.left if n.left is not None else 0 for n in nodes])
            right = np.asarray([n.right if n.right is not None else 0 for n in nodes])
            for _ in range(len(nodes)):
                active = np.flatnonzero(features[indices] >= 0)
                if not len(active):
                    break
                current = indices[active]
                indices[active] = np.where(
                    matrix[active, features[current]] <= thresholds[current],
                    left[current],
                    right[current],
                )
            else:
                raise ValueError("stockout_portable_tree_did_not_terminate")
            result += np.asarray([n.value for n in nodes])[indices]
    if not np.isfinite(result).all():
        raise ValueError("stockout_portable_score_nonfinite")
    return result


def probabilities(scores: Matrix) -> Matrix:
    # logaddexp avoids overflow without clipping or changing the sign of log odds.
    return np.asarray(np.exp(-np.logaddexp(0.0, -scores)), dtype=np.float64)


def fit_model(
    rows: list[dict[str, Any]],
    outcomes: list[int],
    *,
    family: Family,
    variant: Variant,
    fit_known_at: datetime,
    policy: TrainingPolicy,
) -> RiskPipeline:
    if len(outcomes) != len(rows) or set(outcomes) != {0, 1}:
        raise ValueError("stockout_training_requires_binary_classes")
    if any(datetime.fromisoformat(r["as_of"]) >= fit_known_at for r in rows):
        raise ValueError("stockout_training_origin_after_fit_cutoff")
    preprocessing = fit_preprocessing(rows, variant, scaled=family == "logistic_regression")
    matrix = transform(rows, preprocessing)
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
                weights=tuple(float(v) for v in model.coef_[0]),
                intercept=float(model.intercept_[0]),
            )
            if family == "logistic_regression"
            else export_estimator(model, "hist_gradient_boosting", matrix.shape[1])
        )
        portable = probabilities(signed_scores(estimator, matrix))
        if not np.allclose(portable, model.predict_proba(matrix)[:, 1], rtol=1e-10, atol=1e-12):
            raise ValueError("stockout_portable_prediction_parity_failed")
    return RiskPipeline(
        family=family,
        variant=variant,
        fit_known_at=fit_known_at,
        preprocessing=preprocessing,
        estimator=estimator,
        train_labels_sha256=labels_sha256(rows, outcomes),
    )


def raw_scores(pipeline: RiskPipeline, rows: list[dict[str, Any]]) -> Matrix:
    if any(datetime.fromisoformat(r["as_of"]) < pipeline.fit_known_at for r in rows):
        raise ValueError("stockout_model_not_known_at_origin")
    return signed_scores(pipeline.estimator, transform(rows, pipeline.preprocessing))


def fit_sigmoid(
    pipeline: RiskPipeline,
    rows: list[dict[str, Any]],
    outcomes: list[int],
    *,
    fit_known_at: datetime,
    policy: TrainingPolicy,
) -> Sigmoid | None:
    if (
        len(rows) != len(outcomes)
        or not set(outcomes) <= {0, 1}
        or any(datetime.fromisoformat(r["as_of"]) >= fit_known_at for r in rows)
    ):
        raise ValueError("stockout_calibration_cutoff_or_rows_invalid")
    if any(outcomes.count(c) < policy.minimum_calibration_per_class for c in (0, 1)):
        return None
    model = LogisticRegression(C=1.0, l1_ratio=0.0, tol=1e-8, max_iter=1000)
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        model.fit(raw_scores(pipeline, rows).reshape(-1, 1), outcomes)
    return Sigmoid(
        slope=float(model.coef_[0, 0]),
        intercept=float(model.intercept_[0]),
        fit_known_at=fit_known_at,
        calibration_rows=len(rows),
        calibration_keys_sha256=keys_sha256(rows),
        calibration_labels_sha256=labels_sha256(rows, outcomes),
    )


def predict(
    pipeline: RiskPipeline, rows: list[dict[str, Any]], *, calibrated: bool = True
) -> tuple[float, ...]:
    scores = raw_scores(pipeline, rows)
    if calibrated:
        calibrator = pipeline.sigmoid
        if calibrator is None:
            raise ValueError("stockout_calibrator_not_fitted")
        if any(datetime.fromisoformat(r["as_of"]) < calibrator.fit_known_at for r in rows):
            raise ValueError("stockout_calibrator_not_known_at_origin")
        scores = scores * calibrator.slope + calibrator.intercept
    return tuple(float(v) for v in probabilities(scores))
