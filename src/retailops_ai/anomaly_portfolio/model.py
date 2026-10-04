"""Data-only loader and scoring; no native ML, generator or truth imports."""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import DetectorID, Family, FitPolicy, Group
from retailops_ai.anomaly_detectors.protocol import Scope, Window, scoring_origin, series_key
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.data_contracts.common import Contract, Sha256, SourceID, UtcTime
from retailops_ai.qualified_anomalies.contract import ModelRow, Point, Policy
from retailops_ai.source_snapshot.files import canonical_json, json_sha256, read_bytes


class Descriptor(Contract):
    version: Literal["anomaly-portfolio-model-1.0.0"] = "anomaly-portfolio-model-1.0.0"
    source_dataset_id: SourceID
    qualified_anomaly_input_id: str = Field(
        pattern=r"^qualified-anomaly-inputs-sha256-[0-9a-f]{64}$"
    )
    feature_manifest_sha256: Sha256
    feature_rows_sha256: Sha256
    train: Window
    validation: Window
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    policy: FitPolicy
    groups: tuple[Group, ...] = Field(min_length=1, max_length=4)
    training_membership_sha256: Sha256
    validation_membership_sha256: Sha256
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    truth_access: Literal["excluded_no_oracle_training"] = "excluded_no_oracle_training"

    @model_validator(mode="after")
    def temporal(self) -> Self:
        if (
            not self.train.end < self.validation.start
            or not self.training_cutoff < self.selection_cutoff
            or len({(g.event_type, g.currency) for g in self.groups}) != len(self.groups)
        ):
            raise ValueError("anomaly_portfolio_model_temporal_or_group_binding")
        for group in self.groups:
            if group.pipeline is not None and (
                group.pipeline.training_rows != group.training_rows
                or tuple(f.name for f in group.pipeline.fills) != self.policy.features
                or len(group.pipeline.forest.trees) != self.policy.n_estimators
            ):
                raise ValueError("anomaly_portfolio_pipeline_policy_binding")
        return self


class Model(Contract):
    detector_id: DetectorID
    descriptor: Descriptor

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.detector_id != "anomaly-detector-sha256-" + json_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("anomaly_portfolio_model_identity")
        return self


def load(path: Path, expected_sha256: str) -> Model:
    raw = read_bytes(path.parent, path.name, 8 * 1024**2)
    model = Model.model_validate_json(raw)
    if (
        hashlib.sha256(raw).hexdigest() != expected_sha256
        or raw != canonical_json(model.model_dump(mode="json")) + b"\n"
    ):
        raise ValueError("anomaly_portfolio_model_pin_or_encoding")
    return model


def row(point: Point) -> ModelRow | None:
    if point.status != "ready_input":
        return None
    return ModelRow.model_validate(
        {
            "observed_units": point.observation.observed_units,
            "expected_units": point.expected_units,
            "residual_units": point.residual_units,
            "robust_scale_units": point.robust_scale_units,
            "standardized_residual": point.standardized_residual,
            "planned_price": float(point.context.planned_price)
            if point.context.planned_price is not None
            else None,
            "promotion_offered": point.context.promotion_offered,
            "on_hand": point.context.on_hand,
        }
    )


def score(
    model: Model,
    points: list[Point],
    scopes: tuple[Scope, ...],
    window: Window,
    family: Family,
    role: Literal["validation", "final_test", "batch"],
    as_of: datetime,
) -> list[Decision]:
    model = Model.model_validate_json(model.model_dump_json())
    if as_of.utcoffset() != timedelta(0) or len(points) > 10000:
        raise ValueError("anomaly_portfolio_utc_or_input_budget")
    safe = [
        Point.model_validate_json(p.model_dump_json())
        for p in points
        if window.start <= p.business_date <= window.end
    ]
    indexed = {(*series_key(p), p.business_date): p for p in safe}
    if len(indexed) != len(safe):
        raise ValueError("anomaly_portfolio_duplicate_feature")
    if (
        len(scopes) * ((window.end - window.start).days + 1) > 10000
        or window.start > window.end
        or len({series_key(s) for s in scopes}) != len(scopes)
    ):
        raise ValueError("anomaly_portfolio_scoring_census_budget")
    requested = [
        (s, window.start + timedelta(days=offset))
        for s in sorted(scopes, key=series_key)
        for offset in range((window.end - window.start).days + 1)
    ]
    pending = []
    for scope, day in requested:
        point = indexed.get((*series_key(scope), day))
        # Public points bind the fixed 24h/72h profile clock. Unknown slots have
        # the same declared clock rather than using generation or current time.
        origin = scoring_origin(day, scope.event_type, Policy())
        if point is not None and point.scoring_origin != origin:
            raise ValueError("anomaly_portfolio_feature_scoring_clock")
        if (
            origin > as_of
            or origin <= model.descriptor.training_cutoff
            or (role != "validation" and origin <= model.descriptor.selection_cutoff)
            or (
                role == "validation"
                and not model.descriptor.validation.start <= day <= model.descriptor.validation.end
            )
        ):
            raise ValueError("anomaly_portfolio_scoring_cutoff")
        group = next(
            (
                g
                for g in model.descriptor.groups
                if (g.event_type, g.currency) == (scope.event_type, scope.currency)
            ),
            None,
        )
        threshold = (
            None
            if group is None
            else group.baseline_threshold
            if family == "seasonal_residual"
            else group.forest_threshold
        )
        model_row = row(point) if point is not None else None
        ready = (
            model_row is not None
            and threshold is not None
            and (
                family == "seasonal_residual" or (group is not None and group.pipeline is not None)
            )
        )
        pending.append((scope, day, point, origin, group, threshold, model_row, ready))
    values: dict[tuple[str, ...], float] = {}
    for group in model.descriptor.groups:
        selected = [
            p
            for p in pending
            if p[7] and (p[0].event_type, p[0].currency) == (group.event_type, group.currency)
        ]
        rows = [p[6] for p in selected if p[6] is not None]
        scores = (
            tuple(baseline_score(r) for r in rows)
            if family == "seasonal_residual"
            else forest_scores(group.pipeline, rows)
            if group.pipeline is not None
            else ()
        )
        for item, computed_score in zip(selected, scores, strict=True):
            values[(*series_key(item[0]), item[1].isoformat())] = computed_score
    decisions = []
    for scope, day, point, origin, _group, threshold, _model_row, ready in pending:
        value = values.get((*series_key(scope), day.isoformat()))
        alert = value > threshold.threshold if value is not None and threshold is not None else None
        reasons = list(point.reason_codes) if point else ["no_declaration"]
        if not ready and point is not None and point.status == "ready_input":
            reasons.append("insufficient_training_or_validation")
        if alert and point:
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
        decisions.append(
            Decision(
                **scope.model_dump(),
                business_date=day,
                scoring_origin=origin,
                detector_id=model.detector_id,
                family=family,
                role=role,
                status="scored" if value is not None else "insufficient_data",
                score=value,
                threshold=threshold.threshold if value is not None and threshold else None,
                alert=alert,
                severity=None
                if alert is None
                else "none"
                if not alert
                else "high"
                if threshold is not None and value is not None and value > threshold.high_threshold
                else "medium",
                explanation_codes=tuple(reasons),
                input_status=point.status if point else "no_declaration",
                observed_units=point.observation.observed_units if point else None,
                expected_units=point.expected_units if point else None,
                residual_units=point.residual_units if point else None,
                promotion_offered=point.context.promotion_offered if point else None,
                on_hand=point.context.on_hand if point else None,
            )
        )
    return decisions
