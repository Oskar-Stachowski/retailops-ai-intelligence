"""Controlled paired records and selection math; never project or final evidence."""

import hashlib
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_campaign_score_data import score_plan
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    CampaignForecastRawPrediction,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastChoice,
    CampaignForecastTunePlan,
)
from retailops_ai.evaluation_campaign.campaign_tune_data import BandMetrics, choose, paired_metrics
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastExample
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def tune_plan(ids=("controlled-score",), **changes):
    score = score_plan()
    return CampaignForecastTunePlan(
        **(
            dict(
                source_recipe_sha256=score.source_recipe_sha256,
                export_operation_id=score.export_operation_id,
                score_operation_ids=ids,
                campaign_selection_policy_sha256="1" * 64,
                forecast_quality_policy_sha256="2" * 64,
                worker_environment_lock_sha256=score.worker_environment_lock_sha256,
                resources=score.resources,
            )
            | changes
        )
    )


def values(*, hgb_median=10.0, tf_median=9.5, rf_mean=10.0):
    band = CentralInterval(lower=8.0, upper=10.0)
    return (
        FunctionalForecast(mean=11.0, median=9.0, interval=band),
        FunctionalForecast(mean=12.0, median=8.0, interval=CentralInterval(lower=8.0, upper=11.0)),
        FunctionalForecast(mean=13.0, median=9.0, interval=CentralInterval(lower=9.0, upper=11.0)),
        FunctionalForecast(mean=rf_mean, median=None, interval=None),
        FunctionalForecast(mean=10.5, median=hgb_median, interval=None),
        FunctionalForecast(mean=10.75, median=tf_median, interval=None),
    )


def prediction(index, forecasts, *, eligible=True):
    target = date(2026, 1, 2) + timedelta(days=index)
    return CampaignForecastRawPrediction(
        product_id="product-a",
        selling_location_id="location-a",
        channel="store",
        forecast_origin=make_origin(target - timedelta(days=1)).forecast_origin,
        business_timezone="UTC",
        cutoff_policy="end_of_day_second_v1",
        target_date=target,
        horizon_days=1,
        role="tune",
        example_sha256="1" * 64,
        eligible=eligible,
        exclusion_reasons=() if eligible else ("closed_target",),
        values=forecasts
        if eligible
        else tuple(FunctionalForecast(mean=None, median=None, interval=None) for _ in range(6)),
    )


def score_receipt(
    operation,
    rows,
    eligible,
    keys,
    eligible_keys,
    role_sha,
    *,
    files=None,
    size=1,
    dataset_id=None,
    runtime="3" * 64,
):
    plan = score_plan(
        fit_operation_ids={f: operation + "-fit-" + f for f in ("rf", "hgb", "tensorflow")}
    )
    files = files or {
        name: "1" * 64
        for name in ("plan.json", "parents.json", "predictions.jsonl", "metrics.json")
    }
    return CampaignForecastScoreReceipt(
        protocol_sha256="1" * 64,
        operation_id=operation,
        reservation_id="campaign-operation-" + "1" * 32,
        plan=plan,
        export_receipt_sha256="2" * 64,
        fit_receipt_sha256={f: "3" * 64 for f in ("rf", "hgb", "tensorflow")},
        model_artifact_sha256={
            f: canonical_sha256(operation + f) for f in ("rf", "hgb", "tensorflow")
        },
        dataset_id=dataset_id or "ai09-physical-forecast-sha256-" + "4" * 64,
        runtime_code_sha256=runtime,
        role_population_sha256=role_sha,
        rows=rows,
        eligible_rows=eligible,
        keys_sha256=keys,
        eligible_keys_sha256=eligible_keys,
        artifact_sha256=canonical_sha256(files),
        artifact_bytes=size,
        artifact_files=files,
        worker_evidence={"controlled_metadata_only": True},
    )


def diagnostic(operation="controlled-score", *, forecasts=None, actual=10, count=100, censored=0):
    raw = RawMetrics()
    band_metrics = {
        m: BandMetrics()
        for m in ("history7", "history28", "weekday28", "rf_mean", "hgb", "tensorflow")
    }
    for index in range(count + censored):
        row = prediction(index, forecasts or values(), eligible=index < count)
        raw.add(row, actual)
        if row.eligible:
            for model, forecast in zip(band_metrics, row.values, strict=True):
                band_metrics[model].add(actual, forecast.interval, 0.9)
    metrics = raw.result()
    for segment in metrics["segments"]:
        for model in band_metrics:
            segment["models"][model]["interval"] = band_metrics[model].result(
                segment["eligible_rows"]
            )
    trial = dict(
        score_operation_id=operation,
        metrics=metrics,
        rows=count + censored,
        eligible_rows=count,
        keys_sha256="1" * 64,
        eligible_keys_sha256="2" * 64,
        role_population_sha256="3" * 64,
        baseline_predictions_sha256="4" * 64,
    )
    receipt = score_receipt(operation, count + censored, count, "1" * 64, "2" * 64, "3" * 64)
    return trial, receipt


def test_mean_median_and_raw_interval_references_are_selected_separately():
    trial, score = diagnostic()
    selected = choose([trial], {score.operation_id: score}, tune_plan())
    assert selected.status == "selected_for_independent_evaluation"
    assert selected.mean.model == "rf_mean" and selected.median.model == "hgb"
    assert selected.mean.fit_operation_id == score.plan.fit_operation_ids["rf"]
    assert selected.median.model_artifact_sha256 == score.model_artifact_sha256["hgb"]
    assert (
        selected.baseline_mean
        == selected.baseline_median
        == selected.baseline_interval
        == "history7"
    )
    assert selected.candidate_interval_center == selected.median
    assert (
        not selected.calibration_fitted
        and not selected.quality_qualified
        and not selected.stage_ready
    )


def test_every_frozen_trial_participates_and_tie_order_is_deterministic():
    a, sa = diagnostic("trial-z", forecasts=values(hgb_median=9.5, tf_median=9.4))
    b, sb = diagnostic("trial-a", forecasts=values(hgb_median=9.3, tf_median=10.0))
    selected = choose([a, b], {"trial-z": sa, "trial-a": sb}, tune_plan(("trial-z", "trial-a")))
    assert selected.median.model == "tensorflow" and selected.median.score_operation_id == "trial-a"
    b, sb = diagnostic("trial-a", forecasts=values(hgb_median=9.5, tf_median=9.4))
    selected = choose([a, b], {"trial-z": sa, "trial-a": sb}, tune_plan(("trial-z", "trial-a")))
    assert selected.median.score_operation_id == "trial-z"


def test_mean_bias_guard_and_strict_median_improvement_keep_the_baseline():
    forecasts = list(values(hgb_median=81.0, tf_median=81.0, rf_mean=80.0))
    for i, mean in enumerate((120.0, 130.0, 140.0, 80.0, 111.0, 110.5)):
        forecasts[i] = forecasts[i].model_copy(update={"mean": mean})
    for i, median in enumerate((80.0, 70.0, 80.0)):
        forecasts[i] = forecasts[i].model_copy(
            update={"median": median, "interval": CentralInterval(lower=70.0, upper=120.0)}
        )
    trial, score = diagnostic(forecasts=tuple(forecasts), actual=100)
    # Candidate bias above 10% cannot win mean; exact 5% median improvement is insufficient.
    selected = choose([trial], {score.operation_id: score}, tune_plan())
    assert selected.mean.model == "history7" and selected.median.model == "history7"


@pytest.mark.parametrize("count,censored", [(0, 100), (99, 0), (100, 26)])
def test_insufficient_or_censored_populations_cannot_select(count, censored):
    trial, score = diagnostic(count=count, censored=censored)
    selected = choose([trial], {score.operation_id: score}, tune_plan())
    assert selected.status == "not_ready" and selected.reasons
    assert selected.mean is selected.median is None


def test_zero_actual_ratios_stay_undefined_and_perfect_baseline_is_retained():
    zero = tuple(
        FunctionalForecast(
            mean=0.0,
            median=None if i == 3 else 0.0,
            interval=CentralInterval(lower=0.0, upper=0.0) if i < 3 else None,
        )
        for i in range(6)
    )
    trial, score = diagnostic(forecasts=zero, actual=0)
    selected = choose([trial], {score.operation_id: score}, tune_plan())
    assert selected.mean.model == selected.median.model == "history7"
    point = trial["metrics"]["segments"][0]["models"]["history7"]["mean"]
    assert point["wape"] is point["normalized_bias"] is None


@pytest.mark.parametrize(
    "field",
    [
        "keys_sha256",
        "eligible_keys_sha256",
        "role_population_sha256",
        "baseline_predictions_sha256",
    ],
)
def test_trial_differences_are_rejected_instead_of_pooled(field):
    a, sa = diagnostic("trial-a")
    b, sb = diagnostic("trial-b")
    b[field] = "e" * 64
    with pytest.raises(SnapshotError, match="population_or_baselines_differ"):
        choose([a, b], {"trial-a": sa, "trial-b": sb}, tune_plan(("trial-a", "trial-b")))


@pytest.mark.parametrize(
    "change",
    [{"role": "calibration"}, {"score_operation_ids": ("same", "same")}, {"stage_ready": True}],
)
def test_plan_cannot_grant_another_role_duplicate_trials_or_ready(change):
    with pytest.raises(ValidationError):
        tune_plan(**change)


def test_learned_choice_cannot_omit_its_fit_or_artifact_binding():
    with pytest.raises(ValidationError, match="complete_learned_artifact"):
        CampaignForecastChoice(model="tensorflow")


@pytest.fixture
def paired_control(stored_control, tmp_path):
    dataset, manifest = stored_control
    examples = [
        PhysicalForecastExample.model_validate_json(line)
        for line in (dataset / "tune.jsonl").read_bytes().splitlines()
    ]
    rows = []
    metrics = RawMetrics()
    keys, eligible_keys = hashlib.sha256(), hashlib.sha256()
    for example in examples:
        outcome = example.outcome
        row = CampaignForecastRawPrediction(
            **example.membership.model_dump(include=set(ForecastKey.model_fields)),
            role="tune",
            example_sha256=canonical_sha256(example.model_dump(mode="json")),
            eligible=outcome.eligible,
            exclusion_reasons=outcome.reasons,
            values=values()
            if outcome.eligible
            else tuple(FunctionalForecast(mean=None, median=None, interval=None) for _ in range(6)),
        )
        rows.append(row)
        keys.update(membership_key(row) + b"\n")
        if row.eligible:
            eligible_keys.update(membership_key(row) + b"\n")
        metrics.add(row, outcome.label.observed_sales_units)
    bundle = tmp_path / "paired-bundle"
    bundle.mkdir(mode=0o700)
    plan = score_plan(
        fit_operation_ids={f: "controlled-score-fit-" + f for f in ("rf", "hgb", "tensorflow")}
    )
    write(bundle / "plan.json", plan.model_dump(mode="json"))
    write(
        bundle / "parents.json",
        {
            "export_receipt_sha256": "2" * 64,
            "fit_receipt_sha256": {f: "3" * 64 for f in ("rf", "hgb", "tensorflow")},
            "model_artifact_sha256": {
                f: canonical_sha256("controlled-score" + f) for f in ("rf", "hgb", "tensorflow")
            },
        },
    )
    write(bundle / "metrics.json", metrics.result())
    (bundle / "predictions.jsonl").write_bytes(
        b"".join(canonical_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)
    )
    files, size = _bundle_inventory(bundle, plan.max_output_bytes)
    score = score_receipt(
        "controlled-score",
        len(rows),
        sum(row.eligible for row in rows),
        keys.hexdigest(),
        eligible_keys.hexdigest(),
        manifest.descriptor.populations["tune"].sha256,
        files=files,
        size=size,
        dataset_id=manifest.dataset_id,
        runtime=manifest.descriptor.runtime.code_sha256,
    )
    return dataset, bundle, manifest, score, tune_plan()


def test_complete_pair_pass_only_opens_tune_labels_and_retains_all_exclusions(
    paired_control, monkeypatch
):
    from retailops_ai.evaluation_campaign import campaign_tune_data as module

    original = module.regular_file
    opened = []

    def capture(root, name):
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(module, "regular_file", capture)
    result = paired_metrics(*paired_control)
    assert opened == ["tune.jsonl", "predictions.jsonl"]
    assert result["rows"] == paired_control[3].rows
    assert result["eligible_rows"] == paired_control[3].eligible_rows
    assert sum(s["rows"] for s in result["metrics"]["segments"][1:]) == result["rows"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "duplicate",
        "wrong_example",
        "wrong_role",
        "key_digest",
        "role_digest",
        "budget",
        "overflow",
    ],
)
def test_pair_corruption_or_resource_overflow_cannot_be_selected(paired_control, mutation):
    dataset, bundle, manifest, score, plan = paired_control
    path = bundle / "predictions.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if mutation == "missing":
        lines.pop()
    elif mutation == "extra":
        lines.append(lines[-1])
    elif mutation == "duplicate":
        lines[1] = lines[0]
    elif mutation in ("wrong_example", "wrong_role", "overflow"):
        import json

        row = json.loads(lines[0])
        if mutation == "wrong_example":
            row["example_sha256"] = "f" * 64
        elif mutation == "wrong_role":
            row["role"] = "calibration"
        else:
            row["values"][3]["mean"] = 1e200
        lines[0] = canonical_bytes(row) + b"\n"
    elif mutation == "key_digest":
        score = score.model_copy(update={"keys_sha256": "f" * 64})
    elif mutation == "role_digest":
        score = score.model_copy(update={"role_population_sha256": "f" * 64})
    else:
        plan = plan.model_copy(update={"max_rows": 1})
    path.write_bytes(b"".join(lines))
    with pytest.raises((SnapshotError, ValidationError)):
        paired_metrics(dataset, bundle, manifest, score, plan)
