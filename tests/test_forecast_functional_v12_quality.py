"""Parity with immutable quality 2.0 and bounded, namespaced campaign aggregation."""

import math
from dataclasses import replace

import pytest
from test_forecast_quality_v2 import forecast, sample

from retailops_ai.forecasting.functional_v12_quality import (
    DEFAULT_MAX_ROWS,
    CampaignObservation,
    StableSum,
    StreamingCampaignScorer,
    assess_segment_streaming,
)
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import ProtocolObservation
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize(
    "actuals,candidate,baseline,dimension,retained",
    [
        ([10] * 100, forecast(28.0, 10.0, 40.0), forecast(30.0, 10.0, 40.0), "global", False),
        ([10] * 100, forecast(29.0, 10.0, 40.0), forecast(30.0, 10.0, 40.0), "global", False),
        ([10] * 30, forecast(21.0, 10.0, 40.0), forecast(20.0, 10.0, 40.0), "volume", False),
        ([10] * 30, forecast(21.01, 10.0, 40.0), forecast(20.0, 10.0, 40.0), "volume", False),
        ([0] * 100, forecast(), forecast(), "global", False),
        ([0] * 100, forecast(1.0, 1.0, 2.0), forecast(), "global", False),
        ([0] * 99 + [1], forecast(0.0, 0.01, 1.0), forecast(0.0, 0.01, 2.0), "volume", False),
        ([0] * 90 + [1] * 10, forecast(0.0, 0.1, 1.0), forecast(0.0, 0.1, 1.0), "global", True),
        ([0] * 100, forecast(1.0, None, 1.0), forecast(), "global", False),
        ([1] * 20, forecast(), forecast(), "volume", False),
        ([], forecast(), forecast(), "global", False),
    ],
)
def test_streaming_output_exactly_matches_frozen_evaluator(
    actuals, candidate, baseline, dimension, retained
):
    rows = sample(actuals, candidate, baseline)
    args = {"dimension": dimension, "retained_median_baseline": retained}
    assert assess_segment_streaming(iter(rows), **args) == assess_segment_v2(rows, **args)


def test_missing_predictions_and_exclusions_preserve_all_measurable_failures():
    rows = sample([1] * 100, forecast(1.0, 0.0, 2.0), forecast(1.0, 1.0, 2.0))
    rows[0] = rows[0].model_copy(update={"candidate": forecast(2.0, None, 2.0)})
    rows += [
        ProtocolObservation(
            key=f"excluded-{i}",
            actual=None,
            exclusion_reasons=("closed",),
            candidate=forecast(),
            baseline=forecast(),
        )
        for i in range(40)
    ]
    result = assess_segment_streaming(iter(rows), dimension="category")
    assert result == assess_segment_v2(rows, dimension="category")
    assert result["status"] == "not_ready"
    assert "eligibility_coverage_below_minimum" in result["failed_reasons"]
    assert "mean_predictions_incomplete_or_empty" in result["not_ready_reasons"]


def test_stable_expansions_merge_without_chunk_rounding_or_cancellation_loss():
    values = [1e16, 1.0, -1e16, 1e-200, -1e-200, 2**-52, 2**-53, 1e100, -1e100, -1.0]
    sums = []
    for offset in range(3):
        accumulator = StableSum()
        for value in values[offset::3]:
            accumulator.add(value)
        sums.append(accumulator)
    for order in (sums, list(reversed(sums))):
        combined = StableSum()
        for accumulator in order:
            combined.merge(accumulator)
        assert combined.value() == math.fsum(values)
        assert len(combined.partials) <= len(values)


def campaign_row(
    key="same-key", cohort="cohort-a", fold="fold-1", role="validation", volume="zero", actual=0
):
    prediction = forecast(0.0, 0.0, 1.0)
    return CampaignObservation(
        cohort_id=cohort,
        fold=fold,
        role=role,
        horizon=1,
        category="retail",
        channel="store",
        volume=volume,
        observation=ProtocolObservation(
            key=key, actual=actual, candidate=prediction, baseline=prediction
        ),
        retained_median_baseline=True,
        candidate_calibration_rows=50,
        baseline_calibration_rows=50,
    )


def scorer(**kwargs):
    return StreamingCampaignScorer(
        cohorts=("cohort-a", "cohort-b"),
        folds=("fold-1", "fold-2", "fold-3"),
        dimensions={"category": ("retail",), "channel": ("store",), "volume": ()},
        **kwargs,
    )


def test_campaign_all_roles_folds_empty_bins_and_pooled_cohort_namespaces_match_raw_rows():
    records = [
        campaign_row(
            key=str(i),
            cohort=cohort,
            fold=fold,
            role=role,
            volume="low" if cohort == "cohort-b" else "zero",
            actual=int(i % 3 == 0),
        )
        for cohort in ("cohort-a", "cohort-b")
        for fold in ("fold-1", "fold-2", "fold-3")
        for role in ("validation", "development_holdout")
        for i in range(33)
    ]
    with scorer() as stream:
        for row in records:
            stream.add(row)
        report = stream.finalize()
    assert report["processed_rows"] == len(records)
    assert report["input_complete"] and report["status"] == "not_ready"
    assert len(report["segments"]) == 4 * 2 * (1 + 14 + 1 + 1 + 4)
    for segment in report["segments"]:
        selected = []
        for row in records:
            if segment["fold"] != "pooled" and row.fold != segment["fold"]:
                continue
            if row.role != segment["role"]:
                continue
            dimension = segment["dimension"]
            if dimension != "global" and str(getattr(row, dimension)) != segment["value"]:
                continue
            selected.append(
                row.observation.model_copy(
                    update={"key": f"{row.cohort_id}:{row.fold}:{row.role}:{row.observation.key}"}
                )
            )
        expected = assess_segment_v2(
            selected, dimension=segment["dimension"], retained_median_baseline=True
        )
        assert {name: segment[name] for name in expected} == expected
    empty = next(
        s for s in report["segments"] if s["dimension"] == "volume" and s["value"] == "high"
    )
    assert empty["eligible_rows"] == 0 and empty["status"] == "not_ready"


def test_calibration_evidence_does_not_rewrite_predictions_or_hide_real_failure():
    row = replace(campaign_row(actual=1), candidate_calibration_rows=49)
    with scorer() as stream:
        stream.add(row)
        report = stream.finalize()
    segment = next(
        s
        for s in report["segments"]
        if s["fold"] == "fold-1" and s["role"] == "validation" and s["dimension"] == "global"
    )
    original = assess_segment_v2(
        [row.observation], dimension="global", retained_median_baseline=True
    )
    assert segment["candidate"] == original["candidate"]
    assert segment["failed_reasons"] == original["failed_reasons"]
    assert "absolute_normalized_mean_bias_exceeded" in segment["failed_reasons"]
    assert "candidate_interval_calibration_evidence_below_minimum" in segment["not_ready_reasons"]
    assert segment["status"] == "not_ready"


def test_duplicates_and_technical_limits_fail_closed_without_discarding_prior_metrics():
    assert DEFAULT_MAX_ROWS > 128 * 214032 > 1000000
    with scorer(max_rows=2) as stream:
        stream.add(campaign_row(key="1"))
        stream.add(campaign_row(key="2"))
        with pytest.raises(SnapshotError, match="technical_row_budget"):
            stream.add(campaign_row(key="3"))
        report = stream.finalize()
        assert report["processed_rows"] == 2 and not report["input_complete"]
        assert (
            report["status"] == "not_ready"
            and "technical_row_budget" in report["execution_failure"]
        )
        assert any(s["eligible_rows"] == 2 for s in report["segments"])
    with scorer() as stream:
        row = campaign_row()
        stream.add(row)
        # Different cohort may have the same forecast key, but the full identity may not repeat.
        stream.add(replace(row, cohort_id="cohort-b"))
        with pytest.raises(ValueError, match="duplicate_quality_key"):
            stream.add(row)
        assert stream.finalize()["processed_rows"] == 2
    row = sample([0], forecast(1.0, 1.0, 1.0), forecast())[0]
    with pytest.raises(ValueError, match="retained_baseline_predictions_differ"):
        assess_segment_streaming([row], dimension="global", retained_median_baseline=True)
