"""Frozen portfolio inference and calibration, independent of development role adapters.

These primitives grant no data access and perform no model/preprocessing fitting.
The runner must bind an immutable comparison and reserve calibration/final input
before calling them. Inference receives only features and eligibility, never labels.
"""

import math
from collections import defaultdict
from datetime import timedelta
from typing import Any, cast

import numpy as np

from retailops_ai.evaluation_campaign.campaign_calibration_contract import residual_rank
from retailops_ai.evaluation_campaign.campaign_forecast_inference import InferenceWindow
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import _horizons
from retailops_ai.evaluation_campaign.comparison import forecast_key_bytes
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.forecasting.functional_contract import FunctionalPipeline
from retailops_ai.forecasting.functional_preprocessing import transform_values
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.model_trees import TreePredictor
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import Normalization

EMPTY = FunctionalForecast(mean=None, median=None, interval=None)


def tensorflow_vector(window: InferenceWindow, state: Normalization) -> tuple[float, ...]:
    """Label-free equivalent of the fitted legacy TF transform, with full key checks."""
    rows = _horizons((record.row for record in window.records), window.history)
    by_day = {point.business_date: point for point in window.history.points}
    output: list[float] = []
    for offset in range(27, -1, -1):
        point = by_day.get(window.history.forecast_origin.date() - timedelta(days=offset))
        units = (
            state.history_fill
            if point is None or point.observed_units is None
            else float(point.observed_units)
        )
        output.extend(
            (
                (units - state.history_scale.center) / state.history_scale.spread,
                float(point is not None and point.observed_units is None),
                float(point is None),
            )
        )
    for horizon in range(1, 15):
        row = rows.get(horizon)
        if row is None:
            output.extend([0.0] * len(state.scales) + [0.0])
        else:
            values = transform_values(
                {value.name: value.value for value in row.values}, state.encoding
            )
            output.extend(
                (v - s.center) / s.spread for v, s in zip(values, state.scales, strict=True)
            )
            output.append(1.0)
    if len(output) != state.input_width or not all(math.isfinite(v) for v in output):
        raise SnapshotError("portfolio_invalid_tensorflow_vector")
    return tuple(output)


def predict(
    batch: list[InferenceWindow],
    *,
    tensorflow: Any,
    state: Normalization,
    trees: dict[str, tuple[FunctionalPipeline, TreePredictor]],
) -> dict[bytes, dict[str, FunctionalForecast]]:
    """Apply original fixed models on identical keys in bounded batches of 64 windows."""
    if not 1 <= len(batch) <= 64:
        raise SnapshotError("portfolio_inference_batch_budget")
    records = [record for window in batch for record in window.records]
    if (
        len({r.key for r in records}) != len(records)
        or any(r.key != forecast_key_bytes(r.row) for r in records)
        or set(trees) != {"rf_mean", "hgb_mean", "hgb_median"}
    ):
        raise SnapshotError("portfolio_duplicate_inference_key")
    with np.errstate(over="ignore", invalid="ignore"):
        x = np.asarray([tensorflow_vector(w, state) for w in batch], dtype=np.float32)
    if not np.isfinite(x).all():
        raise SnapshotError("portfolio_nonfinite_float32_matrix")
    values = np.asarray(tensorflow(x, training=False), dtype=np.float32) * state.target_scale
    if values.shape != (len(batch), 14, 2) or not np.isfinite(values).all() or (values < 0).any():
        raise SnapshotError("portfolio_invalid_tensorflow_output")
    learned = {}
    for head, (pipeline, predictor) in trees.items():
        matrix = np.asarray(
            [
                (
                    *transform_values(
                        {v.name: v.value for v in r.row.values}, pipeline.preprocessing
                    ),
                    float(r.row.horizon_days),
                )
                for r in records
            ],
            dtype=np.float64,
        )
        points = predictor.matrix(matrix)
        if points.shape != (len(records),) or not np.isfinite(points).all() or (points < 0).any():
            raise SnapshotError("portfolio_invalid_tree_output")
        learned[head] = dict(zip((r.key for r in records), map(float, points), strict=True))
    output = {}
    for index, window in enumerate(batch):
        for record in window.records:
            if not record.eligible:
                output[record.key] = dict.fromkeys(MODELS, EMPTY)
                continue
            baseline_points, _ = empirical_baselines(record.row, window.history)
            forecasts = {
                name: FunctionalForecast(
                    mean=baseline_points[name + ":mean"],
                    median=baseline_points[name + ":median"],
                    interval=None,
                )
                for name in MODELS[:3]
            }
            forecasts["rf_mean"] = FunctionalForecast(
                mean=learned["rf_mean"][record.key], median=None, interval=None
            )
            forecasts["hgb"] = FunctionalForecast(
                mean=learned["hgb_mean"][record.key],
                median=learned["hgb_median"][record.key],
                interval=None,
            )
            pair = values[index, record.row.horizon_days - 1]
            forecasts["tensorflow"] = FunctionalForecast(
                mean=float(pair[0]), median=float(pair[1]), interval=None
            )
            output[record.key] = forecasts
    return output


def fit_intervals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One 90% absolute-residual calibration per model/horizon on a separate role."""
    if not rows or any(row["role"] != "calibration" for row in rows):
        raise SnapshotError("portfolio_calibration_role_required")
    if len({row["key"] for row in rows}) != len(rows):
        raise SnapshotError("portfolio_duplicate_calibration_key")
    output: dict[str, Any] = {}
    for name in MODELS:
        horizons = {}
        for horizon in range(1, 15):
            group = [r for r in rows if r["horizon_days"] == horizon]
            eligible = [r for r in group if not r["exclusion_reasons"]]
            errors = []
            for row in eligible:
                forecast = FunctionalForecast.model_validate(row["predictions"][name])
                center = forecast.mean if name == "rf_mean" else forecast.median
                if center is None or row["actual"] is None:
                    raise SnapshotError("portfolio_calibration_missing_point_or_actual")
                errors.append(abs(center - row["actual"]))
            rank = residual_rank(len(errors))
            reasons = []
            if len(errors) < 100:
                reasons.append("insufficient_calibration_rows")
            if not group or len(errors) / len(group) < 0.8:
                reasons.append("calibration_eligibility_below_minimum")
            if rank > len(errors):
                reasons.append("finite_sample_quantile_unavailable")
            horizons[str(horizon)] = {
                "rows": len(group),
                "eligible_rows": len(errors),
                "rank": rank,
                "radius": sorted(errors)[rank - 1] if not reasons else None,
                "reasons": reasons,
            }
        output[name] = {"center": "mean" if name == "rf_mean" else "median", "horizons": horizons}
    return {
        "models": output,
        "status": "fitted"
        if all(not h["reasons"] for m in output.values() for h in m["horizons"].values())
        else "not_ready",
        "nominal_coverage": 0.9,
        "temporal_coverage_guarantee_claimed": False,
        "model_or_preprocessing_refitted": False,
    }


def add_intervals(
    row: dict[str, Any], calibration: dict[str, Any]
) -> dict[str, FunctionalForecast]:
    if calibration["status"] != "fitted":
        raise SnapshotError("portfolio_calibration_incomplete")
    output = {}
    for name in MODELS:
        forecast = FunctionalForecast.model_validate(row["predictions"][name])
        band = None
        if not row["exclusion_reasons"]:
            model = calibration["models"][name]
            center = getattr(forecast, model["center"])
            radius = model["horizons"][str(row["horizon_days"])]["radius"]
            if center is None or radius is None:
                raise SnapshotError("portfolio_calibration_point_or_radius_missing")
            band = CentralInterval(lower=max(0.0, center - radius), upper=center + radius)
        output[name] = forecast.model_copy(update={"interval": band})
    return output


def metrics(
    rows: list[dict[str, Any]], calibration: dict[str, Any], groups: dict[str, list[str]]
) -> dict[str, Any]:
    if not rows or any(row["role"] != "final_test" for row in rows):
        raise SnapshotError("portfolio_final_role_required")
    if len({row["key"] for row in rows}) != len(rows):
        raise SnapshotError("portfolio_duplicate_final_key")
    output = {}
    for name in MODELS:
        observations = []
        segments: dict[str, dict[str, list[ProtocolObservation]]] = {
            d: defaultdict(list) for d in groups
        }
        for row in rows:
            predictions = add_intervals(row, calibration)
            observation = ProtocolObservation(
                key=row["key"],
                actual=row["actual"],
                exclusion_reasons=tuple(row["exclusion_reasons"]),
                candidate=predictions[name],
                baseline=predictions["history28"],
            )
            observations.append(observation)
            for dimension in groups:
                segments[dimension][row["groups"][dimension]].append(observation)
        assessed = {
            "global": assess_segment_v2(
                observations, dimension="global", retained_median_baseline=name == "history28"
            ),
            "segments": {
                dimension: {
                    value: assess_segment_v2(
                        segments[dimension][value],
                        dimension=cast(Any, dimension),
                        retained_median_baseline=name == "history28",
                    )
                    for value in sorted(set(expected) | set(segments[dimension]))
                }
                for dimension, expected in groups.items()
            },
        }
        for segment in [
            assessed["global"],
            *(v for d in assessed["segments"].values() for v in d.values()),
        ]:
            segment["point_deltas"] = {
                f"{functional}_{metric}": segment["candidate"][functional][metric]
                - segment["baseline"][functional][metric]
                if segment["candidate"][functional][metric] is not None
                and segment["baseline"][functional][metric] is not None
                else None
                for functional in ("mean", "median")
                for metric in ("mae", "mse", "bias_units")
            }
        output[name] = assessed
    return {
        "models": output,
        "rows_per_model": len(rows),
        "scope": "frozen_portfolio_final_per_seed",
        "promotion_allowed": False,
    }


def segment_values(row: Any) -> dict[str, str]:
    values = {value.name: value.value for value in row.values}
    return {
        "horizon": str(row.horizon_days),
        "channel": row.channel,
        "category": str(values["category_id"]),
        "volume": volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
    }
