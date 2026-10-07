"""Frozen development recipe, train-only normalization and explicit output limitations."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    FeatureID,
    ForecastKey,
    ModelID,
    Sha256,
    SplitID,
    TrueFlag,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.preprocessing import FittedState
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast


class ChallengerPolicy(Contract):
    version: Literal["ai09-keras-development-1.0.0"] = "ai09-keras-development-1.0.0"
    scope: Literal["development_only_no_final_test_or_release"] = (
        "development_only_no_final_test_or_release"
    )
    architecture: Literal["history28_target_covariates_dense_direct14_mean_median"] = (
        "history28_target_covariates_dense_direct14_mean_median"
    )
    hidden_units: tuple[Literal[32], Literal[16]] = (32, 16)
    initialization_seed: Annotated[int, Field(ge=0, le=2147483647)] = 42
    data_seed: Annotated[int, Field(ge=0, le=2147483647)] = 42
    trials: Literal[1] = 1
    epochs: Annotated[int, Field(ge=1, le=25)] = 25
    batch_size: Annotated[int, Field(ge=1, le=128)] = 32
    patience: Annotated[int, Field(ge=1, le=5)] = 4
    learning_rate: Annotated[float, Field(gt=0, le=0.01)] = 0.001
    objective: Literal["masked_train_scaled_mean_mse_plus_median_mae"] = (
        "masked_train_scaled_mean_mse_plus_median_mae"
    )
    early_stopping: Literal["development_validation_only_restore_best_epoch"] = (
        "development_validation_only_restore_best_epoch"
    )
    cpu_threads: Literal[1] = 1
    wall_seconds: Annotated[float, Field(gt=0, le=1200)] = 1200.0
    cpu_seconds: Annotated[float, Field(gt=0, le=1200)] = 1200.0
    rss_bytes: Annotated[int, Field(ge=1024**2, le=1024**3)] = 1024**3
    max_matrix_bytes: Annotated[int, Field(ge=1024, le=64 * 1024**2)] = 64 * 1024**2
    max_windows: Annotated[int, Field(ge=1, le=3000)] = 3000
    artifact_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**2)] = 32 * 1024**2
    interval_policy: Literal["absent_pending_separate_development_calibration"] = (
        "absent_pending_separate_development_calibration"
    )
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False


class Scale(Contract):
    center: float
    spread: Annotated[float, Field(gt=0)]


class Normalization(Contract):
    version: Literal["ai09-train-only-normalization-1.0.0"] = "ai09-train-only-normalization-1.0.0"
    encoding: FittedState
    scales: tuple[Scale, ...]
    history_fill: float
    history_scale: Scale
    target_scale: Annotated[float, Field(gt=0)]
    history_order: Literal["origin_minus_27_through_origin_calendar_days"] = (
        "origin_minus_27_through_origin_calendar_days"
    )
    history_channels: tuple[Literal["scaled_units"], Literal["missing"], Literal["inactive"]] = (
        "scaled_units",
        "missing",
        "inactive",
    )
    train_windows_sha256: Sha256

    @model_validator(mode="after")
    def dimensions(self) -> Self:
        if len(self.scales) != len(self.encoding.descriptor.output_columns):
            raise ValueError("tensorflow_normalization_dimension_mismatch")
        return self

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json") | {
            "encoding": self.encoding.descriptor.model_dump(mode="json")
        }

    def content_sha256(self) -> str:
        return canonical_sha256(self.identity_payload())

    @property
    def input_width(self) -> int:
        return 28 * 3 + 14 * (len(self.scales) + 1)


class ChallengerPrediction(ForecastKey):
    value: FunctionalForecast
    interval_status: Literal["not_ready"] = "not_ready"
    interval_reason: Literal["development_calibration_not_implemented"] = (
        "development_calibration_not_implemented"
    )
    deployment_status: Literal["not_ready"] = "not_ready"


class ArtifactFile(Contract):
    size_bytes: Annotated[int, Field(ge=1, le=32 * 1024**2)]
    sha256: Sha256


class Binding(Contract):
    feature_set_id: FeatureID
    split_id: SplitID
    preprocessing_sha256: Sha256
    environment_lock_sha256: Sha256
    policy_sha256: Sha256
    code_sha256: Sha256
    train_population_sha256: Sha256
    validation_population_sha256: Sha256


class EpochReceipt(Contract):
    epoch: Annotated[int, Field(ge=1, le=25)]
    validation_loss: Annotated[float, Field(ge=0)]


class WorkerReceipt(Contract):
    status: Literal["passed"]
    training_seconds: Annotated[float, Field(ge=0)]
    worker_seconds: Annotated[float, Field(ge=0)]
    cold_load_seconds: Annotated[float, Field(ge=0)]
    batch_inference_seconds: Annotated[float, Field(ge=0)]
    best_epoch: Annotated[int, Field(ge=1, le=25)]
    epochs_run: Annotated[int, Field(ge=1, le=25)]
    history: tuple[EpochReceipt, ...] = Field(min_length=1, max_length=25)
    mlflow_run_id: Annotated[str, Field(pattern="^[0-9a-f]{32}$")]
    tensorflow_version: Literal["2.20.0"]
    keras_version: Literal["3.11.3"]
    mlflow_version: Literal["3.4.0"]
    system: Literal["Darwin", "Linux"]
    machine: Literal["arm64", "x86_64"]
    python_version: Literal["3.11.15"]
    determinism_enabled: TrueFlag
    determinism_scope: Literal[
        "fixed_versions_platform_and_initialization_seed_not_cross_platform_bitwise"
    ]
    visible_gpu_count: Literal[0]
    input_shape: tuple[None, Annotated[int, Field(ge=1)]]
    output_shape: tuple[None, Literal[14], Literal[2]]
    output_order: tuple[Literal["mean"], Literal["median"]]
    reload_rtol: Annotated[float, Field(ge=0.000001, le=0.000001)]
    reload_atol: Annotated[float, Field(ge=0.000001, le=0.000001)]
    interval_status: Literal["not_ready"]
    promotion_allowed: FalseFlag
    final_test_accessed: FalseFlag
    peak_self_rss_bytes: Annotated[int, Field(gt=0)]
    self_cpu_seconds: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def epochs(self) -> Self:
        if (
            tuple(e.epoch for e in self.history) != tuple(range(1, self.epochs_run + 1))
            or self.best_epoch > self.epochs_run
        ):
            raise ValueError("tensorflow_epoch_receipt_mismatch")
        return self


class TrainingResources(Contract):
    wall_seconds: Annotated[float, Field(gt=0)]
    cpu_seconds: Annotated[float, Field(ge=0)]
    peak_tree_rss_bytes: Annotated[int, Field(gt=0)]
    monitor_interval_seconds: Annotated[float, Field(ge=0.05, le=0.05)]
    cpu_threads: Literal[1]


class ModelDescriptor(Contract):
    binding: Binding
    policy: ChallengerPolicy
    files: dict[str, ArtifactFile]
    input_width: Annotated[int, Field(ge=1)]
    output_order: tuple[Literal["mean"], Literal["median"]]


class ChallengerManifest(Contract):
    schema_version: Literal["ai09-tensorflow-artifact-1.0.0"] = "ai09-tensorflow-artifact-1.0.0"
    model_id: ModelID
    descriptor: ModelDescriptor
    worker: WorkerReceipt
    resources: TrainingResources
    preparation_seconds: Annotated[float, Field(ge=0)]
    artifact_bytes: Annotated[int, Field(gt=0)]
    status: Literal["trained_and_reloaded_development_only"]
    deployment_status: Literal["not_ready"]
    interval_status: Literal["not_ready"]
    final_test_accessed: FalseFlag
    promotion_allowed: FalseFlag

    @model_validator(mode="after")
    def consistency(self) -> Self:
        policy, worker, measured = self.descriptor.policy, self.worker, self.resources
        if (
            self.model_id
            != "model-sha256-" + canonical_sha256(self.descriptor.model_dump(mode="json"))
            or self.artifact_bytes != sum(f.size_bytes for f in self.descriptor.files.values())
            or self.artifact_bytes > policy.artifact_bytes
            or measured.wall_seconds > policy.wall_seconds
            or measured.cpu_seconds > policy.cpu_seconds
            or measured.peak_tree_rss_bytes > policy.rss_bytes
            or worker.self_cpu_seconds > measured.cpu_seconds
            or worker.peak_self_rss_bytes > measured.peak_tree_rss_bytes
            or worker.input_shape != (None, self.descriptor.input_width)
            or worker.epochs_run > policy.epochs
            or (worker.system, worker.machine) not in {("Darwin", "arm64"), ("Linux", "x86_64")}
        ):
            raise ValueError("tensorflow_manifest_identity_signature_or_budget_mismatch")
        return self
