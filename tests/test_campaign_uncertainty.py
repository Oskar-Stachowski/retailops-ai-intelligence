"""Paired cluster equations, complete censuses and undefined statistical controls."""

import hashlib
import json
import math
from datetime import date, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPrediction,
    CampaignForecastReference,
)
from retailops_ai.evaluation_campaign.campaign_uncertainty import PairedForecastUncertainty
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
    CampaignForecastUncertaintyReport,
    CampaignUncertaintyScope,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def policy(**updates):
    return CampaignForecastUncertaintyPolicy(
        resamples=199,
        minimum_eligible_time_blocks=2,
        minimum_eligible_series_clusters=2,
        **updates,
    )


def scope(**updates):
    return CampaignUncertaintyScope(
        **(
            {
                "data_seed": 42,
                "role": "development_evaluation",
                "dataset_id": "ai09-physical-forecast-sha256-" + "a" * 64,
                "source_recipe_sha256": "a" * 64,
                "frozen_configuration_sha256": "b" * 64,
                "scenario": "normal",
                "dimension": "global",
                "value": "all",
            }
            | updates
        )
    )


def row(
    product="p1",
    day=date(2026, 1, 10),
    horizon=1,
    *,
    c=11.0,
    r=12.0,
    cb=(9.0, 11.0),
    rb=(8.0, 12.0),
    excluded=False,
    missing_mean=False,
):
    return CampaignForecastEvaluationPrediction(
        product_id=product,
        selling_location_id="s1",
        channel="store",
        forecast_origin=end_of_day(day),
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=day + timedelta(days=horizon),
        horizon_days=horizon,
        role="development_evaluation",
        example_sha256="a" * 64,
        frozen_configuration_sha256="b" * 64,
        eligible=not excluded,
        exclusion_reasons=("closed_target",) if excluded else (),
        candidate=FunctionalForecast(
            mean=None if excluded or missing_mean else c,
            median=None if excluded else c,
            interval=None if excluded or cb is None else CentralInterval(lower=cb[0], upper=cb[1]),
        ),
        reference=CampaignForecastReference(
            mean=None if excluded else r,
            median=None if excluded else r,
            interval=None if excluded or rb is None else CentralInterval(lower=rb[0], upper=rb[1]),
            interval_center=None if excluded or rb is None else (rb[0] + rb[1]) / 2,
        ),
    )


def control_rows():
    return [
        (row(p, day), 10) for p in ("p1", "p2") for day in (date(2026, 1, 10), date(2026, 2, 10))
    ]


def census(rows):
    keys, eligible = hashlib.sha256(), hashlib.sha256()
    for prediction, _ in sorted(rows, key=lambda x: membership_key(x[0])):
        value = membership_key(prediction) + b"\n"
        keys.update(value)
        if prediction.eligible:
            eligible.update(value)
    return {
        "expected_rows": len(rows),
        "expected_eligible_rows": sum(r.eligible for r, _ in rows),
        "expected_keys_sha256": keys.hexdigest(),
        "expected_eligible_keys_sha256": eligible.hexdigest(),
    }


def evaluate(path, rows, *, active_policy=None, active_scope=None):
    with PairedForecastUncertainty(
        path, active_scope or scope(), active_policy or policy()
    ) as index:
        for prediction, actual in sorted(rows, key=lambda x: membership_key(x[0])):
            index.add(prediction, actual)
        return index.report(**census(rows))


def test_pairing_preserves_known_constant_deltas_for_both_whole_cluster_methods(tmp_path):
    report = evaluate(tmp_path / "index.sqlite", control_rows())
    expected = {
        "median_mae_delta": -1.0,
        "median_wape_delta": -0.1,
        "relative_median_mae_change": -0.5,
        "mean_mse_delta": -3.0,
        "normalized_mean_bias_delta": -0.1,
        "interval_score_delta": -2.0,
        "interval_coverage_delta": 0.0,
    }
    for method in report.methods:
        assert method.status == "evaluated" and method.clusters == method.eligible_clusters == 2
        assert method.rows == method.eligible_rows == 4 and method.actual_units == 40
        for name, metric in method.metrics.items():
            assert metric.point_delta == pytest.approx(expected[name])
            assert metric.lower == pytest.approx(expected[name])
            assert metric.upper == pytest.approx(expected[name])
            assert metric.valid_replicates == 199 and metric.invalid_replicates == 0
    assert report.methods[0].resampling_trace_sha256 != report.methods[1].resampling_trace_sha256
    assert report.all_scope_keys_consumed and not report.quality_qualified
    assert (
        not report.promotion_allowed
        and not report.final_access_authorized
        and not report.stage_ready
    )


def test_unequal_clusters_recompute_row_weighted_ratios_instead_of_averaging_cluster_metrics(
    tmp_path,
):
    rows = [
        (row("p1", date(2026, 1, 10) if h < 4 else date(2026, 2, 10), h), 10) for h in range(1, 6)
    ] + [(row("p2", date(2026, 2, 10), c=15.0, cb=(9.0, 15.0)), 10)]
    report = evaluate(tmp_path / "weighted.sqlite", rows)
    series = report.methods[1]
    assert series.metrics["median_mae_delta"].point_delta == pytest.approx(-1.0 / 3)
    assert series.metrics["median_wape_delta"].point_delta == pytest.approx(-1.0 / 30)
    assert series.metrics["median_mae_delta"].lower == -1.0
    assert series.metrics["median_mae_delta"].upper == 3.0
    assert series.metrics["median_wape_delta"].lower == pytest.approx(-0.1)
    assert series.metrics["median_wape_delta"].upper == pytest.approx(0.3)


@pytest.mark.parametrize("perfect", [False, True])
def test_identical_paired_models_have_zero_delta_and_a_perfect_reference_has_no_relative_ratio(
    tmp_path,
    perfect,
):
    value = 10.0 if perfect else 12.0
    rows = [
        (
            r.model_copy(
                update={
                    "candidate": FunctionalForecast(
                        mean=value, median=value, interval=r.reference.interval
                    ),
                    "reference": r.reference.model_copy(update={"mean": value, "median": value}),
                }
            ),
            a,
        )
        for r, a in control_rows()
    ]
    report = evaluate(tmp_path / "ties.sqlite", rows)
    for method in report.methods:
        for name, metric in method.metrics.items():
            if perfect and name == "relative_median_mae_change":
                assert metric.status == "not_evaluable" and metric.point_delta is None
                assert "zero_reference_error" in metric.reasons and metric.invalid_replicates == 199
            else:
                assert (metric.point_delta, metric.lower, metric.upper) == (0.0, 0.0, 0.0)


def test_zero_actuals_keep_absolute_failures_but_never_report_zero_denominator_ratios(tmp_path):
    rows = [(r, 0) for r, _ in control_rows()]
    report = evaluate(tmp_path / "zero.sqlite", rows)
    for method in report.methods:
        assert method.metrics["median_mae_delta"].point_delta == -1.0
        assert method.metrics["mean_mse_delta"].point_delta == -23.0
        for name in ("median_wape_delta", "normalized_mean_bias_delta"):
            metric = method.metrics[name]
            assert metric.point_delta is None and metric.lower is None and metric.upper is None
            assert "zero_actual_units" in metric.reasons and metric.invalid_replicates == 199


def test_excluded_only_clusters_and_partial_boundaries_are_retained_not_conditioned_away(tmp_path):
    rows = control_rows() + [(row("p3", date(2026, 3, 10), excluded=True), None)]
    report = evaluate(tmp_path / "excluded.sqlite", rows)
    for method in report.methods:
        assert (method.clusters, method.eligible_clusters, method.rows, method.eligible_rows) == (
            3,
            2,
            5,
            4,
        )
        for metric in method.metrics.values():
            assert metric.invalid_replicates > 0 and metric.valid_replicates > 0
            assert metric.valid_replicates + metric.invalid_replicates == 199
            assert metric.point_delta is not None and metric.lower is None and metric.upper is None
            assert "undefined_resampled_metric" in metric.reasons


def test_missing_head_invalidates_only_its_metrics_and_does_not_hide_other_measured_deltas(
    tmp_path,
):
    rows = control_rows()
    rows[0] = (
        rows[0][0].model_copy(
            update={"candidate": rows[0][0].candidate.model_copy(update={"mean": None})}
        ),
        10,
    )
    report = evaluate(tmp_path / "missing.sqlite", rows)
    for method in report.methods:
        assert method.metrics["median_mae_delta"].status == "evaluated"
        metric = method.metrics["mean_mse_delta"]
        assert metric.point_delta is None and metric.status == "not_evaluable"
        assert "missing_mean_prediction" in metric.reasons
        assert metric.invalid_replicates > 0 and metric.valid_replicates > 0


def test_default_minimum_clusters_and_visit_budget_preserve_point_estimates_without_fake_intervals(
    tmp_path,
):
    underpowered = evaluate(
        tmp_path / "small.sqlite", control_rows(), active_policy=CampaignForecastUncertaintyPolicy()
    )
    budget = evaluate(
        tmp_path / "budget.sqlite",
        control_rows(),
        active_policy=policy(max_resampled_cluster_visits=398),
    )
    for report, reason in (
        (underpowered, "insufficient_eligible_clusters"),
        (budget, "resampling_visit_budget"),
    ):
        for method in report.methods:
            assert method.resamples_executed == 0 and method.status == "not_evaluable"
            for metric in method.metrics.values():
                assert metric.point_delta is not None and reason in metric.reasons
                assert (
                    metric.lower is None
                    and metric.valid_replicates == metric.invalid_replicates == 0
                )


def test_deterministic_preregistered_rng_is_method_scope_and_data_seed_bound(tmp_path):
    rows = control_rows()
    first = evaluate(tmp_path / "first.sqlite", rows)
    second = evaluate(tmp_path / "second.sqlite", rows)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    final_rows = [(r.model_copy(update={"role": "final_test"}), a) for r, a in rows]

    def final_scope(seed):
        return scope(
            data_seed=seed, role="final_test", dataset_id="ai09-final-forecast-sha256-" + "a" * 64
        )

    final42 = evaluate(tmp_path / "f42.sqlite", final_rows, active_scope=final_scope(42))
    final137 = evaluate(tmp_path / "f137.sqlite", final_rows, active_scope=final_scope(137))
    assert not final137.independent_rows_or_seeds_pooled
    for i in (0, 1):
        assert (
            final42.methods[i].derived_resampling_seed
            != final137.methods[i].derived_resampling_seed
        )
        assert (
            final42.methods[i].resampling_trace_sha256
            != final137.methods[i].resampling_trace_sha256
        )
        assert (
            first.methods[i].derived_resampling_seed != final42.methods[i].derived_resampling_seed
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "reverse",
        "role",
        "configuration",
        "missing_actual",
        "boolean_actual",
        "excluded_actual",
        "row_budget",
        "cell_budget",
        "index_budget",
        "overflow",
    ],
)
def test_invalid_stream_fails_closed_and_can_never_return_a_report(tmp_path, mutation):
    p = policy(
        **(
            {"max_rows": 1}
            if mutation == "row_budget"
            else {"max_cluster_cells": 2}
            if mutation == "cell_budget"
            else {"max_index_bytes": 4096}
            if mutation == "index_budget"
            else {}
        )
    )
    with PairedForecastUncertainty(tmp_path / "invalid.sqlite", scope(), p) as index:
        first = row()
        next_row = row("p2", date(2026, 2, 10))
        actual = 10
        if mutation != "index_budget":
            index.add(first, 10)
        if mutation == "duplicate":
            next_row = first
        elif mutation == "reverse":
            next_row = row(day=date(2026, 1, 1))
        elif mutation == "role":
            next_row = next_row.model_copy(update={"role": "final_test"})
        elif mutation == "configuration":
            next_row = next_row.model_copy(update={"frozen_configuration_sha256": "f" * 64})
        elif mutation == "missing_actual":
            actual = None
        elif mutation == "boolean_actual":
            actual = True
        elif mutation == "excluded_actual":
            next_row = row("p2", excluded=True)
        elif mutation == "overflow":
            next_row = row("p2", c=1e200, cb=(0.0, 1e200))
        with pytest.raises(SnapshotError):
            index.add(next_row, actual)
        with pytest.raises(SnapshotError, match="stream_unavailable"):
            index.report(**census([(first, 10), (next_row, actual)]))


@pytest.mark.parametrize("mutation", ["truncated", "eligible_count", "keys", "eligible_keys"])
def test_complete_external_census_is_mandatory_and_a_failed_finish_cannot_be_retried(
    tmp_path, mutation
):
    rows = control_rows()
    with PairedForecastUncertainty(tmp_path / "census.sqlite", scope(), policy()) as index:
        for r, a in sorted(rows, key=lambda x: membership_key(x[0]))[
            : (-1 if mutation == "truncated" else None)
        ]:
            index.add(r, a)
        expected = census(rows)
        if mutation == "eligible_count":
            expected["expected_eligible_rows"] -= 1
        elif mutation in ("keys", "eligible_keys"):
            expected["expected_" + mutation + "_sha256"] = "f" * 64
        with pytest.raises(SnapshotError, match="complete_census_mismatch"):
            index.report(**expected)
        with pytest.raises(SnapshotError, match="stream_unavailable"):
            index.report(**census(rows))


def test_large_actual_unit_totals_are_exact_python_integers_not_sqlite_int64(tmp_path):
    actual = 2**63 + 17
    report = evaluate(tmp_path / "large.sqlite", [(r, actual) for r, _ in control_rows()])
    assert all(m.actual_units == 4 * actual for m in report.methods)


def test_empty_scope_is_explicitly_undefined_and_cannot_authorize_final_or_promotion(tmp_path):
    report = evaluate(tmp_path / "empty.sqlite", [])
    assert report.rows == report.eligible_rows == 0
    for method in report.methods:
        assert method.clusters == 0 and method.resamples_executed == 0
        assert all("no_eligible_rows" in m.reasons for m in method.metrics.values())
    value = report.model_dump(mode="json")
    value["promotion_allowed"] = True
    with pytest.raises(ValidationError):
        CampaignForecastUncertaintyReport.model_validate_json(json.dumps(value))


def test_index_is_private_exclusive_and_changes_to_cluster_census_are_rejected(tmp_path):
    path = tmp_path / "private.sqlite"
    rows = control_rows()
    with PairedForecastUncertainty(path, scope(), policy()) as index:
        assert path.stat().st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError):
            PairedForecastUncertainty(path, scope(), policy())
        for r, a in sorted(rows, key=lambda x: membership_key(x[0])):
            index.add(r, a)
        index.db.execute("DELETE FROM clusters WHERE method='time_block'")
        with pytest.raises(SnapshotError, match="cluster_population_changed"):
            index.report(**census(rows))


@pytest.mark.parametrize("updates", [{"data_seed": 137}, {"role": "final_test"}])
def test_development_seed_and_final_dataset_scope_are_not_interchangeable(updates):
    with pytest.raises(ValidationError, match="dataset_role_or_seed_mismatch"):
        scope(**updates)


def test_disk_sufficient_statistics_match_brute_paired_cluster_resampling_equations(tmp_path):
    randomizer = np.random.Generator(np.random.PCG64(2026))
    rows = []
    for product in ("p1", "p2", "p3"):
        for origin in (date(2026, 1, 10), date(2026, 2, 10), date(2026, 3, 10)):
            for horizon in (1, 2, 14):
                actual = int(randomizer.integers(1, 30))
                c, r = float(randomizer.uniform(1, 20)), float(randomizer.uniform(1, 20))
                rows.append(
                    (
                        row(
                            product,
                            origin,
                            horizon,
                            c=c,
                            r=r,
                            cb=(0.0, max(12.0, c)),
                            rb=(0.0, max(18.0, r)),
                        ),
                        actual,
                    )
                )
    report = evaluate(tmp_path / "differential.sqlite", rows)

    def equations(observations):
        n, actual_sum = len(observations), sum(a for _, a in observations)
        delta_absolute = math.fsum(
            abs(p.candidate.median - a) - abs(p.reference.median - a) for p, a in observations
        )
        reference_absolute = math.fsum(abs(p.reference.median - a) for p, a in observations)

        def interval_score(band, actual):
            return (
                band.upper - band.lower + 20.0 * max(0.0, band.lower - actual, actual - band.upper)
            )

        return {
            "median_mae_delta": delta_absolute / n,
            "median_wape_delta": delta_absolute / actual_sum,
            "relative_median_mae_change": delta_absolute / reference_absolute,
            "mean_mse_delta": math.fsum(
                (p.candidate.mean - a) ** 2 - (p.reference.mean - a) ** 2 for p, a in observations
            )
            / n,
            "normalized_mean_bias_delta": math.fsum(
                p.candidate.mean - p.reference.mean for p, a in observations
            )
            / actual_sum,
            "interval_score_delta": math.fsum(
                interval_score(p.candidate.interval, a) - interval_score(p.reference.interval, a)
                for p, a in observations
            )
            / n,
            "interval_coverage_delta": sum(
                int(p.candidate.interval.lower <= a <= p.candidate.interval.upper)
                - int(p.reference.interval.lower <= a <= p.reference.interval.upper)
                for p, a in observations
            )
            / n,
        }

    point = equations(rows)
    for method in report.methods:
        groups = {}
        for p, a in rows:
            if method.method == "time_block":
                identity = str(
                    (p.forecast_origin.date() - report.policy.time_block_anchor).days // 28
                )
            else:
                identity = json.dumps(
                    [p.product_id, p.selling_location_id, p.channel], separators=(",", ":")
                )
            groups.setdefault(identity, []).append((p, a))
        clusters = [groups[key] for key in sorted(groups)]
        rng = np.random.Generator(np.random.PCG64(method.derived_resampling_seed))
        draws = []
        for _ in range(199):
            indices = rng.integers(0, len(clusters), size=len(clusters))
            draws.append(
                equations([observation for i in indices for observation in clusters[int(i)]])
            )
        for name, metric in method.metrics.items():
            lower, upper = np.quantile(
                [draw[name] for draw in draws], [0.025, 0.975], method="linear"
            )
            assert metric.point_delta == pytest.approx(point[name], rel=1e-13, abs=1e-13)
            assert metric.lower == pytest.approx(lower, rel=1e-13, abs=1e-13)
            assert metric.upper == pytest.approx(upper, rel=1e-13, abs=1e-13)


def test_report_wire_cannot_invent_an_interval_with_no_resamples(tmp_path):
    report = evaluate(tmp_path / "wire.sqlite", control_rows())
    value = report.model_dump(mode="json")
    method = value["methods"][0]
    method["resamples_executed"] = 0
    for metric in method["metrics"].values():
        metric["valid_replicates"] = 0
    with pytest.raises(ValidationError, match="interval_status_mismatch"):
        CampaignForecastUncertaintyReport.model_validate_json(json.dumps(value))


def test_index_schema_bytes_count_against_budget_even_without_any_predictions(tmp_path):
    with PairedForecastUncertainty(
        tmp_path / "empty-small.sqlite", scope(), policy(max_index_bytes=4096)
    ) as index:
        with pytest.raises(SnapshotError, match="index_byte_budget"):
            index.report(**census([]))
