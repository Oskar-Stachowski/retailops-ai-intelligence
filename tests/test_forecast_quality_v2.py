"""Small counterexamples explaining v2, without campaign data or fitted models."""

import pytest
from pydantic import ValidationError

from retailops_ai.forecasting.quality_v2 import assess_segment_v2, interval_score
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
    QualityPolicyV2,
)


def forecast(median=0.0, mean=0.0, upper=0.0):
    return FunctionalForecast(
        median=median, mean=mean, interval=CentralInterval(lower=0.0, upper=upper)
    )


def sample(actuals, candidate, baseline):
    return [
        ProtocolObservation(key=str(i), actual=y, candidate=candidate, baseline=baseline)
        for i, y in enumerate(actuals)
    ]


def test_perfect_zero_forecast_passes_and_false_alarms_fail_without_dividing_by_zero():
    zero = forecast()
    good = assess_segment_v2(sample([0] * 100, zero, zero), dimension="global")
    assert good["status"] == "passed" and good["perfect_median_baseline_tied"]
    assert good["candidate"]["median"]["wape"] is None
    assert good["candidate"]["mean"]["normalized_bias"] is None
    assert good["candidate"]["interval"]["width_to_mean_actual"] is None
    bad = assess_segment_v2(sample([0] * 100, forecast(1.0, 1.0, 2.0), zero), dimension="global")
    assert bad["status"] == "failed"
    assert set(bad["failed_reasons"]) == {
        "median_mae_regression_from_perfect_baseline",
        "mean_mse_regression",
        "positive_mean_forecast_on_all_zero_actuals",
        "interval_score_regression",
    }
    assert bad["candidate"]["mean"]["zero_actual_excess_units"] == 100


def test_sparse_sales_large_width_ratio_is_diagnostic_but_useless_wide_bands_still_fail():
    actuals = [0] * 99 + [1]
    baseline = forecast(0.0, 0.01, 2.0)
    rows = sample(actuals, forecast(0.0, 0.01, 1.0), baseline)
    gate = assess_segment_v2(rows, dimension="volume")
    assert gate["status"] == "passed" and gate["legacy_width_ratio_exceeded"]
    assert gate["candidate"]["interval"]["width_to_mean_actual"] == 100
    assert gate["candidate"]["interval"]["mean_score"] == 1
    wide = assess_segment_v2(
        sample(actuals, forecast(0.0, 0.01, 100.0), baseline), dimension="volume"
    )
    assert wide["status"] == "failed" and "interval_score_regression" in wide["failed_reasons"]
    # Coverage alone would pass this zero-width forecast; the missed rare sale is penalized.
    missed = assess_segment_v2(
        sample([0] * 90 + [1] * 10, forecast(0.0, 0.1, 0.0), forecast(0.0, 0.1, 1.0)),
        dimension="volume",
    )
    assert missed["candidate"]["interval"]["coverage"] == 0.9
    assert "interval_score_regression" in missed["failed_reasons"]


def test_bernoulli_example_requires_distinct_median_and_mean_instead_of_conflicting_goals():
    actuals = [0] * 90 + [1] * 10
    pair = forecast(0.0, 0.1, 1.0)
    good = assess_segment_v2(
        sample(actuals, pair, pair), dimension="global", retained_median_baseline=True
    )
    assert good["status"] == "passed"
    assert good["candidate"]["median"]["mae"] == 0.1
    assert good["candidate"]["median"]["normalized_bias"] == -1
    assert good["candidate"]["mean"]["bias_units"] == pytest.approx(0, abs=1e-16)
    assert good["candidate"]["mean"]["mae"] == pytest.approx(0.18)
    # Using the mean as the median increases MAE by 80%, and remains an actual failure.
    wrong = assess_segment_v2(sample(actuals, forecast(0.1, 0.1, 1.0), pair), dimension="volume")
    assert "critical_segment_median_mae_regression" in wrong["failed_reasons"]
    assert wrong["relative_median_mae_change"] == pytest.approx(0.8)
    # Zero is a good median, but an underforecast if presented as the mean.
    biased = assess_segment_v2(sample(actuals, forecast(0.0, 0.0, 1.0), pair), dimension="volume")
    assert "absolute_normalized_mean_bias_exceeded" in biased["failed_reasons"]


def test_missing_mean_does_not_qualify_and_does_not_hide_measurable_mae_regression():
    rows = sample([0] * 100, forecast(1.0, None, 1.0), forecast())
    gate = assess_segment_v2(rows, dimension="global")
    assert gate["status"] == "not_ready" and gate["has_measurable_failures"]
    assert "mean_predictions_incomplete_or_empty" in gate["not_ready_reasons"]
    assert "median_mae_regression_from_perfect_baseline" in gate["failed_reasons"]


def test_mean_bias_cancellation_does_not_hide_mse_regression():
    rows = [
        ProtocolObservation(
            key=str(i),
            actual=1,
            candidate=forecast(1.0, 0.0 if i % 2 else 2.0, 2.0),
            baseline=forecast(1.0, 1.0, 2.0),
        )
        for i in range(100)
    ]
    gate = assess_segment_v2(rows, dimension="volume")
    assert gate["candidate"]["mean"]["normalized_bias"] == 0
    assert gate["failed_reasons"] == ["mean_mse_regression"]


def test_coverage_and_missing_sample_stay_blocking_with_no_partial_success():
    pair = forecast()
    empty = assess_segment_v2([], dimension="volume")
    assert empty["status"] == "not_ready" and "insufficient_sample" in empty["not_ready_reasons"]
    miss = assess_segment_v2(sample([1] * 30, pair, pair), dimension="volume")
    assert "empirical_interval_coverage_below_minimum" in miss["failed_reasons"]
    excluded = [
        ProtocolObservation(
            key=str(i),
            actual=None,
            exclusion_reasons=("unavailable",),
            candidate=pair,
            baseline=pair,
        )
        for i in range(100)
    ]
    assert assess_segment_v2(excluded, dimension="global")["eligible_rows"] == 0
    with pytest.raises(ValidationError, match="eligible_actual_missing"):
        ProtocolObservation(key="missing", actual=None, candidate=pair, baseline=pair)


def test_unchanged_limits_cannot_be_raised_and_baseline_retention_cannot_be_forged():
    with pytest.raises(ValidationError):
        QualityPolicyV2(maximum_segment_mae_regression=0.11)
    with pytest.raises(ValueError, match="retained_baseline"):
        assess_segment_v2(
            sample([0] * 100, forecast(1.0, 1.0, 1.0), forecast()),
            dimension="global",
            retained_median_baseline=True,
        )
    rows = sample([0] * 30, forecast(), forecast())
    with pytest.raises(ValueError, match="duplicate_quality_key"):
        assess_segment_v2(rows + rows, dimension="volume")


def test_central_interval_score_both_tails_and_mean_outside_interval_are_well_defined():
    band = CentralInterval(lower=1.0, upper=3.0)
    assert interval_score(2, band) == 2
    assert interval_score(0, band) == pytest.approx(22)
    assert interval_score(4, band) == pytest.approx(22)
    # P(Y=0)=.99, P(Y=100)=.01: median=q05=q95=0, mean=1.
    assert forecast(0.0, 1.0, 0.0).mean == 1.0
    with pytest.raises(ValidationError):
        forecast(float("nan"), 0.0, 0.0)
    with pytest.raises(ValidationError, match="median_outside"):
        forecast(1.0, 1.0, 0.0)


def test_original_mae_boundaries_remain_strict_global_and_inclusive_segment():
    baseline = forecast(30.0, 10.0, 40.0)
    exactly_five_percent = assess_segment_v2(
        sample([10] * 100, forecast(29.0, 10.0, 40.0), baseline), dimension="global"
    )
    assert "global_median_mae_improvement_below_minimum" in exactly_five_percent["failed_reasons"]
    improved = assess_segment_v2(
        sample([10] * 100, forecast(28.0, 10.0, 40.0), baseline), dimension="global"
    )
    assert improved["status"] == "passed"
    baseline = forecast(20.0, 10.0, 40.0)
    limit = assess_segment_v2(
        sample([10] * 30, forecast(21.0, 10.0, 40.0), baseline), dimension="volume"
    )
    exceeded = assess_segment_v2(
        sample([10] * 30, forecast(21.01, 10.0, 40.0), baseline), dimension="volume"
    )
    assert limit["status"] == "passed"
    assert "critical_segment_median_mae_regression" in exceeded["failed_reasons"]
