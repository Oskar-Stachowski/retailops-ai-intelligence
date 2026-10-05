"""Causal multiday context cannot consume future outcomes or invent missing zeros."""

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import point

from retailops_ai.anomaly_detectors.codec import baseline_score
from retailops_ai.anomaly_detectors.protocol import series_key
from retailops_ai.anomaly_detectors.rows import (
    CountLag,
    MultiscaleRow,
    count_residual,
    stock_shortfall,
)
from retailops_ai.anomaly_portfolio.model import multiscale_row, row


def test_new_count_context_is_causal_and_independent_of_future_values():
    day = date(2026, 8, 12)
    current = point(day, 18, "return_completed")
    prior = [point(day - timedelta(days=lag), 25, "return_completed") for lag in range(1, 7)]
    indexed = {(*series_key(p), p.business_date): p for p in prior}
    result = multiscale_row(current, indexed)
    assert result is not None and len(result.recent_counts) == 6
    future = point(day + timedelta(days=1), 100000, "return_completed")
    indexed[(*series_key(future), future.business_date)] = future
    assert multiscale_row(current, indexed) == result
    assert all(r.lag_days >= 1 for r in result.recent_counts)


def test_missing_history_is_distinct_from_known_zero_and_never_fills_with_zero():
    day = date(2026, 8, 12)
    current = point(day, 18, "return_completed")
    missing = multiscale_row(current, {})
    previous = point(day - timedelta(days=1), 0, "return_completed")
    known_zero = multiscale_row(
        current, {(*series_key(previous), previous.business_date): previous}
    )
    assert missing is not None and known_zero is not None
    assert missing.short_count_residual is None and missing.long_count_residual is None
    assert known_zero.short_count_residual is not None
    assert known_zero.long_count_residual is None
    assert len(known_zero.recent_counts) == 1
    assert known_zero.recent_counts[0].observed_units == 0
    assert baseline_score(missing) == baseline_score(row(current))


def test_multiscale_row_rejects_tampered_aggregate_and_oracle_extra_fields():
    current = point(date(2026, 8, 12), 18)
    result = multiscale_row(current, {})
    assert result is not None
    for name, value in (("short_count_residual", 100.0), ("label", True), ("seed", 42)):
        raw = result.model_dump(mode="json")
        raw[name] = value
        with pytest.raises(ValidationError):
            MultiscaleRow.model_validate_json(json.dumps(raw))


def test_inventory_rule_uses_factual_shortfall_and_unknown_stock_has_no_rule_signal():
    current = row(point(date(2026, 8, 12), 0))
    assert current is not None
    constrained = current.model_copy(update={"on_hand": 0})
    adequate = current.model_copy(update={"on_hand": 200})
    unknown = current.model_copy(update={"on_hand": None})
    assert stock_shortfall(constrained) == 8.0
    assert stock_shortfall(adequate) == stock_shortfall(unknown) == 0.0
    assert stock_shortfall(constrained.model_copy(update={"expected_units": 0})) == 0.0
    # Sparse known counts can accumulate evidence while the current daily
    # residual remains below a useful threshold.
    quiet = current.model_copy(update={"observed_units": 0, "expected_units": 0})
    support = tuple(CountLag(lag_days=i, observed_units=1, expected_units=0) for i in range(1, 7))
    assert count_residual(quiet, support, 7) == 6.0
