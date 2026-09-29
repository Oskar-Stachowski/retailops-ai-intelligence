"""Bounded direct forecasting, portable learned pipelines and diagnostic comparison."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FeatureID,
    ForecastKey,
    LabelID,
    ModelID,
    Sha256,
    SplitID,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.evaluation_contract import (
    BASELINES,
    BaselineName,
    BaselinePolicy,
    MetricResult,
)
from retailops_ai.forecasting.manifest_contract import FileReceipt, Reason, TableReceipt
from retailops_ai.forecasting.preprocessing import FittedDescriptor

LearnedName = Literal["random_forest", "hist_gradient_boosting"]
ModelName = BaselineName | LearnedName
LEARNED_NAMES = ("random_forest", "hist_gradient_boosting")
MAX_MODEL_BYTES = 128 * 1024**2
MAX_NODES = 1048576


class RFConfig(Contract):
    n_estimators: Annotated[int, Field(ge=1, le=128)] = 64
    max_depth: Annotated[int, Field(ge=1, le=12)] = 12
    min_samples_leaf: Annotated[int, Field(ge=1, le=100)] = 2
    min_samples_split: Annotated[int, Field(ge=2, le=200)] = 2
    max_features: Annotated[float, Field(gt=0, le=1)] = 1.0
    bootstrap: Literal[True] = True
    criterion: Literal["squared_error"] = "squared_error"
    n_jobs: Literal[1] = 1


class HGBConfig(Contract):
    max_iter: Annotated[int, Field(ge=1, le=200)] = 100
    max_leaf_nodes: Annotated[int, Field(ge=2, le=63)] = 31
    min_samples_leaf: Annotated[int, Field(ge=1, le=100)] = 20
    learning_rate: Annotated[float, Field(gt=0, le=1)] = 0.1
    l2_regularization: Annotated[float, Field(ge=0, le=100)] = 0.0
    max_bins: Literal[255] = 255
    loss: Literal["squared_error"] = "squared_error"
    early_stopping: Literal[False] = False
    validation_fraction: None = None
    categorical_features: None = None


class ModelPolicy(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    strategy: Literal["direct_global_with_horizon_feature"] = "direct_global_with_horizon_feature"
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    random_state: Annotated[int, Field(ge=0, le=2147483647)] = 42
    rf: RFConfig = RFConfig()
    hgb: HGBConfig = HGBConfig()
    grid: Literal["one_frozen_configuration_per_family"] = "one_frozen_configuration_per_family"
    baseline: BaselinePolicy = BaselinePolicy()
    postprocessing: Literal["clip_negative_to_zero_no_rounding"] = (
        "clip_negative_to_zero_no_rounding"
    )
    primary_metric: Literal["validation_mae"] = "validation_mae"
    minimum_relative_improvement: Annotated[float, Field(ge=0, lt=1)] = 0.05
    tie_break: Literal["baseline_then_rf_then_hgb"] = "baseline_then_rf_then_hgb"
    selection_use: Literal["diagnostic_only_pending_04_6_to_04_8"] = (
        "diagnostic_only_pending_04_6_to_04_8"
    )
    cpu_threads: Literal[1] = 1
    max_train_rows: Annotated[int, Field(ge=1, le=50000)] = 50000
    max_matrix_bytes: Annotated[int, Field(ge=1024, le=128 * 1024**2)] = 64 * 1024**2
    fit_wall_seconds: Annotated[float, Field(gt=0, le=300)] = 120.0
    fit_cpu_seconds: Annotated[float, Field(gt=0, le=300)] = 90.0
    fit_rss_bytes: Annotated[int, Field(ge=1024**2, le=2 * 1024**3)] = 1024**3
    monitor_interval_seconds: Annotated[float, Field(ge=0.05, le=0.05)] = 0.05
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    legacy_retailops_serving: Literal["unchanged_rejection_not_overridden"] = (
        "unchanged_rejection_not_overridden"
    )


class TreeNode(Contract):
    value: float
    feature: Annotated[int, Field(ge=0)] | None
    threshold: float | None
    left: Annotated[int, Field(ge=0)] | None
    right: Annotated[int, Field(ge=0)] | None

    @model_validator(mode="after")
    def leaf(self) -> Self:
        if (self.feature is None) != all(
            v is None for v in (self.threshold, self.left, self.right)
        ):
            raise ValueError("portable_tree_leaf_mismatch")
        if self.feature is not None and any(
            v is None for v in (self.threshold, self.left, self.right)
        ):
            raise ValueError("portable_tree_branch_incomplete")
        return self


class LearnedTree(Contract):
    nodes: tuple[TreeNode, ...] = Field(min_length=1, max_length=8191)

    @model_validator(mode="after")
    def topology(self) -> Self:
        incoming = [0] * len(self.nodes)
        for index, node in enumerate(self.nodes):
            if node.feature is None:
                continue
            for child in (node.left, node.right):
                if child is None or child <= index or child >= len(self.nodes):
                    raise ValueError("portable_tree_cycle_or_child_outside_tree")
                incoming[child] += 1
        if incoming != [0, *([1] * (len(self.nodes) - 1))]:
            raise ValueError("portable_tree_nodes_not_reachable_once")
        return self


class LearnedEstimator(Contract):
    family: LearnedName
    feature_count: Annotated[int, Field(ge=1, le=4097)]
    input_dtype: Literal["float32", "float64"]
    aggregation: Literal["mean", "baseline_plus_sum"]
    intercept: float
    trees: tuple[LearnedTree, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if sum(len(tree.nodes) for tree in self.trees) > MAX_NODES or any(
            node.feature is not None and node.feature >= self.feature_count
            for tree in self.trees
            for node in tree.nodes
        ):
            raise ValueError("portable_estimator_dimensions_or_node_budget")
        if self.family == "random_forest" and (
            self.input_dtype != "float32" or self.aggregation != "mean" or self.intercept != 0
        ):
            raise ValueError("portable_rf_recipe_mismatch")
        if self.family == "hist_gradient_boosting" and (
            self.input_dtype != "float64" or self.aggregation != "baseline_plus_sum"
        ):
            raise ValueError("portable_hgb_recipe_mismatch")
        return self


class ModelCode(Contract):
    version: Literal["forecast-models-1.0.0"] = "forecast-models-1.0.0"
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    versions: dict[str, str]
    system: str
    machine: str

    @model_validator(mode="after")
    def digest(self) -> Self:
        if not self.code_files or canonical_sha256(self.code_files) != self.code_sha256:
            raise ValueError("forecast_model_code_hash_mismatch")
        return self


class PipelineDescriptor(Contract):
    family: LearnedName
    feature_set_id: FeatureID
    split_id: SplitID
    policy: ModelPolicy
    preprocessing: FittedDescriptor
    train_labels_content_sha256: Sha256
    output_columns: tuple[str, ...]
    estimator: LearnedEstimator
    code: ModelCode

    @model_validator(mode="after")
    def recipe(self) -> Self:
        if (
            self.family != self.estimator.family
            or self.feature_set_id != self.preprocessing.feature_set_id
            or self.split_id != self.preprocessing.split_id
            or self.output_columns != (*self.preprocessing.output_columns, "horizon_days")
            or len(self.output_columns) != self.estimator.feature_count
        ):
            raise ValueError("forecast_pipeline_binding_mismatch")
        expected = (
            self.policy.rf.n_estimators
            if self.family == "random_forest"
            else self.policy.hgb.max_iter
        )
        if len(self.estimator.trees) != expected:
            raise ValueError("forecast_pipeline_iteration_count_mismatch")
        return self


class ModelPipeline(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    model_id: ModelID
    descriptor: PipelineDescriptor
    generated_at: UtcTime
    deployment_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.model_id != "model-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("forecast_model_identity_mismatch")
        return self


class ModelPrediction(ForecastKey):
    fold: str
    role: Literal["train", "validation", "development_holdout", "purged"]
    model: LearnedName
    model_id: ModelID
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    eligible: bool
    exclusion_reasons: tuple[Reason, ...]
    predicted_units: Annotated[float, Field(ge=0)] | None
    prediction_kind: Literal["in_sample_diagnostic", "out_of_time_diagnostic", "excluded"]
    training_knowledge_cutoff: UtcTime

    @model_validator(mode="after")
    def eligibility(self) -> Self:
        if self.eligible != (not self.exclusion_reasons) or self.eligible != (
            self.predicted_units is not None
        ):
            raise ValueError("forecast_model_prediction_coverage_mismatch")
        expected = (
            "in_sample_diagnostic"
            if self.role == "train" and self.eligible
            else "out_of_time_diagnostic"
            if self.eligible
            else "excluded"
        )
        if expected != self.prediction_kind or (
            self.prediction_kind == "out_of_time_diagnostic"
            and self.forecast_origin <= self.training_knowledge_cutoff
        ):
            raise ValueError("forecast_model_prediction_temporal_use_mismatch")
        return self


class ForecastValue(ForecastKey):
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    model_id: ModelID
    predicted_units: Annotated[float, Field(ge=0)]
    deployment_status: Literal["not_ready"] = "not_ready"


class ModelSelection(Contract):
    fold: str
    baseline: BaselineName | None
    selected: ModelName | None
    status: Literal["selected", "not_ready"]
    validation_grain_sha256: Sha256
    validation_metrics: dict[str, MetricResult]
    selection_sha256: Sha256

    @model_validator(mode="after")
    def completeness(self) -> Self:
        if set(self.validation_metrics) != {*BASELINES, *LEARNED_NAMES}:
            raise ValueError("model_selection_candidates_mismatch")
        if len({metric.eligible_rows for metric in self.validation_metrics.values()}) != 1:
            raise ValueError("model_selection_common_count_mismatch")
        complete = all(metric.status == "passed" for metric in self.validation_metrics.values())
        if (self.status == "selected") != complete or (self.selected is not None) != complete:
            raise ValueError("model_selection_status_mismatch")
        if complete and self.baseline is None:
            raise ValueError("model_selection_baseline_required")
        return self


class ResourceReceipt(Contract):
    wall_seconds: Annotated[float, Field(ge=0)]
    cpu_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=0)]
    threads: Literal[1] = 1
    status: Literal["passed"] = "passed"


class ModelRunDescriptor(Contract):
    role: Literal["supervised_model_comparison"] = "supervised_model_comparison"
    feature_set_id: FeatureID
    split_id: SplitID
    parent: Parent
    label_dataset_id: LabelID
    policy: ModelPolicy
    code: ModelCode
    models: dict[str, ModelID]
    predictions_content_sha256: Sha256
    prediction_rows: Annotated[int, Field(ge=1)]
    coverage_counts: dict[str, Annotated[int, Field(ge=0)]]
    selections: tuple[ModelSelection, ...]
    metrics: dict[str, MetricResult]
    status: Literal["passed", "not_ready"]

    @model_validator(mode="after")
    def report(self) -> Self:
        folds = tuple(selection.fold for selection in self.selections)
        expected = {
            fold + ":" + role + ":" + model
            for fold in folds
            for role in ("train", "validation", "development_holdout")
            for model in (*BASELINES, *LEARNED_NAMES)
        }
        models = {fold + ":" + model for fold in folds for model in LEARNED_NAMES}
        if (
            not folds
            or len(set(folds)) != len(folds)
            or set(self.metrics) != expected
            or set(self.models) != models
        ):
            raise ValueError("model_report_fold_role_model_mismatch")
        for selection in self.selections:
            if any(
                self.metrics[selection.fold + ":validation:" + name] != metric
                for name, metric in selection.validation_metrics.items()
            ):
                raise ValueError("model_selection_report_metric_mismatch")
        passed = all(selection.status == "selected" for selection in self.selections) and all(
            metric.status == "passed" for metric in self.metrics.values()
        )
        if (self.status == "passed") != passed:
            raise ValueError("model_report_status_mismatch")
        return self


class ModelRunManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    comparison_id: Annotated[str, Field(pattern=r"^forecast-model-comparison-sha256-[0-9a-f]{64}$")]
    descriptor: ModelRunDescriptor
    predictions: TableReceipt
    pipelines: dict[str, FileReceipt]
    resources: dict[str, ResourceReceipt]
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (
            self.comparison_id
            != "forecast-model-comparison-sha256-"
            + canonical_sha256(self.descriptor.model_dump(mode="json"))
            or self.descriptor.predictions_content_sha256 != self.predictions.content_sha256
            or self.descriptor.prediction_rows != self.predictions.row_count
        ):
            raise ValueError("forecast_model_comparison_identity_mismatch")
        if set(self.pipelines) != set(self.descriptor.models) or set(self.resources) != set(
            self.descriptor.models
        ):
            raise ValueError("forecast_model_comparison_pipeline_inventory_mismatch")
        return self
