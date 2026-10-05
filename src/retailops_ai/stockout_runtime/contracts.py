"""A pinned complete classifier and physical-stock output, independent of forecasts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
)
from retailops_ai.data_contracts.identity import canonical_sha256 as digest
from retailops_ai.stockout_policy.contract import ModelPolicyPin, PolicySpec
from retailops_ai.stockout_runtime.public_contracts import (
    FactualFactor as FactualFactor,
)
from retailops_ai.stockout_runtime.public_contracts import (
    RiskBand as RiskBand,
)
from retailops_ai.stockout_runtime.public_contracts import (
    RiskItem as RiskItem,
)
from retailops_ai.stockout_runtime.public_contracts import (
    RiskStatus as RiskStatus,
)
from retailops_ai.stockout_runtime.public_contracts import (
    RuntimeLineage as RuntimeLineage,
)
from retailops_ai.stockout_runtime.public_contracts import (
    RuntimeReleasePin as RuntimeReleasePin,
)
from retailops_ai.stockout_selection.contract import (
    ConditionalRiskPipeline,
    ConditionalSigmoid,
    SelectionModelPin,
)
from retailops_ai.stockout_training.contract import RiskPipeline, Sigmoid


class ScoringRecipe(Contract):
    version: Literal["stockout-portable-scoring-1.0.0", "stockout-portable-scoring-2.0.0"] = (
        "stockout-portable-scoring-1.0.0"
    )
    pin: ModelPolicyPin | SelectionModelPin
    pipeline: RiskPipeline | ConditionalRiskPipeline

    @model_validator(mode="after")
    def identity_and_chronology(self) -> Self:
        conditional = isinstance(self.pipeline, ConditionalRiskPipeline)
        sigmoid: Sigmoid | ConditionalSigmoid | None
        if isinstance(self.pipeline, ConditionalRiskPipeline):
            sigmoid = self.pipeline.calibrator
            slope = self.pipeline.calibrator.raw_score_slope
        else:
            sigmoid = self.pipeline.sigmoid
            slope = self.pipeline.sigmoid.slope if self.pipeline.sigmoid else 0.0
        if (
            sigmoid is None
            or (conditional != isinstance(self.pin, SelectionModelPin))
            or conditional != self.version.endswith("2.0.0")
            or slope <= 0
            or self.pin.model_id
            != "risk-model-sha256-" + digest(self.pipeline.model_dump(mode="json"))
            or self.pin.calibrator_sha256 != digest(sigmoid.model_dump(mode="json"))
            or self.pin.selection_known_at < sigmoid.fit_known_at
        ):
            raise ValueError("stockout_runtime_model_calibrator_or_selection_pin")
        return self


class ScoringPolicy(Contract):
    version: Literal["stockout-scoring-policy-1.0.0", "stockout-scoring-policy-2.0.0"] = (
        "stockout-scoring-policy-1.0.0"
    )
    policy_id: Annotated[str, Field(pattern=r"^stockout-scoring-policy-sha256-[0-9a-f]{64}$")]
    pin: ModelPolicyPin | SelectionModelPin
    proposal_id: Annotated[str, Field(pattern=r"^stockout-policy-proposal-sha256-[0-9a-f]{64}$")]
    spec: PolicySpec
    # This recipe is an input to approval, not an approval by itself.
    operational_approval: Literal["requires_separate_lifecycle_approval"] = (
        "requires_separate_lifecycle_approval"
    )

    @model_validator(mode="after")
    def identity(self) -> Self:
        conditional = isinstance(self.pin, SelectionModelPin)
        if (
            conditional != self.version.endswith("2.0.0")
            or conditional != self.spec.version.endswith("2.0.0")
            or self.policy_id
            != "stockout-scoring-policy-sha256-"
            + digest(self.model_dump(mode="json", exclude={"policy_id"}))
        ):
            raise ValueError("stockout_runtime_threshold_policy_identity")
        return self
