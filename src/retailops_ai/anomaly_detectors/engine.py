"""Fit past inputs, freeze validation thresholds, then score development test windows."""

from collections import Counter
from decimal import Decimal
from typing import Literal

from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import (
    Diagnostic,
    Family,
    FitPolicy,
    Group,
    ModelDescriptor,
    ModelManifest,
    Prediction,
    Resources,
    Runtime,
    Threshold,
)
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_detectors.protocol import Membership, Protocol, requested
from retailops_ai.qualified_anomalies.contract import ModelRow, Point
from retailops_ai.qualified_anomalies.features import model_row
from retailops_ai.qualified_anomalies.store import Manifest as FeatureManifest
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256


def jsonl(rows: list[Membership] | list[Diagnostic] | list[Prediction]) -> bytes:
    return b"".join(canonical_json(row.model_dump(mode="json")) + b"\n" for row in rows)


def capacity_threshold(scores: list[float], policy: FitPolicy) -> Threshold | None:
    if len(scores) < policy.minimum_validation_rows:
        return None
    ordered = sorted(scores)
    allowed = int(Decimal(str(policy.validation_alert_fraction)) * len(scores))
    high = int(Decimal(str(policy.validation_high_fraction)) * len(scores))
    return Threshold(
        validation_rows=len(scores),
        validation_scores_sha256=json_sha256(scores),
        allowed_alerts=allowed,
        allowed_high_alerts=high,
        threshold=ordered[len(scores) - allowed - 1],
        high_threshold=ordered[len(scores) - high - 1],
    )


def eligible_rows(pairs: list[tuple[Membership, Point | None]]) -> list[ModelRow]:
    output = []
    for membership, point in pairs:
        if membership.eligible:
            row = model_row(point) if point is not None else None
            if row is None:
                raise SnapshotError("anomaly_eligible_input_without_model_row")
            output.append(row)
    return output


def run(
    feature_manifest: FeatureManifest,
    points: list[Point],
    protocol: Protocol,
    policy: FitPolicy,
    runtime: Runtime,
) -> tuple[ModelManifest, bytes, bytes, bytes, list[Resources], dict[str, int], dict[str, int]]:
    policy = FitPolicy.model_validate_json(policy.model_dump_json())
    protocol = Protocol.model_validate_json(protocol.model_dump_json())
    if protocol.feature_policy != feature_manifest.descriptor.policy:
        raise SnapshotError("anomaly_protocol_parent_policy_mismatch")
    memberships = requested(protocol, points)
    groups = []
    diagnostics = []
    resources = []
    for event, currency in sorted({(s.event_type, s.currency) for s in protocol.scopes}):
        selected = [
            (m, p) for m, p in memberships if m.event_type == event and m.currency == currency
        ]
        train = [(m, p) for m, p in selected if m.role == "train"]
        validation = [(m, p) for m, p in selected if m.role == "validation"]
        train_rows, validation_rows = eligible_rows(train), eligible_rows(validation)
        pipeline = None
        if len(train_rows) >= policy.minimum_train_rows:
            pipeline, receipt = fit_pipeline(train_rows, validation_rows, policy)
            resources.append(receipt)
        baseline = [baseline_score(row) for row in validation_rows]
        forest = list(forest_scores(pipeline, validation_rows)) if pipeline is not None else []
        groups.append(
            Group(
                event_type=event,
                currency=currency,
                training_rows=len(train_rows),
                pipeline=pipeline,
                baseline_threshold=capacity_threshold(baseline, policy),
                forest_threshold=capacity_threshold(forest, policy),
            )
        )
        index = 0
        for membership, _ in validation:
            families: tuple[tuple[Family, list[float]], ...] = (
                ("seasonal_residual", baseline),
                ("isolation_forest", forest),
            )
            for family, values in families:
                diagnostics.append(
                    Diagnostic(
                        **membership.model_dump(),
                        family=family,
                        score_status="input_ineligible"
                        if not membership.eligible
                        else "scored"
                        if values
                        else "insufficient_training",
                        score=values[index] if membership.eligible and values else None,
                    )
                )
            index += int(membership.eligible)
    descriptor = ModelDescriptor(
        qualified_anomaly_input_id=feature_manifest.qualified_anomaly_input_id,
        feature_descriptor_sha256=json_sha256(feature_manifest.descriptor.model_dump(mode="json")),
        protocol=protocol,
        policy=policy,
        runtime=runtime,
        training_membership_sha256=json_sha256(
            [m.model_dump(mode="json") for m, _ in memberships if m.role == "train"]
        ),
        validation_membership_sha256=json_sha256(
            [m.model_dump(mode="json") for m, _ in memberships if m.role == "validation"]
        ),
        groups=tuple(groups),
    )
    model = ModelManifest(
        detector_id="anomaly-detector-sha256-" + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    test_forest_scores = {}
    for group in groups:
        pairs = [
            (m, p)
            for m, p in memberships
            if m.role == "test"
            and m.event_type == group.event_type
            and m.currency == group.currency
            and m.eligible
        ]
        if group.pipeline is not None and group.forest_threshold is not None:
            scores = forest_scores(group.pipeline, eligible_rows(pairs))
            for (m, _), forest_score in zip(pairs, scores, strict=True):
                test_forest_scores[m.model_dump_json()] = forest_score
    predictions = []
    for membership, point in memberships:
        if membership.role != "test":
            continue
        group = next(
            g
            for g in groups
            if g.event_type == membership.event_type and g.currency == membership.currency
        )
        row = model_row(point) if membership.eligible and point is not None else None
        decisions: tuple[tuple[Family, Threshold | None], ...] = (
            ("seasonal_residual", group.baseline_threshold),
            ("isolation_forest", group.forest_threshold),
        )
        for family, threshold in decisions:
            reasons = list(membership.reason_codes)
            score: float | None = None
            if family == "isolation_forest" and group.pipeline is None:
                reasons.append("insufficient_training")
            elif threshold is None:
                reasons.append("insufficient_validation")
            elif row is not None:
                score = (
                    baseline_score(row)
                    if family == "seasonal_residual"
                    else (test_forest_scores[membership.model_dump_json()])
                )
            alert = (
                score > threshold.threshold if score is not None and threshold is not None else None
            )
            severity: Literal["none", "medium", "high"] | None = (
                None
                if alert is None
                else "none"
                if not alert
                else (
                    "high"
                    if threshold is not None
                    and score is not None
                    and score > threshold.high_threshold
                    else "medium"
                )
            )
            if alert and point is not None:
                reasons.append(
                    "positive_residual"
                    if (point.residual_units or 0) > 0
                    else "negative_residual"
                    if (point.residual_units or 0) < 0
                    else "zero_residual"
                )
                if point.context.on_hand == 0:
                    reasons.append("inventory_constraint_present")
                if point.context.promotion_offered:
                    reasons.append("planned_promotion_context")
            predictions.append(
                Prediction(
                    **membership.model_dump(),
                    detector_id=model.detector_id,
                    family=family,
                    status="scored" if score is not None else "insufficient_data",
                    score=score,
                    threshold=threshold.threshold
                    if score is not None and threshold is not None
                    else None,
                    alert=alert,
                    severity=severity,
                    explanation_codes=tuple(reasons),
                    observed_units=point.observation.observed_units if point is not None else None,
                    expected_units=point.expected_units if point is not None else None,
                    residual_units=point.residual_units if point is not None else None,
                )
            )
    return (
        model,
        jsonl([m for m, _ in memberships]),
        jsonl(diagnostics),
        jsonl(predictions),
        resources,
        dict(
            Counter(
                f"{m.role}/{m.input_status}/{'eligible' if m.eligible else 'excluded'}"
                for m, _ in memberships
            )
        ),
        dict(Counter(f"{p.family}/{p.status}" for p in predictions)),
    )
