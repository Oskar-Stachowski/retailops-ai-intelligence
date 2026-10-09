"""Full numerical fit controls, independent of any Project or fresh Source data."""

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import numeric_rows, point, scope

from retailops_ai.anomaly_detectors.census_contract import (
    CensusFitPolicy,
    CensusGroup,
    CensusPipeline,
)
from retailops_ai.anomaly_detectors.census_fit import (
    census_capacity_threshold,
    fit_census_pipeline,
)
from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import FitPolicy, Pipeline, Threshold
from retailops_ai.anomaly_detectors.engine import capacity_threshold
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import (
    CensusCountRateDescriptor,
    EventCapacity,
    Model,
    count_rate_row,
    score,
)
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256


def test_full_fit_preserves_native_small_matrix_exactly(tmp_path):
    rows = [
        r.model_copy(update={"planned_price": float(i) if i % 3 else None})
        for i, r in enumerate(numeric_rows(64))
    ]
    policy = CensusFitPolicy(n_estimators=8, max_samples=32)
    legacy, _ = fit_pipeline(rows, rows[:8], FitPolicy(n_estimators=8, max_samples=32))
    # Wild held-out probes cannot change training medians or the fitted forest.
    probes = [r.model_copy(update={"planned_price": 1e10}) for r in rows[:8]]
    actual, cost = fit_census_pipeline(iter(rows), len(rows), probes, policy, scratch=tmp_path)
    assert actual.model_dump(mode="json") == legacy.model_dump(mode="json")
    assert cost.native_max_score_error <= 1e-12
    assert cost.wall_seconds > 0 and cost.cpu_seconds > 0 and cost.peak_rss_bytes > 0
    assert not list(tmp_path.iterdir())


def test_all_rows_above_legacy_cap_fit_reload_and_native_score(tmp_path):
    rows = [
        r.model_copy(update={"planned_price": 1.0 if i < 5000 else 101.0})
        for i, r in enumerate(numeric_rows(10017))
    ]
    policy = CensusFitPolicy(n_estimators=8, max_samples=32)
    consumed = []

    def source():
        for i, row in enumerate(rows):
            consumed.append(i)
            yield row

    pipeline, _ = fit_census_pipeline(source(), len(rows), rows[:4], policy, scratch=tmp_path)
    assert consumed == list(range(10017))
    assert pipeline.training_rows == 10017
    assert pipeline.training_rows_sha256 == json_sha256([r.model_dump(mode="json") for r in rows])
    fill = next(f for f in pipeline.fills if f.name == "planned_price")
    assert fill.known_count == 10017 and fill.value == 101.0  # Prefix median would be 51.
    with pytest.raises(ValidationError):
        Pipeline.model_validate_json(pipeline.model_dump_json())
    restored = CensusPipeline.model_validate_json(pipeline.model_dump_json())
    assert restored == pipeline
    baseline = census_capacity_threshold(
        (baseline_score(r) for r in rows), len(rows), policy, scratch=tmp_path
    )
    assert baseline is not None and baseline.validation_rows == 10017
    with pytest.raises(ValidationError):
        Threshold.model_validate_json(baseline.model_dump_json())
    native_scores = forest_scores(restored, rows[:32])
    forest_threshold = census_capacity_threshold(
        iter(native_scores), len(native_scores), policy, scratch=tmp_path
    )
    descriptor = CensusCountRateDescriptor(
        source_dataset_id="source-sha256-" + "1" * 64,
        feature_manifest_sha256="3" * 64,
        feature_rows_sha256="4" * 64,
        train=Window(start=date(2026, 7, 1), end=date(2026, 7, 20)),
        validation=Window(start=date(2026, 7, 23), end=date(2026, 8, 7)),
        training_cutoff=datetime(2026, 7, 22, tzinfo=UTC),
        selection_cutoff=datetime(2026, 8, 11, tzinfo=UTC),
        policy=policy,
        groups=(
            CensusGroup(
                event_type="sale_completed",
                currency="PLN",
                training_rows=len(rows),
                pipeline=pipeline,
                baseline_threshold=baseline,
                forest_threshold=forest_threshold,
            ),
        ),
        training_membership_sha256="5" * 64,
        validation_membership_sha256="6" * 64,
        code_sha256="7" * 64,
        dependency_lock_sha256="8" * 64,
        event_capacities=tuple(
            EventCapacity(event_type=e, alert_fraction=0.05, high_fraction=0.01)
            for e in ("sale_completed", "return_completed")
        ),
    )
    model = Model(
        detector_id="anomaly-detector-sha256-" + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    model = Model.model_validate_json(model.model_dump_json())
    assert isinstance(model.descriptor, CensusCountRateDescriptor)
    assert model.descriptor.qualified_anomaly_input_id is None
    day = date(2026, 8, 12)
    public = point(day, 90)
    numerical = count_rate_row(public, {})
    assert numerical is not None
    for family in ("seasonal_residual", "isolation_forest"):
        decisions = score(
            model,
            [public],
            (scope(),),
            Window(start=day, end=day),
            family,
            "batch",
            datetime(2026, 8, 16, tzinfo=UTC),
        )
        verify_scores(model, decisions, [numerical.model_dump(mode="json")])
        assert decisions[0].status == "scored"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "values", [[], [0.0] * 15, [0.0] * 32, [float(i // 3) for i in range(101)]]
)
def test_full_threshold_has_exact_native_count_ties_and_score_identity(values, tmp_path):
    actual = census_capacity_threshold(
        iter(values), len(values), CensusFitPolicy(), scratch=tmp_path
    )
    expected = capacity_threshold(values, FitPolicy())
    assert (actual.model_dump() if actual else None) == (
        expected.model_dump() if expected else None
    )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("count,actual", [(32, 31), (32, 33)])
def test_missing_or_extra_train_row_fails_before_worker(count, actual, tmp_path, monkeypatch):
    from retailops_ai.anomaly_detectors import census_fit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid census must not start native worker")

    monkeypatch.setattr(census_fit.subprocess, "Popen", forbidden)
    with pytest.raises(SnapshotError, match="(extra|missing)_training_row"):
        fit_census_pipeline(
            iter(numeric_rows(actual)), count, [], CensusFitPolicy(), scratch=tmp_path
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "scores,count",
    [
        ([0.0] * 17, 16),
        ([0.0] * 15, 16),
        ([float("nan")], 1),
        ([float("inf")], 1),
        ([-1.0], 1),
        ([True], 1),
    ],
)
def test_incomplete_or_invalid_validation_cannot_set_threshold(scores, count, tmp_path):
    with pytest.raises(SnapshotError):
        census_capacity_threshold(iter(scores), count, CensusFitPolicy(), scratch=tmp_path)
    assert not list(tmp_path.iterdir())


def test_oversized_count_is_rejected_without_consuming_rows(tmp_path):
    def untouched():
        raise AssertionError("over-budget census cannot consume input")
        yield

    with pytest.raises(SnapshotError, match="training_budget"):
        fit_census_pipeline(untouched(), 1000001, [], CensusFitPolicy(), scratch=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("resource", ["memory", "disk"])
def test_host_reserve_refusal_precedes_consumption(resource, tmp_path, monkeypatch):
    from retailops_ai.anomaly_detectors import census_fit

    def untouched():
        raise AssertionError("resource refusal must precede input consumption")
        yield

    if resource == "memory":
        monkeypatch.setattr(
            census_fit.psutil, "virtual_memory", lambda: SimpleNamespace(available=0)
        )
    else:
        monkeypatch.setattr(census_fit.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))
    with pytest.raises(SnapshotError, match="host_reserve"):
        fit_census_pipeline(untouched(), 32, [], CensusFitPolicy(), scratch=tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("name", ["x.npy", "probes.npy"])
def test_matrix_mutation_cannot_publish_fitted_model(name, tmp_path, monkeypatch):
    from retailops_ai.anomaly_detectors import census_fit

    popen = census_fit.subprocess.Popen

    def mutate_then_start(command, **kwargs):
        path = Path(command[-1]) / name
        with path.open("ab") as stream:
            stream.write(b"changed after preparation")
        return popen(command, **kwargs)

    monkeypatch.setattr(census_fit.subprocess, "Popen", mutate_then_start)
    with pytest.raises(SnapshotError, match="worker_failed"):
        fit_census_pipeline(iter(numeric_rows(32)), 32, [], CensusFitPolicy(), scratch=tmp_path)
    assert not list(tmp_path.iterdir())


def test_census_policy_requires_explicit_new_version():
    with pytest.raises(ValidationError):
        FitPolicy.model_validate_json(CensusFitPolicy().model_dump_json())


@pytest.mark.parametrize("field,value", [("peak_rss_bytes", 1024**3 + 1), ("cpu_seconds", 301)])
def test_final_worker_peak_and_cpu_still_enforce_unchanged_limits(
    field, value, tmp_path, monkeypatch
):
    import json

    from retailops_ai.anomaly_detectors import census_fit

    original = census_fit.read_bytes

    def reported_peak(root, name, maximum):
        raw = original(root, name, maximum)
        if name == "resources.json":
            record = json.loads(raw)
            record[field] = value
            return json.dumps(record).encode()
        return raw

    monkeypatch.setattr(census_fit, "read_bytes", reported_peak)
    with pytest.raises(SnapshotError, match="fit_final_resource_budget") as caught:
        fit_census_pipeline(iter(numeric_rows(32)), 32, [], CensusFitPolicy(), scratch=tmp_path)
    measured = json.loads(str(caught.value).split(": ", 1)[1])
    assert measured["measurement"][field] >= value
    assert measured["parent_sampled_peak_rss_bytes"] > 0
    assert not list(tmp_path.iterdir())
