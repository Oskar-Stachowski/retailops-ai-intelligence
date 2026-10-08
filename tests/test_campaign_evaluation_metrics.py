"""Differential numerical gates and retained failures on small explicit controls."""

import numpy as np
import pytest
from test_campaign_evaluation_configuration import configuration, trial_rows

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPrediction,
    CampaignForecastReference,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_functionals import FrozenForecastComposer
from retailops_ai.evaluation_campaign.campaign_evaluation_metrics import EvaluationSegment
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
)
from retailops_ai.source_snapshot.files import SnapshotError


def forecast(mean=0.0, median=0.0, band=None):
    return FunctionalForecast(
        mean=mean,
        median=median,
        interval=band or CentralInterval(lower=0.0, upper=max(2.0, median or 0.0)),
    )


def prediction(index, candidate, baseline, *, excluded=False, role="development_evaluation"):
    return CampaignForecastEvaluationPrediction(
        **trial_rows(index)[0].model_dump(include=set(ForecastKey.model_fields)),
        role=role,
        example_sha256="a" * 64,
        frozen_configuration_sha256="b" * 64,
        eligible=not excluded,
        exclusion_reasons=("closed_target",) if excluded else (),
        candidate=candidate
        if not excluded
        else FunctionalForecast(mean=None, median=None, interval=None),
        reference=CampaignForecastReference(
            mean=baseline.mean,
            median=baseline.median,
            interval=baseline.interval,
            interval_center=baseline.median if baseline.interval is not None else None,
        )
        if not excluded
        else CampaignForecastReference(mean=None, median=None, interval=None, interval_center=None),
    )


@pytest.mark.parametrize("dimension", ["global", "horizon", "category", "channel", "volume"])
@pytest.mark.parametrize("retained", [False, True])
def test_streamed_gates_and_stable_metrics_match_legacy_equations_on_random_paired_keys(
    dimension, retained
):
    randomizer = np.random.default_rng(137)
    segment = EvaluationSegment(dimension, retained_median_baseline=retained)
    legacy = []
    for i in range(150):
        actual = int(randomizer.integers(12))
        median = randomizer.random() * 10
        baseline = forecast(
            randomizer.random() * 12, median, CentralInterval(lower=0.0, upper=14.0)
        )
        candidate = forecast(
            randomizer.random() * 12,
            median if retained else randomizer.random() * 10,
            CentralInterval(lower=0.0, upper=12.0),
        )
        excluded = i % 11 == 0
        row = prediction(i, candidate, baseline, excluded=excluded)
        segment.add(row, None if excluded else actual)
        legacy.append(
            ProtocolObservation(
                key=str(i),
                actual=None if excluded else actual,
                exclusion_reasons=row.exclusion_reasons,
                candidate=row.candidate,
                baseline=FunctionalForecast(
                    mean=row.reference.mean,
                    median=row.reference.median,
                    interval=row.reference.interval,
                ),
            )
        )
    expected = assess_segment_v2(legacy, dimension=dimension, retained_median_baseline=retained)
    result = segment.result()
    for field in (
        "status",
        "failed_reasons",
        "not_ready_reasons",
        "has_measurable_failures",
        "eligible_rows",
        "total_rows",
        "diagnostics",
        "perfect_median_baseline_tied",
        "legacy_width_ratio_exceeded",
    ):
        assert result[field] == expected[field]
    assert result["eligibility_coverage"] == expected["eligibility_coverage"]
    assert result["relative_median_mae_change"] == expected["relative_median_mae_change"]
    for side in ("candidate", "baseline"):
        for head in ("mean", "median", "interval"):
            assert result[side][head] == pytest.approx(expected[side][head], rel=1e-14, abs=1e-14)


def test_reference_median_outside_other_baseline_band_is_scored_as_separate_functionals():
    composer = FrozenForecastComposer(configuration())
    segment = EvaluationSegment("category", retained_median_baseline=False)
    for i in range(30):
        segment.add(composer.compose(trial_rows(i)), 40)
    result = segment.result()
    assert result["baseline"]["median"]["mae"] == 0.0
    assert result["baseline"]["interval"]["coverage"] == 0.0
    assert result["baseline"]["interval"]["mean_score"] == pytest.approx(705.0)
    assert "median_mae_regression_from_perfect_baseline" in result["failed_reasons"]


@pytest.mark.parametrize("missing_mean", [False, True])
def test_all_zero_ratios_undefined_and_missing_predictions_do_not_hide_measurable_failure(
    missing_mean,
):
    segment = EvaluationSegment("global", retained_median_baseline=False)
    baseline = forecast(band=CentralInterval(lower=0.0, upper=0.0))
    candidate = forecast(None if missing_mean else 1.0, 1.0)
    for i in range(100):
        segment.add(prediction(i, candidate, baseline), 0)
    result = segment.result()
    assert result["status"] == ("not_ready" if missing_mean else "failed")
    assert result["candidate"]["median"]["wape"] is None
    assert result["candidate"]["interval"]["width_to_mean_actual"] is None
    assert result["has_measurable_failures"]
    assert "median_mae_regression_from_perfect_baseline" in result["failed_reasons"]
    assert (
        "positive_mean_forecast_on_all_zero_actuals" in result["failed_reasons"]
    ) != missing_mean


def test_final_scope_is_explicit_and_fewer_than_required_rows_blocks_qualification():
    segment = EvaluationSegment("scenario", retained_median_baseline=True)
    zero = forecast(band=CentralInterval(lower=0.0, upper=0.0))
    for i in range(29):
        segment.add(prediction(i, zero, zero, role="final_test"), 0)
    result = segment.result()
    assert result["role"] == "final_test" and result["status"] == "not_ready"
    assert result["not_ready_reasons"] == ["insufficient_sample"]
    assert "not_campaign_qualification" in result["scope"]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "reverse",
        "scope",
        "configuration",
        "missing_actual",
        "excluded_actual",
        "budget",
        "retained",
    ],
)
def test_key_scope_budget_and_retained_baseline_guards_stop_invalid_stream(mutation):
    segment = EvaluationSegment(
        "global", retained_median_baseline=True, max_rows=1 if mutation == "budget" else 100
    )
    zero = forecast()
    first = prediction(1, zero, zero)
    segment.add(first, 0)
    next_row, actual = prediction(2, zero, zero), 0
    if mutation == "duplicate":
        next_row = first
    elif mutation == "reverse":
        next_row = prediction(0, zero, zero)
    elif mutation == "scope":
        next_row = next_row.model_copy(update={"role": "final_test"})
    elif mutation == "configuration":
        next_row = next_row.model_copy(update={"frozen_configuration_sha256": "f" * 64})
    elif mutation == "missing_actual":
        actual = None
    elif mutation == "excluded_actual":
        next_row = prediction(2, zero, zero, excluded=True)
    elif mutation == "retained":
        next_row = prediction(2, forecast(0.0, 1.0), zero)
    with pytest.raises(SnapshotError):
        segment.add(next_row, actual)
    with pytest.raises(SnapshotError, match="failed_metric_stream_has_no_result"):
        segment.result()


def test_finite_outputs_with_overflowing_squared_errors_fail_instead_of_reporting_infinite_mse():
    segment = EvaluationSegment("global", retained_median_baseline=True)
    row = prediction(0, forecast(mean=1e200), forecast())
    with pytest.raises(SnapshotError, match="nonfinite_point_error"):
        segment.add(row, 0)
