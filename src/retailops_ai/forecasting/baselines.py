"""Fixed-origin predictors: raw known history only; no access to future outcomes."""

from datetime import timedelta
from math import fsum

from retailops_ai.forecasting.evaluation_contract import (
    BaselineEstimate,
    BaselineName,
    BaselinePolicy,
)
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.source_snapshot.files import SnapshotError


def predict(
    model: BaselineName, row: InputRow, history: HistoryContext, policy: BaselinePolicy
) -> BaselineEstimate:
    # Validate even instances constructed with model_copy/model_construct by an adapter.
    row = InputRow.model_validate_json(row.model_dump_json())
    history = HistoryContext.model_validate_json(history.model_dump_json())
    policy = BaselinePolicy.model_validate_json(policy.model_dump_json())
    if row.history_context_sha256 != history.content_sha256() or (
        row.forecast_origin,
        row.product_id,
        row.selling_location_id,
        row.channel,
    ) != (
        history.forecast_origin,
        history.product_id,
        history.selling_location_id,
        history.channel,
    ):
        raise SnapshotError("baseline_history_key_mismatch")
    known = [point for point in history.points if point.observed_units is not None]
    selected = []
    reason: str
    if model == "last_observed":
        selected = known[-1:]
        reason = "no_known_history"
    elif model == "moving_average":
        start = row.forecast_origin.date() + timedelta(days=1 - policy.moving_average_calendar_days)
        selected = [point for point in known if point.business_date >= start]
        if len(selected) < policy.moving_average_minimum_known_days:
            selected = []
        reason = "insufficient_calendar_window"
    elif model == "seasonal_naive7":
        # h=8..14 repeat the last KNOWN analogous weekday before origin, never h=1..7 actuals.
        selected = [
            point for point in known if point.business_date.weekday() == row.target_date.weekday()
        ][-1:]
        reason = "no_known_same_weekday"
    else:
        raise SnapshotError("baseline_model_not_allowlisted")
    if not selected:
        return BaselineEstimate.model_validate(
            {"predicted_units": None, "history_dates": (), "reason": reason}
        )
    return BaselineEstimate(
        predicted_units=fsum(
            float(point.observed_units) for point in selected if point.observed_units is not None
        )
        / len(selected),
        history_dates=tuple(point.business_date for point in selected),
        reason=None,
    )
