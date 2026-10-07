"""Evaluate missing labels, repeated alerts and availability rather than output counts."""

import json
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from retailops_ai.anomaly_detectors.contract import Prediction
from retailops_ai.anomaly_detectors.protocol import Window
from retailops_ai.anomaly_evaluation.contract import EvaluationPolicy, Truth
from retailops_ai.anomaly_evaluation.evaluator import evaluate as evaluate_saved


def evaluate(*args, **kwargs):
    return evaluate_saved(*args, source_dataset_id=SOURCE_ID, **kwargs)


SCOPE = dict(
    event_type="sale_completed",
    product_id="00000000-0000-0000-0000-000000000001",
    selling_location_id="00000000-0000-0000-0000-000000000002",
    channel="store",
    currency="PLN",
)
WINDOW = Window(start=date(2026, 1, 1), end=date(2026, 1, 10))
AS_OF = datetime(2026, 2, 1, tzinfo=UTC)
SOURCE_ID = "source-sha256-" + "b" * 64


def prediction(
    day: int, alert: bool = False, *, status: str = "scored", score: float | None = None
) -> Prediction:
    scored = status == "scored"
    return Prediction.model_validate_json(
        json.dumps(
            {
                **SCOPE,
                "business_date": f"2026-01-{day:02d}",
                "scoring_origin": f"2026-01-{day + 2:02d}T00:00:00Z",
                "role": "test",
                "input_status": "ready_input" if scored else "no_declaration",
                "eligible": scored,
                "reason_codes": [] if scored else ["no_declaration"],
                "detector_id": "anomaly-detector-sha256-" + "a" * 64,
                "family": "seasonal_residual",
                "status": status,
                "score": (score if score is not None else 2.0 if alert else 0.0)
                if scored
                else None,
                "threshold": 1.0 if scored else None,
                "alert": alert if scored else None,
                "severity": ("high" if alert else "none") if scored else None,
                "explanation_codes": [],
                "observed_units": 10 if scored else None,
                "expected_units": 10 if scored else None,
                "residual_units": 0 if scored else None,
            }
        )
    )


def truth(
    *,
    episodes: list[dict] | None = None,
    start: int = 1,
    end: int = 10,
    available: str = "2026-01-20T00:00:00Z",
) -> Truth:
    if episodes is None:
        episodes = [
            {
                **SCOPE,
                "episode_id": "episode-1",
                "business_type": "one_day_spike",
                "window": {"start": "2026-01-04", "end": "2026-01-04"},
                "first_evidence_available_at": "2026-01-06T00:00:00Z",
                "label_available_at": "2026-01-20T00:00:00Z",
            }
        ]
    return Truth.model_validate_json(
        json.dumps(
            {
                "source_dataset_id": SOURCE_ID,
                "source_scenario_sha256": "c" * 64,
                "complete_windows": [
                    {
                        **SCOPE,
                        "window": {"start": f"2026-01-{start:02d}", "end": f"2026-01-{end:02d}"},
                        "available_at": available,
                    }
                ],
                "episodes": episodes,
            }
        )
    )


def run(
    alerts: tuple[int, ...] = (), *, labels: Truth | None = None, missing: tuple[int, ...] = ()
) -> dict:
    return evaluate(
        [
            prediction(
                day, day in alerts, status="insufficient_data" if day in missing else "scored"
            )
            for day in range(1, 11)
        ],
        labels or truth(),
        WINDOW,
        AS_OF,
    )["descriptor"]


def test_repeat_alerts_do_not_inflate_episode_recall_and_late_alert_is_observation_false_positive() -> (
    None
):
    report = run((4, 5))
    assert report["observation"]["precision"]["value"] == 0.5
    assert report["observation"]["false_alerts_per_1000"]["value"] == pytest.approx(1000 / 9)
    assert report["episode"]["recall"]["value"] == 1
    assert report["episode"]["n_detected"] == 1
    assert report["episode"]["repeat_alert_count"] == 1
    assert report["episode"]["delay_from_start_days"]["value"] == 2
    assert report["episode"]["delay_from_evidence_seconds"]["value"] == 0


@pytest.mark.parametrize("alerts, detected", [((3,), 0), ((5,), 1), ((6,), 0)])
def test_episode_zero_early_lead_and_one_day_late_tolerance(
    alerts: tuple[int, ...], detected: int
) -> None:
    assert run(alerts)["episode"]["n_detected"] == detected


def test_unknown_truth_and_insufficient_inputs_are_excluded_and_reported() -> None:
    report = run((1, 4), labels=truth(start=3, end=8), missing=(7,))
    assert report["coverage"]["unknown_truth"] == 4
    assert report["coverage"]["insufficient_data"] == 1
    assert report["coverage"]["evaluable"] == 5
    assert report["observation"]["false_positive"] == 0
    assert report["observation"]["clean_observations"] == 4
    assert report["coverage"]["insufficient_data_fraction"]["value"] == 0.1


def test_no_positives_and_no_predictions_are_not_perfect_metrics() -> None:
    report = run(labels=truth(episodes=[]))
    for metric in ("precision", "recall", "high_severity_precision", "average_precision"):
        assert report["observation"][metric]["status"] == "not_evaluable"
        assert report["observation"][metric]["value"] is None
    assert report["episode"]["recall"]["value"] is None


def test_unscored_positive_is_not_dropped_from_episode_recall() -> None:
    report = run(missing=(4, 5))
    assert report["observation"]["recall"]["value"] is None
    assert report["episode"]["recall"]["value"] == 0
    assert report["episode"]["n_evaluable"] == 1


def test_truth_availability_is_separate_from_input_availability() -> None:
    report = run((4,), labels=truth(available="2026-03-01T00:00:00Z"))
    assert report["coverage"]["immature_truth"] == 10
    assert report["episode"]["excluded"] == {"incomplete_truth": 1}
    assert report["episode"]["n_detected"] == 0


def test_average_precision_handles_score_ties_as_groups() -> None:
    rows = [prediction(day, True) for day in range(1, 11)]
    report = evaluate(rows, truth(), WINDOW, AS_OF)["descriptor"]
    assert report["observation"]["average_precision"]["value"] == pytest.approx(0.1)


def test_missing_or_duplicate_prediction_census_is_rejected() -> None:
    rows = [prediction(day) for day in range(1, 11)]
    with pytest.raises(ValueError, match="census_incomplete"):
        evaluate(rows[:-1], truth(), WINDOW, AS_OF)
    with pytest.raises(ValueError, match="duplicate_prediction"):
        evaluate([*rows, rows[0]], truth(), WINDOW, AS_OF)


def test_future_prediction_is_rejected() -> None:
    with pytest.raises(ValueError, match="window_or_cutoff"):
        evaluate(
            [prediction(day) for day in range(1, 11)],
            truth(),
            WINDOW,
            datetime(2026, 1, 11, tzinfo=UTC),
        )


def test_overlap_is_rejected_and_matching_tolerance_assigns_one_episode_per_alert() -> None:
    first = truth().episodes[0].model_dump(mode="json")
    second = {**first, "episode_id": "episode-2"}
    with pytest.raises(ValidationError, match="truth_overlap"):
        truth(episodes=[first, second])
    second["window"] = {"start": "2026-01-05", "end": "2026-01-05"}
    second["first_evidence_available_at"] = "2026-01-07T00:00:00Z"
    report = run((5,), labels=truth(episodes=[first, second]))
    assert report["episode"]["n_detected"] == 1
    assert report["episode"]["recall"]["value"] == 0.5


def test_identity_is_order_independent_and_policy_cannot_be_changed_silently() -> None:
    rows = [prediction(day, day == 4) for day in range(1, 11)]
    assert evaluate(rows, truth(), WINDOW, AS_OF) == evaluate(
        list(reversed(rows)), truth(), WINDOW, AS_OF
    )
    with pytest.raises(ValidationError):
        EvaluationPolicy(late_tolerance_days=2)
