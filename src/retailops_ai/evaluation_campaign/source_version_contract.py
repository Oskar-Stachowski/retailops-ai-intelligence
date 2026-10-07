"""Temporal quality evidence for replayed observed-quantity history 1.0.0."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    Sha256,
    TrueFlag,
    UtcTime,
    utc_time,
)
from retailops_ai.evaluation_campaign.label_contract import DemandVersion
from retailops_ai.evaluation_campaign.source_replay_contract import ForecastSourceReplayReceipt


class ForecastSourceVersion(Contract):
    """A quantity version with separate quality knowledge; no implicit completeness.

    This wire object is diagnostic metadata, not authority to open a source or
    evidence that arbitrary caller declarations have been physically replayed.
    Only the context-bound source reader establishes its actual parent binding.
    """

    candidate: DemandVersion
    observation_id: Annotated[str, Field(min_length=1, max_length=512)]
    observation_source_record_sha256: Sha256
    history_policy_version: Literal["observed-quantity-history-1.0.0"] = (
        "observed-quantity-history-1.0.0"
    )
    quality_basis: Literal["exact_latest_observation", "historical_quality_not_established"]
    quality_available_at: UtcTime | None
    source_data_complete: StrictBool | None
    quality_status: Literal["valid", "quarantined", "incomplete"] | None

    @model_validator(mode="after")
    def quality_evidence(self) -> Self:
        if self.candidate.source_data_complete or self.candidate.quality_status != "incomplete":
            raise ValueError("forecast_source_version_base_must_remain_unqualified")
        proof = (self.quality_available_at, self.source_data_complete, self.quality_status)
        if self.quality_basis == "historical_quality_not_established":
            if any(v is not None for v in proof):
                raise ValueError("forecast_source_version_historical_quality_is_unknown")
        elif (
            any(v is None for v in proof)
            or self.candidate.curated_available_at is None
            or self.quality_available_at is None
            or self.quality_available_at < self.candidate.curated_available_at
        ):
            raise ValueError("forecast_source_version_quality_time_mismatch")
        return self

    def at_cutoff(self, cutoff: datetime) -> DemandVersion:
        """Keep original quantity availability; unavailable quality remains censored.

        Returning a candidate never establishes role membership, maturity,
        feature eligibility, statistical independence or final-test authority.
        """
        if type(cutoff) is not datetime:
            raise ValueError("forecast_source_version_explicit_utc_cutoff_required")
        cutoff = utc_time(cutoff)
        known = self.quality_available_at is not None and self.quality_available_at <= cutoff
        return DemandVersion.model_validate(
            self.candidate.model_dump()
            | {
                "source_data_complete": self.source_data_complete if known else False,
                "quality_status": self.quality_status if known else "incomplete",
            }
        )


class ForecastSourceVersionReceipt(Contract):
    version: Literal["ai09-forecast-source-versions-receipt-1.0.0"] = (
        "ai09-forecast-source-versions-receipt-1.0.0"
    )
    source_replay: ForecastSourceReplayReceipt
    observation_rows: Annotated[int, Field(ge=1, le=100000)]
    version_rows: Annotated[int, Field(ge=1, le=100000)]
    latest_observation_quality_proofs: Annotated[int, Field(ge=1, le=100000)]
    historical_versions_without_quality_proof: Annotated[int, Field(ge=0, le=100000)]
    version_inventory_sha256: Sha256
    version_inventory_verified: TrueFlag = True
    quality_policy: Literal[
        "latest_exact_observation_flags_at_max_curated_availability_prior_quality_unknown"
    ] = "latest_exact_observation_flags_at_max_curated_availability_prior_quality_unknown"
    maximum_versions_per_observation: Literal[8] = 8
    sqlite_cache_kib: Literal[4096] = 4096
    maximum_index_bytes: Literal[134217728] = 134217728
    feature_and_partition_rows_verified: FalseFlag = False
    scoped_outcome_evidence_verified: FalseFlag = False
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    evaluation_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def counts(self) -> Self:
        if (
            self.latest_observation_quality_proofs != self.observation_rows
            or self.version_rows
            != self.latest_observation_quality_proofs
            + self.historical_versions_without_quality_proof
            or self.version_rows > self.source_replay.curated_rows
        ):
            raise ValueError("forecast_source_version_count_reconciliation_mismatch")
        return self
