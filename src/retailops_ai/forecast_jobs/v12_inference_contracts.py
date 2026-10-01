"""Label-free inference results preserve every v12 functional and its exact reference."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.contracts import ProfileID
from retailops_ai.forecast_jobs.v12_contracts import MAX_ROWS, V12Prediction, V12RuntimePin, Zero
from retailops_ai.model_lifecycle.v12_release_contracts import V12InferenceContext


class V12InferenceResult(Contract):
    version: Literal["forecast-v12-inference-result-1.0.0"] = "forecast-v12-inference-result-1.0.0"
    pin: V12RuntimePin
    inference: V12InferenceContext
    profile_id: ProfileID
    predictions: tuple[V12Prediction, ...] = Field(min_length=1, max_length=MAX_ROWS)
    predictions_sha256: Sha256
    cold_load_seconds: Annotated[float, Field(ge=0)]
    compute_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=1)]
    generated_at: UtcTime
    model_refits: Zero
    source_generation: FalseFlag
    published_forecast_outputs: Zero

    @model_validator(mode="after")
    def result_identity(self) -> Self:
        if (
            self.pin.forecast_model_status != "ready"
            or self.predictions_sha256
            != canonical_sha256([p.model_dump(mode="json") for p in self.predictions])
            or any(p.metadata.recipe_id != self.pin.recipe_id for p in self.predictions)
        ):
            raise ValueError("v12_inference_result_identity")
        return self
