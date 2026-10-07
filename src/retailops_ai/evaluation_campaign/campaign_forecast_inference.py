"""Common bounded inference inputs without labels, roles or evaluation permissions."""

from dataclasses import dataclass
from typing import Any

import numpy as np

from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastEncoding, Family
from retailops_ai.evaluation_campaign.campaign_forecast_inputs import (
    tensorflow_functionals,
    tensorflow_vector,
    tree_vector,
)
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError

EMPTY = FunctionalForecast(mean=None, median=None, interval=None)


@dataclass(frozen=True)
class InferenceRecord:
    key: bytes
    row: InputRow
    eligible: bool


@dataclass(frozen=True)
class InferenceWindow:
    records: tuple[InferenceRecord, ...]
    history: HistoryContext


def infer_functionals(
    batch: list[InferenceWindow],
    encodings: dict[Family, CampaignForecastEncoding],
    models: dict[str, Any],
) -> dict[bytes, tuple[FunctionalForecast, ...]]:
    """Same six functionals for every key; never inspect an actual or refit state."""
    if not 1 <= len(batch) <= 256 or any(not 1 <= len(w.records) <= 14 for w in batch):
        raise SnapshotError("campaign_inference_window_batch_limit")
    keys = [record.key for window in batch for record in window.records]
    if len(keys) != len(set(keys)):
        raise SnapshotError("campaign_inference_duplicate_key")
    eligible = [(r.key, r.row) for window in batch for r in window.records if r.eligible]
    learned: dict[str, dict[bytes, float]] = {}
    for family in FAMILIES[:2]:
        heads = ("mean",) if family == "rf" else ("mean", "median")
        if eligible:
            matrix = np.asarray(
                [tree_vector(row, encodings[family]) for _, row in eligible], dtype=np.float64
            )
            for head in heads:
                values = np.asarray(models[family + ":" + head].matrix(matrix), dtype=np.float64)
                if (
                    values.shape != (len(eligible),)
                    or not np.isfinite(values).all()
                    or (values < 0).any()
                ):
                    raise SnapshotError("campaign_score_invalid_tree_output")
                learned[family + ":" + head] = {
                    key: float(value) for (key, _), value in zip(eligible, values, strict=True)
                }
    active = [
        (i, window) for i, window in enumerate(batch) if any(r.eligible for r in window.records)
    ]
    tensorflow: dict[int, tuple[FunctionalForecast, ...]] = {}
    if active:
        with np.errstate(over="ignore", invalid="ignore"):
            matrix = np.asarray(
                [
                    tensorflow_vector(
                        (r.row for r in window.records), window.history, encodings["tensorflow"]
                    )
                    for _, window in active
                ],
                dtype=np.float32,
            )
        if not np.isfinite(matrix).all():
            raise SnapshotError("campaign_score_nonfinite_tensorflow_inputs")
        values = np.asarray(models["tensorflow"](matrix, training=False), dtype=np.float32)
        if values.shape != (len(active), 14, 2):
            raise SnapshotError("campaign_score_invalid_tensorflow_output")
        tensorflow = {
            i: tensorflow_functionals(value, encodings["tensorflow"])
            for (i, _), value in zip(active, values, strict=True)
        }
    predictions = {}
    for index, window in enumerate(batch):
        for record in window.records:
            row = record.row
            forecasts = [EMPTY] * 6
            if record.eligible:
                points, bands = empirical_baselines(row, window.history)
                baseline = []
                for name in ("history7", "history28", "weekday28"):
                    band = bands[name]
                    baseline.append(
                        FunctionalForecast(
                            mean=points[name + ":mean"],
                            median=points[name + ":median"],
                            interval=CentralInterval(lower=band[0], upper=band[1])
                            if band is not None
                            else None,
                        )
                    )
                forecasts[:3] = baseline
                forecasts[3] = FunctionalForecast(
                    mean=learned["rf:mean"][record.key], median=None, interval=None
                )
                forecasts[4] = FunctionalForecast(
                    mean=learned["hgb:mean"][record.key],
                    median=learned["hgb:median"][record.key],
                    interval=None,
                )
                forecasts[5] = tensorflow[index][row.horizon_days - 1]
            predictions[record.key] = tuple(forecasts)
    return predictions
