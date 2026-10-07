"""Train-only native fitting with validation-only operational thresholds."""

import hashlib
from importlib.resources import files
from typing import Any

from retailops_ai.anomaly_detectors.codec import baseline_score, forest_scores
from retailops_ai.anomaly_detectors.contract import FitPolicy, Group
from retailops_ai.anomaly_detectors.engine import capacity_threshold
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_detectors.protocol import requested, series_key
from retailops_ai.anomaly_detectors.rows import NumericalRow
from retailops_ai.anomaly_portfolio.inputs import VerifiedFeatures
from retailops_ai.anomaly_portfolio.model import (
    CountRateDescriptor,
    Descriptor,
    EventCapacity,
    Model,
    MultiscaleDescriptor,
    count_rate_row,
    multiscale_row,
    row,
)
from retailops_ai.anomaly_portfolio.protocol import PortfolioProtocol
from retailops_ai.qualified_anomalies.contract import Point
from retailops_ai.source_snapshot.files import json_sha256
from retailops_ai.source_snapshot.protocol import resource_bytes


def train(
    features: VerifiedFeatures,
    protocol: PortfolioProtocol,
    policy: FitPolicy,
    *,
    multiscale: bool = False,
    event_capacities: tuple[EventCapacity, ...] | None = None,
) -> tuple[Model, list[dict[str, Any]]]:
    manifest = features.manifest
    points = list(features.points())
    pairs = requested(protocol, points)
    indexed: dict[tuple[object, ...], Point] = {
        (*series_key(p), p.business_date): p for p in points
    }

    def numerical(point: Point) -> NumericalRow | None:
        if event_capacities is not None:
            return count_rate_row(point, indexed)
        return multiscale_row(point, indexed) if multiscale else row(point)

    groups = []
    resources = []
    for event, currency in sorted({(s.event_type, s.currency) for s in protocol.scopes}):
        local_policy = policy
        if event_capacities is not None:
            capacity = next(c for c in event_capacities if c.event_type == event)
            local_policy = FitPolicy.model_validate(
                {
                    **policy.model_dump(),
                    "validation_alert_fraction": capacity.alert_fraction,
                    "validation_high_fraction": capacity.high_fraction,
                }
            )
        selected = [(m, p) for m, p in pairs if (m.event_type, m.currency) == (event, currency)]
        training = [
            r
            for m, p in selected
            if m.role == "train"
            and m.eligible
            and p is not None
            and (r := numerical(p)) is not None
        ]
        validation = [
            r
            for m, p in selected
            if m.role == "validation"
            and m.eligible
            and p is not None
            and (r := numerical(p)) is not None
        ]
        pipeline = None
        if len(training) >= policy.minimum_train_rows:
            pipeline, receipt = fit_pipeline(training, validation, policy)
            resources.append(receipt.model_dump(mode="json"))
        baseline = [baseline_score(r) for r in validation]
        forest = list(forest_scores(pipeline, validation)) if pipeline is not None else []
        groups.append(
            Group(
                event_type=event,
                currency=currency,
                training_rows=len(training),
                pipeline=pipeline,
                baseline_threshold=capacity_threshold(baseline, local_policy),
                forest_threshold=capacity_threshold(forest, local_policy),
            )
        )
    descriptor_type = (
        CountRateDescriptor
        if event_capacities is not None
        else (MultiscaleDescriptor if multiscale else Descriptor)
    )
    extra: dict[str, Any] = (
        {"event_capacities": event_capacities} if event_capacities is not None else {}
    )
    descriptor = descriptor_type(
        **extra,
        source_dataset_id=manifest.descriptor.coverage.descriptor.source_dataset_id,
        qualified_anomaly_input_id=manifest.qualified_anomaly_input_id,
        feature_manifest_sha256=features.manifest_sha256,
        feature_rows_sha256=manifest.descriptor.rows_sha256,
        train=protocol.train,
        validation=protocol.validation,
        training_cutoff=protocol.training_cutoff,
        selection_cutoff=protocol.selection_cutoff,
        policy=policy,
        groups=tuple(groups),
        training_membership_sha256=json_sha256(
            [m.model_dump(mode="json") for m, _ in pairs if m.role == "train"]
        ),
        validation_membership_sha256=json_sha256(
            [m.model_dump(mode="json") for m, _ in pairs if m.role == "validation"]
        ),
        code_sha256=json_sha256(
            {
                name + "/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for name in ("anomaly_portfolio", "anomaly_detectors", "qualified_anomalies")
                for p in files("retailops_ai." + name).iterdir()
                if p.name.endswith(".py")
            }
        ),
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
    )
    return Model(
        detector_id="anomaly-detector-sha256-" + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    ), resources
