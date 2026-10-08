"""Real native scorer/replay, complete large census and causal batch equivalence.

These are exposed controls and a small genuine saved forest. They do not run
Project fits, read final Project data or qualify scientific model performance.
"""

import json
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
import test_anomaly_portfolio_model as native_model_controls
from test_anomaly_detectors import point, scope

from retailops_ai.anomaly_detectors.protocol import Window, series_key
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import (
    CountRateDescriptor,
    Model,
    MultiscaleDescriptor,
    score,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_scoring as worker

saved_model = native_model_controls.saved_model


@pytest.fixture(scope="module")
def multiscale_model(saved_model):
    document = saved_model.descriptor.model_dump(mode="json")
    document["version"] = "anomaly-portfolio-model-2.0.0"
    descriptor = MultiscaleDescriptor.model_validate_json(json.dumps(document))
    return Model(
        detector_id="anomaly-detector-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )


@pytest.fixture(scope="module")
def count_rate_model(multiscale_model):
    document = multiscale_model.descriptor.model_dump(mode="json")
    document.update(
        version="anomaly-portfolio-model-3.0.0",
        residual_recipe="causal-count-residuals-2.0.0",
        event_capacities=[
            {"event_type": kind, "alert_fraction": 0.1, "high_fraction": 0.05}
            for kind in ("sale_completed", "return_completed")
        ],
    )
    descriptor = CountRateDescriptor.model_validate_json(json.dumps(document))
    return Model(
        detector_id="anomaly-detector-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )


@pytest.mark.parametrize("family", ["seasonal_residual", "isolation_forest"])
@pytest.mark.parametrize("model_name", ["saved_model", "multiscale_model", "count_rate_model"])
def test_native_predictions_and_six_day_features_survive_batching(request, model_name, family):
    model = request.getfixturevalue(model_name)
    window = Window(start=date(2026, 8, 12), end=date(2026, 8, 13))
    as_of = datetime(2026, 8, 17, tzinfo=UTC)
    scopes = (scope(), scope(product=str(UUID(int=3))))
    points = [
        point(window.start - timedelta(days=6) + timedelta(days=i), 10 + i * 7) for i in range(8)
    ]
    expected = score(model, points, scopes, window, family, "batch", as_of)
    result = list(
        worker.iter_anomaly_census_scores(
            model, iter(points), scopes, window, family, "batch", as_of, batch_points=8
        )
    )
    assert [r.decision for r in result] == expected
    assert len(result) == 4
    assert all(
        r.decision.status == "insufficient_data"
        and r.model_row is None
        and r.public_point_sha256 is None
        for r in result[2:]
    )
    assert [r.public_point_sha256 for r in result[:2]] == [
        canonical_sha256(p.model_dump(mode="json")) for p in points[-2:]
    ]
    verify_scores(
        model,
        [r.decision for r in result],
        [r.model_row.model_dump(mode="json") if r.model_row else None for r in result],
    )
    if model_name in ("multiscale_model", "count_rate_model"):
        assert [len(r.model_row.recent_counts) for r in result[:2]] == [6, 6]


def test_full_census_larger_than_native_limit_is_not_trimmed(saved_model, monkeypatch):
    scopes = tuple(
        sorted((scope(product=str(UUID(int=i))) for i in range(1, 1301)), key=series_key)
    )
    window = Window(start=date(2026, 8, 12), end=date(2026, 8, 19))
    as_of = datetime(2026, 8, 26, tzinfo=UTC)
    with pytest.raises(ValueError, match="scoring_census_budget"):
        score(saved_model, [], scopes, window, "seasonal_residual", "batch", as_of)
    calls = []
    native = worker.score

    def observed(model, points, requested, window, family, role, as_of):
        calls.append((len(points), len(requested) * 8))
        return native(model, points, requested, window, family, role, as_of)

    monkeypatch.setattr(worker, "score", observed)
    result = list(
        worker.iter_anomaly_census_scores(
            saved_model, iter(()), scopes, window, "seasonal_residual", "batch", as_of
        )
    )
    assert len(result) == 10400
    assert len({(*series_key(r.decision), r.decision.business_date) for r in result}) == 10400
    assert all(
        r.decision.status == "insufficient_data"
        and r.decision.score is None
        and r.decision.alert is None
        for r in result
    )
    assert len(calls) > 1 and all(p <= 8192 and n <= 10000 for p, n in calls)


@pytest.mark.parametrize("attack", ["duplicate", "order", "scope", "date", "null"])
def test_malformed_point_stream_fails_instead_of_qualifying_partial_census(saved_model, attack):
    start = date(2026, 8, 12)
    points = [point(start), point(start + timedelta(days=1))]
    if attack == "duplicate":
        points.append(points[-1])
    elif attack == "order":
        points.reverse()
    elif attack == "scope":
        points = [point(start, kind="return_completed")]
    elif attack == "date":
        points.append(point(start + timedelta(days=2)))
    else:
        points = [None, *points]
    with pytest.raises(ValueError, match="campaign_anomaly_scoring_point_"):
        list(
            worker.iter_anomaly_census_scores(
                saved_model,
                iter(points),
                (scope(),),
                Window(start=start, end=start + timedelta(days=1)),
                "seasonal_residual",
                "batch",
                datetime(2026, 8, 17, tzinfo=UTC),
            )
        )


def test_original_cutoff_and_validation_role_guards_are_not_bypassed(saved_model):
    day = date(2026, 8, 12)
    for role, as_of in (
        ("validation", datetime(2026, 8, 17, tzinfo=UTC)),
        ("batch", datetime(2026, 8, 13, tzinfo=UTC)),
    ):
        with pytest.raises(ValueError, match="scoring_cutoff"):
            list(
                worker.iter_anomaly_census_scores(
                    saved_model,
                    iter([point(day)]),
                    (scope(),),
                    Window(start=day, end=day),
                    "seasonal_residual",
                    role,
                    as_of,
                )
            )


@pytest.mark.parametrize("budget", [0, 6, 10001, True])
def test_native_batch_budget_rejects_before_reading_features(saved_model, budget):
    def unread():
        pytest.fail("invalid plan consumed public features")
        yield

    day = date(2026, 8, 12)
    with pytest.raises(ValueError, match="scoring_budget"):
        list(
            worker.iter_anomaly_census_scores(
                saved_model,
                unread(),
                (scope(),),
                Window(start=day, end=day),
                "seasonal_residual",
                "batch",
                datetime(2026, 8, 17, tzinfo=UTC),
                batch_points=budget,
            )
        )


def test_independent_native_replay_failure_cannot_emit_scores(saved_model, monkeypatch):
    def reject(*args):
        raise ValueError("controlled_independent_replay_failure")

    monkeypatch.setattr(worker, "verify_scores", reject)
    day = date(2026, 8, 12)
    iterator = worker.iter_anomaly_census_scores(
        saved_model,
        iter([point(day)]),
        (scope(),),
        Window(start=day, end=day),
        "seasonal_residual",
        "batch",
        datetime(2026, 8, 17, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="independent_replay_failure"):
        next(iterator)


def test_declared_row_budget_blocks_whole_census_without_trimming(saved_model):
    def unread():
        pytest.fail("insufficient budget consumed public features")
        yield

    day = date(2026, 8, 12)
    with pytest.raises(ValueError, match="scoring_budget"):
        list(
            worker.iter_anomaly_census_scores(
                saved_model,
                unread(),
                (scope(), scope(product=str(UUID(int=3)))),
                Window(start=day, end=day),
                "seasonal_residual",
                "batch",
                datetime(2026, 8, 17, tzinfo=UTC),
                max_requested_rows=1,
            )
        )


def test_stream_is_bounded_and_a_later_parent_error_is_not_hidden(saved_model):
    day = date(2026, 8, 12)
    read = []

    def parent():
        for kind in ("return_completed", "sale_completed"):
            read.append(kind)
            yield point(day, kind=kind)
        raise ValueError("controlled_later_parent_failure")

    scopes = (scope("return_completed"), scope())
    iterator = worker.iter_anomaly_census_scores(
        saved_model,
        parent(),
        scopes,
        Window(start=day, end=day),
        "seasonal_residual",
        "batch",
        datetime(2026, 8, 17, tzinfo=UTC),
        batch_points=7,
    )
    first = next(iterator)
    assert first.decision.event_type == "return_completed"
    assert read == ["return_completed", "sale_completed"]  # one lookahead, no whole-parent load
    with pytest.raises(ValueError, match="later_parent_failure"):
        next(iterator)
