"""Evaluate saved decisions without fitting, changing thresholds or importing ML libraries."""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any, Literal

from retailops_ai.anomaly_detectors.contract import Prediction
from retailops_ai.anomaly_detectors.protocol import Window, series_key
from retailops_ai.anomaly_evaluation.contract import (
    Decision,
    Episode,
    EvaluationPolicy,
    Metric,
    Truth,
    dates,
)
from retailops_ai.source_snapshot.files import json_sha256


def point_key(prediction: Prediction | Decision) -> tuple[str, ...]:
    return (*series_key(prediction), prediction.business_date.isoformat())


def ratio(
    numerator: int,
    denominator: int,
    total: int,
    evaluable: int,
    *,
    unit: str = "ratio",
    multiplier: int = 1,
    reason: str,
) -> dict[str, Any]:
    return Metric.model_validate(
        {
            "value": multiplier * numerator / denominator if denominator else None,
            "unit": unit,
            "status": "evaluable" if denominator else "not_evaluable",
            "n_total": total,
            "n_evaluable": evaluable,
            "coverage": evaluable / total if total else 0,
            "numerator": numerator,
            "denominator": denominator,
            "reason": None if denominator else reason,
        }
    ).model_dump(mode="json")


def average_precision(
    rows: list[tuple[Prediction | Decision, Episode | None]], total: int
) -> dict[str, Any]:
    positives = sum(e is not None for _, e in rows)
    if not positives or positives == len(rows):
        return Metric(
            value=None,
            unit="ratio",
            status="not_evaluable",
            n_total=total,
            n_evaluable=len(rows),
            coverage=len(rows) / total if total else 0,
            reason="both_truth_classes_required",
        ).model_dump(mode="json")
    groups: dict[float, list[bool]] = defaultdict(list)
    for prediction, episode in rows:
        if prediction.score is None:
            raise ValueError("anomaly_evaluable_score_missing")
        groups[prediction.score].append(episode is not None)
    seen = true = 0
    ap = 0.0
    for score in sorted(groups, reverse=True):
        labels = groups[score]
        increment = sum(labels)
        seen += len(labels)
        true += increment
        ap += increment / positives * true / seen
    return Metric(
        value=ap,
        unit="ratio",
        status="evaluable",
        n_total=total,
        n_evaluable=len(rows),
        coverage=len(rows) / total,
        reason=None,
    ).model_dump(mode="json")


def observation(
    rows: list[tuple[Prediction | Decision, Episode | None]], total: int
) -> dict[str, Any]:
    positives = sum(e is not None for _, e in rows)
    predicted = sum(p.alert is True for p, _ in rows)
    tp = sum(p.alert is True and e is not None for p, e in rows)
    high = [(p, e) for p, e in rows if p.severity == "high"]
    clean = len(rows) - positives
    return {
        "n_requested": total,
        "n_evaluable": len(rows),
        "positive_observations": positives,
        "clean_observations": clean,
        "predicted_positive": predicted,
        "true_positive": tp,
        "false_positive": predicted - tp,
        "false_negative": positives - tp,
        "true_negative": clean - (predicted - tp),
        "precision": ratio(tp, predicted, total, len(rows), reason="no_positive_predictions"),
        "recall": ratio(tp, positives, total, len(rows), reason="no_truth_positives"),
        "false_alerts_per_1000": ratio(
            predicted - tp,
            clean,
            total,
            len(rows),
            unit="alerts_per_1000_clean_observations",
            multiplier=1000,
            reason="no_evaluable_clean_observations",
        ),
        "high_severity_precision": ratio(
            sum(e is not None for _, e in high),
            len(high),
            total,
            len(rows),
            reason="no_high_severity_predictions",
        ),
        "average_precision": average_precision(rows, total),
    }


def evaluate(
    predictions: Sequence[Prediction | Decision],
    truth: Truth,
    window: Window,
    as_of: datetime,
    policy: EvaluationPolicy | None = None,
    *,
    source_dataset_id: str,
) -> dict[str, Any]:
    policy = EvaluationPolicy.model_validate_json((policy or EvaluationPolicy()).model_dump_json())
    truth = Truth.model_validate_json(truth.model_dump_json())
    if truth.source_dataset_id != source_dataset_id:
        raise ValueError("anomaly_evaluation_truth_source_mismatch")
    if as_of.utcoffset() != timedelta(0):
        raise ValueError("anomaly_evaluation_utc_required")
    safe: list[Prediction | Decision] = [
        type(p).model_validate_json(p.model_dump_json()) for p in predictions
    ]
    if not safe or len(safe) > 1000000:
        raise ValueError("anomaly_evaluation_prediction_budget")
    if len({(p.family, point_key(p)) for p in safe}) != len(safe):
        raise ValueError("anomaly_evaluation_duplicate_prediction")
    if len({p.detector_id for p in safe}) != 1 or len({p.family for p in safe}) != 1:
        raise ValueError("anomaly_evaluation_requires_one_model_and_family")
    if any(
        not window.start <= p.business_date <= window.end or p.scoring_origin > as_of for p in safe
    ):
        raise ValueError("anomaly_evaluation_window_or_cutoff")
    scopes = {series_key(p) for p in safe}
    if len(scopes) * len(dates(window)) != len(safe):
        raise ValueError("anomaly_evaluation_requested_census_incomplete")
    required = {(*scope, day.isoformat()) for scope in scopes for day in dates(window)}
    if {point_key(p) for p in safe} != required:
        raise ValueError("anomaly_evaluation_requested_census_incomplete")
    # Completeness is declared by the independent truth producer. No episode
    # membership or absence of detector alerts can establish a clean observation.
    census = {
        (*series_key(w), day.isoformat()): w.available_at
        for w in truth.complete_windows
        for day in dates(w.window)
    }
    indexed = {
        (*series_key(e), day.isoformat()): e for e in truth.episodes for day in dates(e.window)
    }
    unknown = immature = insufficient = 0
    joined: list[tuple[Prediction | Decision, Episode | None]] = []
    for prediction in safe:
        key = point_key(prediction)
        episode = indexed.get(key)
        if key not in census:
            unknown += 1
        elif census[key] > as_of or (episode and episode.label_available_at > as_of):
            immature += 1
        elif prediction.status != "scored":
            insufficient += 1
        else:
            joined.append((prediction, episode))
    report: dict[str, Any] = {
        "source_dataset_id": source_dataset_id,
        "policy": policy.model_dump(mode="json"),
        "truth_sha256": json_sha256(truth.model_dump(mode="json")),
        "predictions_sha256": json_sha256(
            [p.model_dump(mode="json") for p in sorted(safe, key=point_key)]
        ),
        "window": window.model_dump(mode="json"),
        "as_of": as_of.isoformat(),
        "detector_id": safe[0].detector_id,
        "family": safe[0].family,
        "coverage": {
            "requested": len(safe),
            "unknown_truth": unknown,
            "immature_truth": immature,
            "insufficient_data": insufficient,
            "evaluable": len(joined),
            "input_insufficient_data_total": sum(p.status != "scored" for p in safe),
            "insufficient_data_fraction": ratio(
                sum(p.status != "scored" for p in safe),
                len(safe),
                len(safe),
                len(safe),
                reason="empty_census",
            ),
        },
        "observation": observation(joined, len(safe)),
        "per_segment": {},
        "per_type": {},
    }
    for event, currency in sorted({(p.event_type, p.currency) for p in safe}):
        selected = [(p, e) for p, e in joined if (p.event_type, p.currency) == (event, currency)]
        report["per_segment"][f"{event}/{currency}"] = observation(
            selected, sum((p.event_type, p.currency) == (event, currency) for p in safe)
        )
    # Type-specific metrics use the same clean controls, excluding other types'
    # positives so their detections do not become false positives of this type.
    for kind in sorted({e.business_type for e in truth.episodes}):
        events = {e.event_type for e in truth.episodes if e.business_type == kind}
        selected = [
            (p, e)
            for p, e in joined
            if p.event_type in events and (e is None or e.business_type == kind)
        ]
        report["per_type"][kind] = observation(selected, sum(p.event_type in events for p in safe))
    eligible_episodes = []
    excluded_episodes: Counter[str] = Counter()
    for e in truth.episodes:
        if (
            series_key(e) not in scopes
            or e.window.end < window.start
            or e.window.start > window.end
        ):
            continue
        if e.window.start < window.start or e.window.end > window.end:
            excluded_episodes["boundary_censored"] += 1
        elif e.label_available_at > as_of:
            excluded_episodes["immature"] += 1
        elif any(
            census.get((*series_key(e), d.isoformat()), as_of + timedelta(seconds=1)) > as_of
            for d in dates(e.window)
        ):
            excluded_episodes["incomplete_truth"] += 1
        else:
            eligible_episodes.append(e)
    matches: dict[str, list[Prediction | Decision]] = defaultdict(list)
    unmatched = 0
    for p, _ in sorted(
        joined, key=lambda pair: (pair[0].business_date, pair[0].scoring_origin, point_key(pair[0]))
    ):
        if p.alert is not True:
            continue
        candidates = [
            e
            for e in eligible_episodes
            if series_key(e) == series_key(p)
            and e.window.start
            <= p.business_date
            <= e.window.end + timedelta(days=policy.late_tolerance_days)
            and p.scoring_origin >= e.first_evidence_available_at
        ]
        if candidates:
            matched = min(candidates, key=lambda e: (e.window.end, e.episode_id))
            matches[matched.episode_id].append(p)
        else:
            unmatched += 1
    details: list[dict[str, Any]] = []
    for e in sorted(eligible_episodes, key=lambda e: e.episode_id):
        alerts = matches.get(e.episode_id, [])
        first = alerts[0] if alerts else None
        details.append(
            {
                "episode_id": e.episode_id,
                "business_type": e.business_type,
                "detected": bool(alerts),
                "alert_count": len(alerts),
                "repeat_alert_count": max(0, len(alerts) - 1),
                "first_alert_date": first.business_date.isoformat() if first else None,
                "delay_from_start_days": (
                    first.scoring_origin
                    - datetime.combine(e.window.start, datetime.min.time(), UTC)
                ).total_seconds()
                / 86400
                if first
                else None,
                "delay_from_evidence_seconds": (
                    first.scoring_origin - e.first_evidence_available_at
                ).total_seconds()
                if first
                else None,
            }
        )
    episode_report: dict[str, Any] = {
        "n_evaluable": len(details),
        "n_detected": len(matches),
        "excluded": dict(excluded_episodes),
        "repeat_alert_count": sum(d["repeat_alert_count"] for d in details),
        "unmatched_alert_count": unmatched,
        "details": details,
        "recall": ratio(
            sum(d["detected"] for d in details),
            len(details),
            len(details),
            len(details),
            reason="no_mature_episodes",
        ),
        "per_type": {},
    }
    for kind in sorted({e.business_type for e in truth.episodes}):
        part = [d for d in details if d["business_type"] == kind]
        episode_report["per_type"][kind] = ratio(
            sum(d["detected"] for d in part),
            len(part),
            len(part),
            len(part),
            reason="no_mature_episodes_of_type",
        )
    delays: tuple[tuple[str, Literal["days", "seconds"]], ...] = (
        ("delay_from_start_days", "days"),
        ("delay_from_evidence_seconds", "seconds"),
    )
    for name, unit in delays:
        values = [float(d[name]) for d in details if d[name] is not None]
        episode_report[name] = Metric(
            value=mean(values) if values else None,
            unit=unit,
            status="evaluable" if values else "not_evaluable",
            n_total=len(details),
            n_evaluable=len(values),
            coverage=len(values) / len(details) if details else 0,
            reason=None if values else "no_detected_episodes",
        ).model_dump(mode="json")
    report["episode"] = episode_report
    descriptor = {"version": policy.version, **report}
    return {
        "evaluation_id": "anomaly-evaluation-sha256-" + json_sha256(descriptor),
        "descriptor": descriptor,
    }
