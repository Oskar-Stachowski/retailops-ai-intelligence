"""Offline v12 load/predict contracts; tracking evidence never grants serving permission."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    Sha256,
    SourceID,
    Symbol,
    TrueFlag,
    Units,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import ProfileID
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt
from retailops_ai.source_snapshot.files import relative_path

MAX_ROWS = 256
MAX_REQUEST_BYTES = 4 * 1024**2
MAX_RESULT_BYTES = 1024**2
MAX_STDERR_BYTES = 16 * 1024
RunID = Annotated[str, Field(pattern=r"^functional-v12-run-sha256-[0-9a-f]{64}$")]
RecipeID = Annotated[str, Field(pattern=r"^functional-v12-recipe-sha256-[0-9a-f]{64}$")]
Zero = Annotated[int, Field(ge=0, le=0)]


class V12RuntimePin(Contract):
    version: Literal["forecast-v12-offline-runtime-pin-1.0.0"] = (
        "forecast-v12-offline-runtime-pin-1.0.0"
    )
    purpose: Literal["offline_load_predict_acceptance"] = "offline_load_predict_acceptance"
    run_id: RunID
    campaign_id: Annotated[str, Field(pattern=r"^functional-v12-campaign-sha256-[0-9a-f]{64}$")]
    freeze_id: Annotated[str, Field(pattern=r"^functional-v12-freeze-sha256-[0-9a-f]{64}$")]
    replay_id: Annotated[str, Field(pattern=r"^functional-v12-replay-sha256-[0-9a-f]{64}$")]
    cohort_id: Symbol
    fold: FoldPlan
    recipe_id: RecipeID
    recipe_path: str
    recipe: V12ArtifactReceipt
    manifest: V12ArtifactReceipt
    signature: V12ArtifactReceipt
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    source_dataset_id: SourceID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    forecast_model_status: Literal["ready", "not_ready"]
    serving_eligible: FalseFlag = False

    @model_validator(mode="after")
    def artifact_boundary(self) -> Self:
        relative_path(self.recipe_path)
        if not self.recipe_path.startswith("campaign/") or any(
            not 1 <= receipt.size_bytes <= MAX_REQUEST_BYTES
            for receipt in (self.recipe, self.manifest, self.signature)
        ):
            raise ValueError("v12_runtime_metadata_boundary")
        return self


class V12ExecutionLimits(Contract):
    wall_seconds: Annotated[float, Field(gt=0, le=120)] = 60.0
    rss_bytes: Annotated[int, Field(ge=64 * 1024**2, le=1024**3)] = 512 * 1024**2


class V12Execution(Contract):
    pin: V12RuntimePin
    inputs: PreparedInputs
    limits: V12ExecutionLimits = V12ExecutionLimits()


class V12Interval(Contract):
    lower: Units
    upper: Units

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.upper < self.lower:
            raise ValueError("v12_runtime_interval_order")
        return self


class V12Forecast(Contract):
    median: Units | None
    mean: Units | None
    interval: V12Interval | None

    @model_validator(mode="after")
    def quantiles(self) -> Self:
        if (
            self.median is not None
            and self.interval is not None
            and not (self.interval.lower <= self.median <= self.interval.upper)
        ):
            raise ValueError("v12_runtime_median_outside_interval")
        return self


class V12PredictionMetadata(Contract):
    selected: str | None
    baseline: str | None
    mean_source: Symbol
    exact_reference_median: TrueFlag
    exact_reference_interval: TrueFlag
    recipe_id: RecipeID


class V12Prediction(Contract):
    key: Annotated[str, Field(min_length=1, max_length=1024)]
    candidate: V12Forecast
    baseline: V12Forecast
    metadata: V12PredictionMetadata
    exclusion_reason: Literal["closed_target"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def retained_reference(self) -> Self:
        if (
            self.candidate.median != self.baseline.median
            or self.candidate.interval != self.baseline.interval
            or self.metadata.selected != self.metadata.baseline
            or (
                self.exclusion_reason is not None
                and any(
                    v is not None
                    for forecast in (self.candidate, self.baseline)
                    for v in (forecast.median, forecast.mean, forecast.interval)
                )
            )
        ):
            raise ValueError("v12_runtime_exact_reference_changed")
        return self


class V12RuntimeResult(Contract):
    version: Literal["forecast-v12-offline-runtime-result-1.0.0"] = (
        "forecast-v12-offline-runtime-result-1.0.0"
    )
    purpose: Literal["offline_load_predict_acceptance"] = "offline_load_predict_acceptance"
    pin: V12RuntimePin
    profile_id: ProfileID
    predictions: tuple[V12Prediction, ...] = Field(min_length=1, max_length=MAX_ROWS)
    predictions_sha256: Sha256
    cold_load_seconds: Annotated[float, Field(ge=0)]
    compute_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=1)]
    generated_at: UtcTime
    model_refits: Zero
    source_generation: FalseFlag
    serving_eligible: FalseFlag
    published_forecast_outputs: Zero

    @model_validator(mode="after")
    def result_identity(self) -> Self:
        values = [prediction.model_dump(mode="json") for prediction in self.predictions]
        if self.predictions_sha256 != canonical_sha256(values) or any(
            prediction.metadata.recipe_id != self.pin.recipe_id for prediction in self.predictions
        ):
            raise ValueError("v12_runtime_result_identity")
        return self
