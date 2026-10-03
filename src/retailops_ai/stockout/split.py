"""Chronological membership, availability purge and development-only label access."""

import hashlib
from collections import Counter
from typing import Any, Literal, Self

from pydantic import model_validator

from retailops_ai.data_contracts.common import Contract, UtcTime
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.contract import LabelPoint
from retailops_ai.stockout.feature_contract import FeaturePoint

ROLES = ("train", "tune", "calibration", "test")
DevelopmentRole = Literal["train", "tune", "calibration"]


class SplitPolicy(Contract):
    version: Literal["stockout-temporal-split-1.0.0"] = "stockout-temporal-split-1.0.0"
    start_at: UtcTime
    train_until: UtcTime
    tune_until: UtcTime
    calibration_until: UtcTime
    test_until: UtcTime
    evaluated_at: UtcTime
    purge: Literal["outcome_end_and_availability_strictly_before_next_role"] = (
        "outcome_end_and_availability_strictly_before_next_role"
    )
    final_test: Literal["membership_only_no_outcome_metrics_no_training_access"] = (
        "membership_only_no_outcome_metrics_no_training_access"
    )

    @model_validator(mode="after")
    def chronological(self) -> Self:
        clocks = (
            self.start_at,
            self.train_until,
            self.tune_until,
            self.calibration_until,
            self.test_until,
        )
        if (
            any(a >= b for a, b in zip(clocks, clocks[1:], strict=False))
            or self.evaluated_at < self.test_until
        ):
            raise ValueError("stockout_split_requires_ordered_utc_intervals")
        return self


def key(point: dict[str, Any]) -> tuple[str, str, str]:
    return point["product_id"], point["stock_location_id"], point["as_of"]


def build_split(
    features: dict[str, Any], labels: dict[str, Any], policy: SplitPolicy
) -> dict[str, Any]:
    """Called only with fully replayed datasets by the preparation adapter."""
    if (
        features["descriptor"]["role"] != "stockout_features"
        or labels["descriptor"]["role"] != "stockout_labels"
    ):
        raise ValueError("stockout_split_dataset_role_mismatch")
    if any(
        features["descriptor"][k] != labels["descriptor"][k]
        for k in ("source_dataset_id", "qualification_id")
    ):
        raise ValueError("stockout_split_parent_mismatch")
    indexed = {
        key(p): FeaturePoint.model_validate_json(canonical_json(p)) for p in features["points"]
    }
    if len(indexed) != len(features["points"]) or len({key(p) for p in labels["points"]}) != len(
        labels["points"]
    ):
        raise ValueError("stockout_split_duplicate_physical_origin")
    boundaries = (
        policy.start_at,
        policy.train_until,
        policy.tune_until,
        policy.calibration_until,
        policy.test_until,
    )
    membership = []
    classes: dict[str, Counter[int]] = {r: Counter() for r in ROLES[:-1]}
    for raw in sorted(labels["points"], key=key):
        point = LabelPoint.model_validate(raw)
        feature = indexed.get(key(raw))
        selected = next(
            (i for i in range(4) if boundaries[i] <= point.as_of < boundaries[i + 1]), None
        )
        role = ROLES[selected] if selected is not None else None
        reason = None
        if selected is None:
            reason = "outside_campaign"
        elif feature is None:
            reason = "feature_missing"
        elif feature.status != "eligible":
            reason = "feature_" + (feature.reason or feature.status)
        elif point.status != "evaluable":
            reason = "label_" + (point.reason or point.status)
        elif selected < 3 and (
            point.window_end_at >= boundaries[selected + 1]
            or point.label_available_at is None
            or point.label_available_at >= boundaries[selected + 1]
        ):
            reason = "purged_boundary_or_delayed_label"
        elif point.label_available_at is None or point.label_available_at > policy.evaluated_at:
            reason = "label_not_available_at_campaign_cutoff"
        eligible = reason is None
        membership.append(
            dict(
                product_id=point.product_id,
                stock_location_id=point.stock_location_id,
                as_of=raw["as_of"],
                role=role,
                eligible=eligible,
                reason=reason,
                label_available_at=raw["label_available_at"],
                window_end_at=raw["window_end_at"],
            )
        )
        if eligible and role in classes and point.incident_stockout is not None:
            classes[role][point.incident_stockout] += 1
    counts = {r: sum(m["eligible"] and m["role"] == r for m in membership) for r in ROLES}
    ready = all(counts[r] > 0 for r in ROLES) and all(len(classes[r]) == 2 for r in ROLES[:-1])
    descriptor = dict(
        schema_version="1.0.0",
        role="stockout_split",
        feature_dataset_id=features["feature_dataset_id"],
        label_dataset_id=labels["label_dataset_id"],
        source_dataset_id=features["descriptor"]["source_dataset_id"],
        policy=policy.model_dump(mode="json"),
        membership_sha256=hashlib.sha256(canonical_json(membership)).hexdigest(),
    )
    return dict(
        split_id="split-sha256-" + hashlib.sha256(canonical_json(descriptor)).hexdigest(),
        descriptor=descriptor,
        membership=membership,
        report=dict(
            eligible_by_role=counts,
            reasons=dict(sorted(Counter(m["reason"] for m in membership if m["reason"]).items())),
            development_classes={
                r: {str(k): v for k, v in sorted(c.items())} for r, c in classes.items()
            },
            temporal_membership_ready=ready,
            final_test_outcomes_evaluated=False,
            upstream_forecast_ready=features["report"]["upstream_forecast_ready"],
            model_ready=False,
        ),
    )


def development_labels(
    split: dict[str, Any],
    features: dict[str, Any],
    labels: dict[str, Any],
    *,
    role: DevelopmentRole,
) -> list[LabelPoint]:
    if role not in ROLES[:-1]:
        raise ValueError("stockout_final_test_requires_separate_approved_campaign")
    if split["descriptor"]["label_dataset_id"] != labels["label_dataset_id"]:
        raise ValueError("stockout_development_label_pin_mismatch")
    if split != build_split(
        features, labels, SplitPolicy.model_validate(split["descriptor"]["policy"])
    ):
        raise ValueError("stockout_development_split_replay_mismatch")
    selected = {key(m) for m in split["membership"] if m["eligible"] and m["role"] == role}
    return [LabelPoint.model_validate(p) for p in labels["points"] if key(p) in selected]
