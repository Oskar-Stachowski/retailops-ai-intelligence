"""Saved genuine forest, causal cutoff and null abstention use the same scorer."""

import hashlib
from datetime import UTC, date, datetime

import pytest
from test_anomaly_detectors import numeric_rows, point, scope

from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import FitPolicy, Group
from retailops_ai.anomaly_detectors.engine import capacity_threshold
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_portfolio.model import Descriptor, Model, load, row, score
from retailops_ai.source_snapshot.files import canonical_json, json_sha256


@pytest.fixture(scope="module")
def saved_model():
    policy = FitPolicy(n_estimators=8, max_samples=32)
    train = numeric_rows(32)
    pipeline, _ = fit_pipeline(train, train, policy)
    descriptor = Descriptor(
        source_dataset_id="source-sha256-" + "1" * 64,
        qualified_anomaly_input_id="qualified-anomaly-inputs-sha256-" + "2" * 64,
        feature_manifest_sha256="3" * 64,
        feature_rows_sha256="4" * 64,
        train=Window(start=date(2026, 7, 1), end=date(2026, 7, 20)),
        validation=Window(start=date(2026, 7, 23), end=date(2026, 8, 7)),
        training_cutoff=datetime(2026, 7, 22, tzinfo=UTC),
        selection_cutoff=datetime(2026, 8, 11, tzinfo=UTC),
        policy=policy,
        groups=(
            Group(
                event_type="sale_completed",
                currency="PLN",
                training_rows=32,
                pipeline=pipeline,
                baseline_threshold=capacity_threshold([baseline_score(r) for r in train], policy),
                forest_threshold=capacity_threshold(list(forest_scores(pipeline, train)), policy),
            ),
        ),
        training_membership_sha256="5" * 64,
        validation_membership_sha256="6" * 64,
        code_sha256="7" * 64,
        dependency_lock_sha256="8" * 64,
    )
    return Model(
        detector_id="anomaly-detector-sha256-" + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )


def test_saved_model_scores_actual_forest_and_baseline_without_fallback(tmp_path, saved_model):
    path = tmp_path / "model.json"
    raw = canonical_json(saved_model.model_dump(mode="json")) + b"\n"
    path.write_bytes(raw)
    loaded = load(path, hashlib.sha256(raw).hexdigest())
    day = date(2026, 8, 12)
    p = point(day, 90)
    args = ([p], (scope(),), Window(start=day, end=day))
    for family in ("seasonal_residual", "isolation_forest"):
        result = score(loaded, *args, family, "batch", datetime(2026, 8, 16, tzinfo=UTC))
        assert result == score(
            saved_model, *args, family, "batch", datetime(2026, 8, 16, tzinfo=UTC)
        )
        expected = (
            baseline_score(row(p))
            if family == "seasonal_residual"
            else forest_scores(saved_model.descriptor.groups[0].pipeline, [row(p)])[0]
        )
        assert result[0].score == expected and result[0].status == "scored"
    with pytest.raises(ValueError, match="pin_or_encoding"):
        load(path, "0" * 64)
    assert loaded.descriptor.groups[0].pipeline.forest.trees


def test_unknown_series_and_unavailable_outcome_never_receive_zero_scores(saved_model):
    day = date(2026, 8, 12)
    window = Window(start=day, end=day)
    result = score(
        saved_model,
        [],
        (scope(),),
        window,
        "isolation_forest",
        "batch",
        datetime(2026, 8, 16, tzinfo=UTC),
    )
    assert (
        result[0].status == "insufficient_data"
        and result[0].score is None
        and result[0].alert is None
    )
    with pytest.raises(ValueError, match="scoring_cutoff"):
        score(
            saved_model,
            [point(day)],
            (scope(),),
            window,
            "isolation_forest",
            "batch",
            datetime(2026, 8, 13, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="utc_or_input_budget"):
        score(
            saved_model, [], (scope(),), window, "seasonal_residual", "batch", datetime(2026, 8, 16)
        )


def test_validation_role_cannot_score_holdout_and_duplicate_input_fails(saved_model):
    day = date(2026, 8, 12)
    p = point(day)
    window = Window(start=day, end=day)
    with pytest.raises(ValueError, match="scoring_cutoff"):
        score(
            saved_model,
            [p],
            (scope(),),
            window,
            "seasonal_residual",
            "validation",
            datetime(2026, 8, 16, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="duplicate_feature"):
        score(
            saved_model,
            [p, p],
            (scope(),),
            window,
            "seasonal_residual",
            "batch",
            datetime(2026, 8, 16, tzinfo=UTC),
        )
