"""Safe serialized baseline recipe; inference reuses the AI 04 predictor."""

from typing import Literal, Self

from pydantic import model_validator

from retailops_ai.data_contracts.common import Contract, FeatureID, ModelID, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.evaluation_contract import BaselineName, BaselinePolicy
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow


class BaselinePipeline(Contract):
    format: Literal["forecast-baseline-v1"]
    model_id: ModelID
    feature_set_id: FeatureID
    model: BaselineName
    policy: BaselinePolicy
    selection_cutoff: UtcTime

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.model_id != "model-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"model_id"})
        ):
            raise ValueError("baseline_pipeline_identity_mismatch")
        return self


class BaselineInput(Contract):
    row: InputRow
    history: HistoryContext


def predict_examples(pipeline: BaselinePipeline, examples: list[BaselineInput]) -> list[float]:
    from retailops_ai.forecasting.baselines import predict

    output = []
    for example in examples:
        if example.row.forecast_origin <= pipeline.selection_cutoff:
            raise ValueError("baseline_not_known_at_origin")
        result = predict(pipeline.model, example.row, example.history, pipeline.policy)
        if result.predicted_units is None:
            raise ValueError("baseline_load_smoke_insufficient_history")
        output.append(float(result.predicted_units))
    return output
