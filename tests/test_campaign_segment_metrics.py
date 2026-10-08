"""Direct six-model equations, exclusions and full source-context census binding."""

import pytest
from test_campaign_segments import census, context, inputs

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastQualityPolicy,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_metrics import EvaluationSegment
from retailops_ai.evaluation_campaign.campaign_segment_metrics import RawTrialCriticalSegments
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import CampaignUncertaintyScope
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def row(ctx, *, trial="trial1", value=2.0):
    return CampaignForecastTrialPrediction(
        **ctx.model_dump(include=set(ForecastKey.model_fields)),
        role=ctx.role,
        trial_tune_score_operation_id=trial,
        example_sha256=ctx.example_sha256,
        eligible=ctx.eligible,
        exclusion_reasons=ctx.exclusion_reasons,
        values=tuple(
            FunctionalForecast(
                mean=value + n if ctx.eligible else None,
                median=value + n if ctx.eligible and n != 3 else None,
                interval=CentralInterval(lower=0.0, upper=10.0) if ctx.eligible and n < 3 else None,
            )
            for n in range(6)
        ),
    )


def contexts():
    result = []
    for product, volume, excluded in (("p1", 0.0, False), ("p2", 8.0, False), ("p3", 25.0, True)):
        f, h = inputs(product=product, volume=volume)
        result.append(context(f, h, excluded=excluded))
    return sorted(result, key=membership_key)


def collector(rows):
    return RawTrialCriticalSegments(
        census(rows),
        trial_tune_score_operation_id="trial1",
        quality_policy=CampaignForecastQualityPolicy(),
    )


def test_all_models_and_empty_or_excluded_groups_are_retained_with_direct_paired_metrics():
    rows = contexts()
    stream = collector(rows)
    for ctx, actual in zip(rows, [0, 4, None], strict=True):
        stream.add(row(ctx), actual, ctx)
    report = stream.finish()
    groups = {(s["dimension"], s["value"]): s for s in report["segments"]}
    assert len(groups) == len(census(rows).populations) == 57
    assert report["rows"] == 3 and report["eligible_rows"] == 2
    for index, name in enumerate(MODELS):
        metrics = groups["global", "all"]["models"][name]
        errors = [2.0 + index, index - 2.0]
        assert metrics["mean"]["mse"] == sum(e * e for e in errors) / 2
        if index == 3:
            assert metrics["median"]["mae"] is None
            assert metrics["median"]["wape"] is None
        else:
            assert metrics["median"]["mae"] == sum(abs(e) for e in errors) / 2
            assert metrics["median"]["wape"] == sum(abs(e) for e in errors) / 4
        assert metrics["actual_sum"] == 4
    assert groups["volume", "high"]["rows"] == 1
    assert groups["volume", "high"]["eligible_rows"] == 0
    assert groups["category", "c2"]["rows"] == 0
    assert groups["category", "c2"]["models"][MODELS[-1]]["median"]["wape"] is None
    assert not report["quality_qualified"] and not report["stage_ready"]
    assert not report["raw_learned_intervals_calibrated"]


@pytest.mark.parametrize(
    "mutation", ["trial", "example", "key", "eligibility", "actual", "duplicate"]
)
def test_row_binding_and_failure_latch_block_partial_or_different_populations(mutation):
    rows = contexts()
    stream = collector(rows)
    first = row(rows[0])
    actual = 2
    if mutation == "trial":
        first = first.model_copy(update={"trial_tune_score_operation_id": "other"})
    elif mutation == "example":
        first = first.model_copy(update={"example_sha256": "f" * 64})
    elif mutation == "key":
        first = first.model_copy(update={"product_id": "other"})
    elif mutation == "eligibility":
        first = row(rows[2])
    elif mutation == "actual":
        actual = None
    elif mutation == "duplicate":
        stream.add(first, actual, rows[0])
    with pytest.raises(SnapshotError):
        stream.add(first, actual, rows[0])
    with pytest.raises(SnapshotError, match="stream_unavailable"):
        stream.finish()


def test_planned_anomaly_dimension_is_supported_by_metrics_and_preregistered_uncertainty_scope():
    population = census(contexts())
    scope = CampaignUncertaintyScope(
        data_seed=population.scope.data_seed,
        role=population.scope.role,
        dataset_id=population.scope.dataset_id,
        source_recipe_sha256=population.scope.source_recipe_sha256,
        frozen_configuration_sha256="f" * 64,
        scenario="all",
        dimension="anomaly",
        value="inventory_censored_episode",
    )
    result = EvaluationSegment("anomaly", retained_median_baseline=False).result()
    assert scope.dimension == "anomaly" and result["dimension"] == "anomaly"
    assert result["status"] == "not_ready"


@pytest.mark.parametrize("mutation", ["prefix", "changed_stratum"])
def test_finish_requires_exact_full_context_trace_and_every_segment_membership(mutation):
    rows = contexts()
    stream = collector(rows)
    for ctx in rows[:1] if mutation == "prefix" else rows:
        if mutation == "changed_stratum" and ctx == rows[0]:
            ctx = ctx.model_copy(update={"origin_rolling_28_mean": 30.0})
        stream.add(row(ctx), None if not ctx.eligible else 2, ctx)
    with pytest.raises(SnapshotError, match="mismatch"):
        stream.finish()
    with pytest.raises(SnapshotError, match="stream_unavailable"):
        stream.finish()
