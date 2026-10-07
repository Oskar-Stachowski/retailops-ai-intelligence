"""Exact rank math and real role files on controls; no project qualification."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from pydantic import ValidationError
from test_campaign_tune_data import diagnostic, score_receipt, tune_plan, values
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.common import ForecastKey
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_calibration_data as data
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationPlan,
    CampaignForecastHorizonCalibration,
    residual_rank,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastRawPrediction
from retailops_ai.evaluation_campaign.campaign_score_metrics import RawMetrics
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.evaluation_campaign.physical_contract import PhysicalForecastExample
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


def calibration_plan(ids=("controlled-calibration-score",), **changes):
    tune = tune_plan()
    return CampaignForecastCalibrationPlan(
        **(
            dict(
                source_recipe_sha256=tune.source_recipe_sha256,
                export_operation_id=tune.export_operation_id,
                tune_operation_id="controlled-tune",
                score_operation_ids=ids,
                forecast_quality_policy_sha256=tune.forecast_quality_policy_sha256,
                worker_environment_lock_sha256=tune.worker_environment_lock_sha256,
                resources=tune.resources,
            )
            | changes
        )
    )


def selected():
    trial, score = diagnostic()
    return choose([trial], {score.operation_id: score}, tune_plan())


def residual_control(tmp_path, *, n=100, excluded=0, absent=None, zero=False):
    db = sqlite3.connect(tmp_path / "rank.sqlite")
    db.execute("CREATE TABLE residuals(horizon INTEGER,error REAL,ordinal INTEGER)")
    counts = {h: (n + excluded, n) for h in range(1, 15)}
    for h in counts:
        if h == absent:
            counts[h] = (0, 0)
            continue
        db.executemany(
            "INSERT INTO residuals VALUES (?,?,?)",
            ((h, 0.0 if zero else float(i), i) for i in reversed(range(n))),
        )
    return db, counts


@pytest.mark.parametrize(
    "n,rank", [(0, 1), (8, 9), (9, 9), (10, 10), (99, 90), (100, 91), (109, 99), (10**7, 9000001)]
)
def test_exact_finite_sample_rank_without_float_rounding_or_clamping(n, rank):
    assert residual_rank(n) == rank


@pytest.mark.parametrize("zero", [False, True])
def test_all_fourteen_horizons_use_exact_order_statistic_and_fixed_nonnegative_bounds(
    tmp_path, zero
):
    db, counts = residual_control(tmp_path, zero=zero)
    with closing(db):
        fitted = data.fit_horizons(
            db, counts, selected(), "controlled-calibration-score", calibration_plan()
        )
    assert fitted.calibration_fitted and fitted.status == "fitted_for_independent_evaluation"
    assert [h.radius for h in fitted.horizons] == [0.0 if zero else 90.0] * 14
    assert all(h.quantile_rank == 91 for h in fitted.horizons)
    band = data.interval(fitted, 14, 10.0)
    assert (
        (band.lower, band.upper) == (10.0, 10.0)
        if zero
        else (band.lower, band.upper) == (0.0, 100.0)
    )
    assert not fitted.temporal_coverage_guarantee_claimed and not fitted.quality_qualified


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"n": 99}, "insufficient_calibration_rows"),
        ({"n": 8}, "finite_sample_order_statistic_unavailable"),
        ({"excluded": 26}, "calibration_eligibility_coverage_below_minimum"),
        ({"absent": 14}, "insufficient_calibration_rows"),
    ],
)
def test_underpowered_censored_or_missing_horizon_cannot_hide_behind_pooling(
    tmp_path, change, reason
):
    db, counts = residual_control(tmp_path, **change)
    with closing(db):
        fitted = data.fit_horizons(
            db, counts, selected(), "controlled-calibration-score", calibration_plan()
        )
    assert not fitted.calibration_fitted and fitted.status == "not_ready"
    assert any(reason in h.reasons and h.radius is None for h in fitted.horizons)
    with pytest.raises(SnapshotError, match="unavailable"):
        data.interval(fitted, 1, 10.0)


def test_exact_eighty_percent_coverage_is_accepted_and_residual_count_is_checked(tmp_path):
    db, counts = residual_control(tmp_path, excluded=25)
    with closing(db):
        assert data.fit_horizons(
            db, counts, selected(), "controlled-calibration-score", calibration_plan()
        ).calibration_fitted
        db.execute("DELETE FROM residuals WHERE horizon=14 AND ordinal=0")
        with pytest.raises(SnapshotError, match="count_mismatch"):
            data.fit_horizons(
                db, counts, selected(), "controlled-calibration-score", calibration_plan()
            )


@pytest.mark.parametrize(
    "median,horizon", [(float("nan"), 1), (float("inf"), 1), (-1.0, 1), (1.0, 0), (1.0, 15)]
)
def test_fixed_interval_rejects_invalid_input(tmp_path, median, horizon):
    db, counts = residual_control(tmp_path)
    with closing(db):
        fitted = data.fit_horizons(
            db, counts, selected(), "controlled-calibration-score", calibration_plan()
        )
    with pytest.raises(SnapshotError):
        data.interval(fitted, horizon, median)


def test_typed_result_cannot_claim_fitted_with_an_unavailable_sample():
    with pytest.raises(ValidationError):
        CampaignForecastHorizonCalibration(
            horizon_days=1, rows=99, eligible_rows=99, quantile_rank=90, radius=1.0, reasons=()
        )


@pytest.fixture
def calibration_control(stored_control, tmp_path):
    dataset, manifest = stored_control
    examples = [
        PhysicalForecastExample.model_validate_json(line)
        for line in (dataset / "calibration.jsonl").read_bytes().splitlines()
    ]
    rows, metrics = [], RawMetrics()
    keys, eligible_keys = hashlib.sha256(), hashlib.sha256()
    for example in examples:
        outcome = example.outcome
        row = CampaignForecastRawPrediction(
            **example.membership.model_dump(include=set(ForecastKey.model_fields)),
            role="calibration",
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
    bundle = tmp_path / "calibration-raw-bundle"
    bundle.mkdir(mode=0o700)
    prototype = score_receipt(
        "controlled-score",
        len(rows),
        sum(row.eligible for row in rows),
        keys.hexdigest(),
        eligible_keys.hexdigest(),
        manifest.descriptor.populations["calibration"].sha256,
        dataset_id=manifest.dataset_id,
        runtime=manifest.descriptor.runtime.code_sha256,
    )
    score = prototype.model_copy(
        update={
            "operation_id": "controlled-calibration-score",
            "plan": prototype.plan.model_copy(update={"role": "calibration"}),
        }
    )
    write(bundle / "plan.json", score.plan.model_dump(mode="json"))
    write(
        bundle / "parents.json",
        {
            "export_receipt_sha256": score.export_receipt_sha256,
            "fit_receipt_sha256": score.fit_receipt_sha256,
            "model_artifact_sha256": score.model_artifact_sha256,
        },
    )
    write(bundle / "metrics.json", metrics.result())
    (bundle / "predictions.jsonl").write_bytes(
        b"".join(canonical_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)
    )
    files, size = _bundle_inventory(bundle, score.plan.max_output_bytes)
    score = score.model_copy(
        update={
            "artifact_files": files,
            "artifact_bytes": size,
            "artifact_sha256": canonical_sha256(files),
        }
    )
    return dataset, bundle, manifest, score, calibration_plan()


def test_real_calibration_pass_only_opens_its_role_and_keeps_exclusions(
    calibration_control, tmp_path, monkeypatch
):
    opened, original = [], data.regular_file

    def capture(root, name):
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(data, "regular_file", capture)
    dataset, bundle, manifest, score, plan = calibration_control
    root = tmp_path / "fit-residuals"
    root.mkdir(mode=0o700)
    fitted, proof = data.fit(root, dataset, bundle, manifest, score, selected(), plan)
    assert opened == ["calibration.jsonl", "predictions.jsonl"]
    assert fitted.rows == score.rows and fitted.eligible_rows == score.eligible_rows
    assert (
        proof["full_calibration_label_passes"] == 1
        and proof["tune_label_passes"] == proof["independent_or_final_label_passes"] == 0
    )
    assert fitted.status == "not_ready"  # Controlled role does not meet all fourteen sample gates.


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
        "prediction_digest",
        "budget",
    ],
)
def test_full_pair_corruption_cannot_fit_a_calibrator(calibration_control, mutation):
    dataset, bundle, manifest, score, plan = calibration_control
    path = bundle / "predictions.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    if mutation == "missing":
        lines.pop()
    elif mutation == "extra":
        lines.append(lines[-1])
    elif mutation == "duplicate":
        lines[1] = lines[0]
    elif mutation in ("wrong_example", "wrong_role"):
        row = json.loads(lines[0])
        row["example_sha256" if mutation == "wrong_example" else "role"] = (
            "f" * 64 if mutation == "wrong_example" else "tune"
        )
        lines[0] = canonical_bytes(row) + b"\n"
    elif mutation in ("key_digest", "role_digest"):
        score = score.model_copy(
            update={
                "keys_sha256" if mutation == "key_digest" else "role_population_sha256": "f" * 64
            }
        )
    elif mutation == "prediction_digest":
        score = score.model_copy(
            update={"artifact_files": score.artifact_files | {"predictions.jsonl": "f" * 64}}
        )
    else:
        plan = plan.model_copy(update={"max_rows": 1})
    path.write_bytes(b"".join(lines))
    with pytest.raises((SnapshotError, ValidationError)):
        list(data.pairs(dataset, bundle, manifest, score, plan))
