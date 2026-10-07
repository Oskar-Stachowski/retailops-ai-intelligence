"""Explicit operator choices, bound to one model and its calibrator."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime

Probability = Annotated[float, Field(ge=0, le=1)]
PositiveCost = Annotated[float, Field(gt=0, le=1000000)]


class RiskThresholds(Contract):
    medium_from: Annotated[float, Field(gt=0, lt=1)]
    high_from: Annotated[float, Field(gt=0, lt=1)]
    critical_from: Annotated[float, Field(gt=0, lt=1)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.medium_from < self.high_from < self.critical_from:
            raise ValueError("stockout_policy_thresholds_must_increase")
        return self


class OperatorCapacity(Contract):
    mode: Literal["top_n", "top_fraction"]
    top_n: Annotated[int, Field(ge=1, le=200)] | None = None
    fraction: Annotated[float, Field(gt=0, le=1)] | None = None
    unit: Literal["physical_product_stock_keys_per_origin"] = (
        "physical_product_stock_keys_per_origin"
    )
    ties: Literal["product_stock_lexical"] = "product_stock_lexical"

    @model_validator(mode="after")
    def one_capacity(self) -> Self:
        if (self.top_n is not None) != (self.mode == "top_n") or (self.fraction is not None) != (
            self.mode == "top_fraction"
        ):
            raise ValueError("stockout_policy_exactly_one_capacity")
        return self


class AttentionCosts(Contract):
    false_attention: PositiveCost
    missed_incident: PositiveCost
    interpretation: Literal["operator_proposal_not_validated_business_savings"] = (
        "operator_proposal_not_validated_business_savings"
    )


class PolicySpec(Contract):
    version: Literal["stockout-threshold-proposal-1.0.0", "stockout-threshold-proposal-2.0.0"] = (
        "stockout-threshold-proposal-1.0.0"
    )
    thresholds: RiskThresholds
    capacity: OperatorCapacity
    costs: AttentionCosts
    evaluation_role: Literal["tune", "calibration"] = "tune"
    final_test_access: Literal["forbidden"] = "forbidden"
    approval_status: Literal["proposal_requires_operator_review"] = (
        "proposal_requires_operator_review"
    )

    @model_validator(mode="after")
    def role_for_protocol(self) -> Self:
        if (self.version.endswith("2.0.0")) != (self.evaluation_role == "calibration"):
            raise ValueError("stockout_policy_protocol_evaluation_role")
        return self


class ModelPolicyPin(Contract):
    qualification_id: Annotated[str, Field(pattern=r"^stockout-qualification-sha256-[0-9a-f]{64}$")]
    model_id: Annotated[str, Field(pattern=r"^risk-model-sha256-[0-9a-f]{64}$")]
    calibrator_sha256: Sha256
    selection_known_at: UtcTime
    prediction_mode: Literal["sigmoid"] = "sigmoid"


class PolicyRow(Contract):
    product_id: str = Field(min_length=1, max_length=128)
    stock_location_id: str = Field(min_length=1, max_length=128)
    as_of: UtcTime
    category_id: str = Field(min_length=1, max_length=128)
    probability: Probability
    incident_stockout: Annotated[int, Field(ge=0, le=1)]
    historical_inventory_constraint: bool


def risk_band(probability: float, thresholds: RiskThresholds) -> str:
    # Validate public values again; model_copy is not a validation mechanism.
    checked = RiskThresholds.model_validate_json(thresholds.model_dump_json())
    if type(probability) is not float or not 0 <= probability <= 1:
        raise ValueError("stockout_policy_probability_invalid")
    if probability >= checked.critical_from:
        return "critical"
    if probability >= checked.high_from:
        return "high"
    if probability >= checked.medium_from:
        return "medium"
    return "low"
