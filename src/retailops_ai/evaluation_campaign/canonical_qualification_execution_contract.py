"""Original full-journal receipts and costs; development evidence grants no final access."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.canonical_qualification import (
    PreparedCanonicalQualificationProtocol,
)


class CanonicalQualificationExecutionResult(Contract):
    version: Literal["ai09-canonical-qualification-execution-result-1.0.0"] = (
        "ai09-canonical-qualification-execution-result-1.0.0"
    )
    protocol: PreparedCanonicalQualificationProtocol
    journal_head_sha256: Sha256
    operation_receipt_sha256: dict[str, Sha256]
    operation_costs: Annotated[tuple[CampaignCost, ...], Field(min_length=8, max_length=16)]
    calibration_status: Literal["not_attempted", "not_ready", "fitted_for_independent_evaluation"]
    evaluation_quality: dict[Literal["ordinary", "demand", "physical"], bool]
    reasons: tuple[str, ...]
    status: Literal["completed", "not_ready"]
    development_quality_qualified: bool
    final_test_access_authorized: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_original_prefix(self) -> Self:
        count = len(self.operation_costs)
        if (
            count not in {8, 10, 16}
            or set(self.operation_receipt_sha256)
            != {o.operation_id for o in self.protocol.operations[:count]}
            or any(
                c.peak_process_tree_rss_bytes is None or c.artifact_bytes is None
                for c in self.operation_costs
            )
            or (self.calibration_status == "not_attempted") != (count == 8)
            or (self.calibration_status == "not_ready") != (count == 10)
            or set(self.evaluation_quality)
            != ({"ordinary", "demand", "physical"} if count == 16 else set())
            or (self.status == "completed") != (count == 16)
            or (not self.reasons) != (count == 16)
            or self.development_quality_qualified
            != (count == 16 and all(self.evaluation_quality.values()))
        ):
            raise ValueError("canonical_qualification_original_completion_or_cost_mismatch")
        return self

    def cold_wall_seconds(self) -> float:
        prefix = self.protocol.qualification.prefix
        return (
            prefix.advancement.cold_wall_seconds()
            + prefix.parents.cold_wall_seconds()
            + sum(c.wall_seconds for c in self.operation_costs)
        )
