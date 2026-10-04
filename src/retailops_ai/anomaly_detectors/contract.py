"""Bounded data-only trees and frozen unsupervised validation-capacity thresholds."""

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.protocol import Currency, EventType, Membership, Protocol
from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.qualified_anomalies.contract import MODEL_FEATURES

VERSION = "anomaly-detectors-1.0.0"
MAX_MODEL_BYTES = 8 * 1024**2
Feature = Literal[
    "observed_units",
    "expected_units",
    "residual_units",
    "robust_scale_units",
    "standardized_residual",
    "planned_price",
    "promotion_offered",
    "on_hand",
]
Family = Literal["seasonal_residual", "isolation_forest"]
Finite = Annotated[float, Field(allow_inf_nan=False)]
DetectorID = Annotated[str, Field(pattern=r"^anomaly-detector-sha256-[0-9a-f]{64}$")]


class FitPolicy(Contract):
    version: Literal["anomaly-detector-fit-1.0.0"] = "anomaly-detector-fit-1.0.0"
    features: tuple[Feature, ...] = MODEL_FEATURES
    n_estimators: Annotated[int, Field(ge=8, le=128)] = 32
    max_samples: Annotated[int, Field(ge=16, le=256)] = 64
    max_features: Annotated[float, Field(gt=0, le=1)] = 1.0
    contamination: Literal["auto"] | Annotated[float, Field(gt=0, le=0.5)] = "auto"
    model_seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 137
    minimum_train_rows: Annotated[int, Field(ge=16, le=10000)] = 16
    minimum_validation_rows: Annotated[int, Field(ge=16, le=10000)] = 16
    validation_alert_fraction: Annotated[float, Field(gt=0, le=0.2)] = 0.05
    validation_high_fraction: Annotated[float, Field(gt=0, le=0.2)] = 0.01
    selection: Literal["unlabelled_validation_alert_capacity"] = (
        "unlabelled_validation_alert_capacity"
    )
    score_arithmetic: Literal["decimal50_half_even_12_places"] = "decimal50_half_even_12_places"
    fit_wall_seconds: Literal[60] = 60
    fit_cpu_seconds: Literal[60] = 60
    fit_rss_bytes: Literal[536870912] = 536870912
    max_train_rows: Literal[10000] = 10000
    max_matrix_bytes: Literal[4194304] = 4194304

    @model_validator(mode="after")
    def allowlist(self) -> Self:
        if (
            self.features != tuple(f for f in MODEL_FEATURES if f in self.features)
            or not self.features
            or self.validation_high_fraction > self.validation_alert_fraction
        ):
            raise ValueError("anomaly_fit_feature_allowlist_or_capacity")
        return self


class Fill(Contract):
    name: Feature
    value: Finite
    known_count: Annotated[int, Field(ge=0, le=10000)]
    reason: Literal["train_median", "entirely_missing_constant_zero"]

    @model_validator(mode="after")
    def missing(self) -> Self:
        if (self.known_count == 0) != (self.reason == "entirely_missing_constant_zero") or (
            self.known_count == 0 and self.value != 0
        ):
            raise ValueError("anomaly_preprocessing_missing_fill")
        return self


class Node(Contract):
    sample_count: Annotated[int, Field(ge=1, le=256)]
    feature: Annotated[int, Field(ge=0, le=15)] | None
    threshold: Finite | None
    left: Annotated[int, Field(ge=0, le=510)] | None
    right: Annotated[int, Field(ge=0, le=510)] | None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.feature is None) != all(
            v is None for v in (self.threshold, self.left, self.right)
        ):
            raise ValueError("anomaly_tree_node_shape")
        if self.feature is not None and any(
            v is None for v in (self.threshold, self.left, self.right)
        ):
            raise ValueError("anomaly_tree_node_shape")
        return self


class Tree(Contract):
    nodes: tuple[Node, ...] = Field(min_length=1, max_length=511)

    @model_validator(mode="after")
    def graph(self) -> Self:
        incoming = [0] * len(self.nodes)
        for index, node in enumerate(self.nodes):
            if node.feature is not None:
                if node.left is None or node.right is None:
                    raise ValueError("anomaly_tree_node_shape")
                if not index < node.left < len(self.nodes) or not index < node.right < len(
                    self.nodes
                ):
                    raise ValueError("anomaly_tree_cycle_or_bounds")
                if node.left == node.right or node.sample_count != (
                    self.nodes[node.left].sample_count + self.nodes[node.right].sample_count
                ):
                    raise ValueError("anomaly_tree_partition")
                incoming[node.left] += 1
                incoming[node.right] += 1
        if incoming != [0] + [1] * (len(incoming) - 1):
            raise ValueError("anomaly_tree_disconnected_or_shared_node")
        return self


class Forest(Contract):
    sklearn_version: Literal["1.9.1"] = "1.9.1"
    input_dtype: Literal["float32"] = "float32"
    feature_count: Annotated[int, Field(ge=2, le=16)]
    max_samples: Annotated[int, Field(ge=16, le=256)]
    native_offset: Annotated[float, Field(ge=-1, le=0)]
    trees: tuple[Tree, ...] = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        for tree in self.trees:
            depths = [0] * len(tree.nodes)
            if (
                tree.nodes[0].sample_count != self.max_samples
                or len(tree.nodes) > 2 * self.max_samples - 1
            ):
                raise ValueError("anomaly_forest_sample_binding")
            for index, node in enumerate(tree.nodes):
                if node.feature is not None:
                    if node.left is None or node.right is None:
                        raise ValueError("anomaly_tree_node_shape")
                    if node.feature >= self.feature_count:
                        raise ValueError("anomaly_tree_feature_outside_matrix")
                    depths[node.left] = depths[index] + 1
                    depths[node.right] = depths[index] + 1
            if max(depths) > math.ceil(math.log2(self.max_samples)):
                raise ValueError("anomaly_tree_depth_budget")
        return self


class Pipeline(Contract):
    training_rows: Annotated[int, Field(ge=16, le=10000)]
    training_rows_sha256: Sha256
    fills: tuple[Fill, ...] = Field(min_length=1, max_length=8)
    forest: Forest

    @model_validator(mode="after")
    def fitted(self) -> Self:
        names = tuple(f.name for f in self.fills)
        if (
            names != tuple(f for f in MODEL_FEATURES if f in names)
            or len(names) * 2 != self.forest.feature_count
            or self.forest.max_samples > self.training_rows
            or any(f.known_count > self.training_rows for f in self.fills)
        ):
            raise ValueError("anomaly_pipeline_preprocessing_binding")
        return self


class Threshold(Contract):
    validation_rows: Annotated[int, Field(ge=16, le=10000)]
    validation_scores_sha256: Sha256
    allowed_alerts: Annotated[int, Field(ge=0, le=2000)]
    allowed_high_alerts: Annotated[int, Field(ge=0, le=2000)]
    threshold: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    high_threshold: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    comparison: Literal["strictly_greater_ties_do_not_alert"] = "strictly_greater_ties_do_not_alert"

    @model_validator(mode="after")
    def capacity(self) -> Self:
        if self.high_threshold < self.threshold or not (
            self.allowed_high_alerts <= self.allowed_alerts <= self.validation_rows
        ):
            raise ValueError("anomaly_threshold_order_or_capacity")
        return self


class Group(Contract):
    event_type: EventType
    currency: Currency
    training_rows: Annotated[int, Field(ge=0, le=10000)]
    pipeline: Pipeline | None
    baseline_threshold: Threshold | None
    forest_threshold: Threshold | None


class Diagnostic(Membership):
    family: Family
    score_status: Literal["scored", "input_ineligible", "insufficient_training"]
    score: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    purpose: Literal["validation_threshold_selection_no_operational_alert"] = (
        "validation_threshold_selection_no_operational_alert"
    )

    @model_validator(mode="after")
    def validation_only(self) -> Self:
        if (
            self.role != "validation"
            or (self.score_status == "scored") != (self.score is not None)
            or (self.score_status == "input_ineligible") != (not self.eligible)
            or (self.score_status == "insufficient_training" and self.family != "isolation_forest")
        ):
            raise ValueError("anomaly_validation_score_status")
        return self


class Prediction(Membership):
    detector_id: DetectorID
    family: Family
    status: Literal["scored", "insufficient_data"]
    score: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    threshold: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    alert: bool | None
    severity: Literal["none", "medium", "high"] | None
    explanation_codes: tuple[str, ...]
    observed_units: Annotated[int, Field(ge=0)] | None
    expected_units: Annotated[int, Field(ge=0)] | None
    residual_units: int | None
    detector_readiness: Literal["not_qualified"] = "not_qualified"

    @model_validator(mode="after")
    def decision(self) -> Self:
        if self.role != "test":
            raise ValueError("anomaly_prediction_not_development_test")
        if self.status == "scored":
            if (
                not self.eligible
                or self.score is None
                or self.threshold is None
                or self.alert != (self.score > self.threshold)
                or self.severity is None
                or (self.severity == "none") != (self.alert is False)
                or any(
                    v is None
                    for v in (self.observed_units, self.expected_units, self.residual_units)
                )
            ):
                raise ValueError("anomaly_prediction_score_decision")
        elif any(v is not None for v in (self.score, self.threshold, self.alert, self.severity)):
            raise ValueError("anomaly_unscoreable_has_decision")
        return self


class Resources(Contract):
    wall_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cpu_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    peak_rss_bytes: Annotated[int, Field(ge=0)]
    native_max_score_error: Annotated[float, Field(ge=0, le=1e-12)]


class Runtime(Contract):
    code_files: dict[str, Sha256]
    contract_files: dict[str, Sha256]
    dependency_lock_sha256: Sha256
    python_version: str


class ModelDescriptor(Contract):
    version: Literal["anomaly-detectors-1.0.0"] = "anomaly-detectors-1.0.0"
    qualified_anomaly_input_id: Annotated[
        str, Field(pattern=r"^qualified-anomaly-inputs-sha256-[0-9a-f]{64}$")
    ]
    feature_descriptor_sha256: Sha256
    protocol: Protocol
    policy: FitPolicy
    runtime: Runtime
    training_membership_sha256: Sha256
    validation_membership_sha256: Sha256
    groups: tuple[Group, ...] = Field(min_length=1, max_length=4)
    truth_access: Literal["excluded"] = "excluded"
    detector_readiness: Literal["not_qualified"] = "not_qualified"

    @model_validator(mode="after")
    def group_bindings(self) -> Self:
        keys = [(g.event_type, g.currency) for g in self.groups]
        if keys != sorted({(s.event_type, s.currency) for s in self.protocol.scopes}):
            raise ValueError("anomaly_model_group_inventory")
        for group in self.groups:
            if (group.pipeline is None) != (group.training_rows < self.policy.minimum_train_rows):
                raise ValueError("anomaly_model_training_readiness")
            if group.pipeline is not None and (
                group.pipeline.training_rows != group.training_rows
                or tuple(f.name for f in group.pipeline.fills) != self.policy.features
                or len(group.pipeline.forest.trees) != self.policy.n_estimators
                or group.pipeline.forest.max_samples
                != min(self.policy.max_samples, group.training_rows)
            ):
                raise ValueError("anomaly_model_policy_pipeline_binding")
            if group.pipeline is None and group.forest_threshold is not None:
                raise ValueError("anomaly_unfitted_forest_threshold")
        return self


class ModelManifest(Contract):
    detector_id: DetectorID
    descriptor: ModelDescriptor


class RunDescriptor(Contract):
    version: Literal["anomaly-detector-run-1.0.0"] = "anomaly-detector-run-1.0.0"
    detector_id: DetectorID
    model_manifest_sha256: Sha256
    membership_sha256: Sha256
    validation_scores_sha256: Sha256
    test_scores_sha256: Sha256
    requested_rows: Annotated[int, Field(ge=1, le=10000)]
    role_status_counts: dict[str, Annotated[int, Field(ge=0)]]
    prediction_status_counts: dict[str, Annotated[int, Field(ge=0)]]
    model_quality: Literal["not_evaluated"] = "not_evaluated"
    operational_serving: Literal["not_qualified"] = "not_qualified"


class RunManifest(Contract):
    run_artifact_id: Annotated[str, Field(pattern=r"^anomaly-detector-run-sha256-[0-9a-f]{64}$")]
    descriptor: RunDescriptor
