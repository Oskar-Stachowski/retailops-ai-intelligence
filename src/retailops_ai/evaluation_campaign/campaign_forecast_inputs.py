"""Shared train/inference vectors use frozen encoding and as-of covariates, never labels."""

import math
from collections.abc import Iterable
from datetime import timedelta
from itertools import islice

from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastEncoding
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def transform(row: InputRow, encoding: CampaignForecastEncoding) -> tuple[float, ...]:
    values = {v.name: v.value for v in row.values}
    output: list[float] = []
    for item in encoding.numeric:
        value = values[item.name]
        number = item.fill if value is None else float(value)
        output.extend(((number - item.center) / item.spread, float(value is None)))
    for category in encoding.categorical:
        value = values[category.name]
        output.extend(
            (float(value is None), float(value is not None and value not in category.categories))
        )
        output.extend(float(value == known) for known in category.categories)
    if not all(math.isfinite(v) for v in output):
        raise SnapshotError("campaign_fit_nonfinite_transformed_value")
    return tuple(output)


def tree_vector(row: InputRow, encoding: CampaignForecastEncoding) -> tuple[float, ...]:
    return (*transform(row, encoding), row.horizon_days / 14)


def _horizons(rows: Iterable[InputRow], history: HistoryContext) -> dict[int, InputRow]:
    context_hash = history.content_sha256()
    horizons: dict[int, InputRow] = {}
    for count, row in enumerate(rows, 1):
        if count > 14 or row.horizon_days in horizons:
            raise SnapshotError("campaign_forecast_duplicate_or_excess_horizon")
        if (row.product_id, row.selling_location_id, row.channel, row.forecast_origin) != (
            history.product_id,
            history.selling_location_id,
            history.channel,
            history.forecast_origin,
        ) or row.history_context_sha256 != context_hash:
            raise SnapshotError("campaign_forecast_window_history_binding")
        horizons[row.horizon_days] = row
    if not horizons:
        raise SnapshotError("campaign_forecast_empty_window")
    return horizons


def tensorflow_vector(
    rows: Iterable[InputRow], history: HistoryContext, encoding: CampaignForecastEncoding
) -> tuple[float, ...]:
    horizons = _horizons(rows, history)
    by_day = {p.business_date: p for p in history.points}
    vector: list[float] = []
    for offset in range(27, -1, -1):
        point = by_day.get(history.forecast_origin.date() - timedelta(days=offset))
        units = (
            encoding.history_fill
            if point is None or point.observed_units is None
            else float(point.observed_units)
        )
        vector.extend(
            (
                (units - encoding.history_center) / encoding.history_spread,
                float(point is None or point.observed_units is None),
                float(point is None),
            )
        )
    for horizon in range(1, 15):
        row = horizons.get(horizon)
        vector.extend(
            tree_vector(row, encoding)
            if row is not None
            else (0.0,) * (len(encoding.output_columns) + 1)
        )
    if len(vector) != encoding.tensorflow_width or not all(math.isfinite(v) for v in vector):
        raise SnapshotError("campaign_forecast_tensorflow_vector_dimension_or_finite")
    return tuple(vector)


def tensorflow_functionals(
    values: Iterable[Iterable[float]], encoding: CampaignForecastEncoding
) -> tuple[FunctionalForecast, ...]:
    """Convert all 14 raw heads to original units without training or outcome corrections."""
    result: list[FunctionalForecast] = []
    for horizon, heads in enumerate(values, 1):
        if horizon > 14:
            raise SnapshotError("campaign_forecast_tensorflow_output_shape")
        pair = tuple(islice(heads, 3))
        if len(pair) != 2 or any(not math.isfinite(v) or v < 0 for v in pair):
            raise SnapshotError("campaign_forecast_tensorflow_output_shape_or_value")
        result.append(
            FunctionalForecast(
                mean=float(pair[0]) * encoding.target_scale,
                median=float(pair[1]) * encoding.target_scale,
                interval=None,
            )
        )
    if len(result) != 14:
        raise SnapshotError("campaign_forecast_tensorflow_output_shape")
    return tuple(result)
