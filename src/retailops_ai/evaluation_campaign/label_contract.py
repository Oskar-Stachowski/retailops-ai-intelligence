"""Scoped physical evidence and diagnostic five-role forecast qualification."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    ForecastKey,
    SellingKey,
    Sha256,
    UtcTime,
    end_of_day,
)
from retailops_ai.evaluation_campaign.outcome_contract import OutcomePopulation
from retailops_ai.evaluation_campaign.outcome_journal import forecast_population
from retailops_ai.evaluation_campaign.partition_contract import (
    DevelopmentRole,
    ForecastPartitionManifest,
)


class DemandVersion(SellingKey):
    """An explicitly declared curated version; a seal does not prove source truth."""

    business_date: date
    record_id: Annotated[str, Field(min_length=1, max_length=512)]
    source_record_sha256: Sha256
    version: Annotated[int, Field(ge=1, le=2**63 - 1)]
    curated_available_at: UtcTime | None
    observed_units: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    observation_status: Literal["observed_positive", "observed_zero", "closed", "missing"]
    source_data_complete: StrictBool
    quality_status: Literal["valid", "quarantined", "incomplete"]

    @model_validator(mode="after")
    def quantity(self) -> Self:
        if (
            self.observation_status in ("observed_zero", "closed")
            and self.observed_units not in (None, 0)
            or self.observation_status == "observed_positive"
            and self.observed_units is not None
            and self.observed_units == 0
            or self.observation_status == "missing"
            and self.observed_units is not None
        ):
            raise ValueError("forecast_evidence_status_quantity_mismatch")
        return self


class OutcomeEvidence(Contract):
    key: ForecastKey
    candidates: Annotated[tuple[DemandVersion, ...], Field(max_length=8)]

    @model_validator(mode="after")
    def scoped(self) -> Self:
        if any(
            (v.product_id, v.selling_location_id, v.channel, v.business_date)
            != (
                self.key.product_id,
                self.key.selling_location_id,
                self.key.channel,
                self.key.target_date,
            )
            for v in self.candidates
        ):
            raise ValueError("forecast_evidence_candidate_outside_key")
        return self


class OutcomeEvidenceManifest(Contract):
    version: Literal["ai09-scoped-forecast-evidence-1.0.0"] = "ai09-scoped-forecast-evidence-1.0.0"
    population: OutcomePopulation
    row_count: Annotated[int, Field(ge=1, le=100000)]
    size_bytes: Annotated[int, Field(ge=1, le=64 * 1024**2)]
    format: Literal["canonical_key_sorted_outcomes_jsonl"] = "canonical_key_sorted_outcomes_jsonl"
    provenance: Literal["declared_curated_versions_full_source_parent_not_replayed"] = (
        "declared_curated_versions_full_source_parent_not_replayed"
    )
    source_qualification: Literal["not_established"] = "not_established"
    final_test_access_authorized: FalseFlag = False


class ForecastOutcomeReadPolicy(Contract):
    max_rows: Annotated[int, Field(ge=1, le=100000)] = 100000
    max_evidence_bytes: Annotated[int, Field(ge=1, le=64 * 1024**2)] = 64 * 1024**2
    max_index_bytes: Annotated[int, Field(ge=4096, le=128 * 1024**2)] = 128 * 1024**2
    max_record_bytes: Annotated[int, Field(ge=1024, le=32 * 1024)] = 32 * 1024
    sqlite_cache_kib: Literal[4096] = 4096
    version_selection: Literal["latest_known_version_no_complete_version_fallback"] = (
        "latest_known_version_no_complete_version_fallback"
    )
    missing_label: Literal["retain_censored_never_zero"] = "retain_censored_never_zero"
    public_parent_exposure: Literal["complete_feature_and_history_parent_verification"] = (
        "complete_feature_and_history_parent_verification"
    )
    version_exposure: Literal["declared_late_versions_read_but_never_selected"] = (
        "declared_late_versions_read_but_never_selected"
    )
    statistical_independence: Literal["not_asserted"] = "not_asserted"


class ForecastOutcomeReadProtocol(Contract):
    version: Literal["ai09-forecast-outcome-read-1.0.0"] = "ai09-forecast-outcome-read-1.0.0"
    partitions: ForecastPartitionManifest
    evidence: Annotated[tuple[OutcomeEvidenceManifest, ...], Field(min_length=1, max_length=5)]
    policy: ForecastOutcomeReadPolicy = ForecastOutcomeReadPolicy()
    qualification: Literal["development_reader_diagnostic_only"] = (
        "development_reader_diagnostic_only"
    )
    independent_evaluation_access_authorized: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if len({e.population.role for e in self.evidence}) != len(self.evidence):
            raise ValueError("forecast_outcome_duplicate_role")
        for evidence in self.evidence:
            role = evidence.population.role
            expected = forecast_population(
                self.partitions,
                role,
                outcome_artifact_sha256=evidence.population.outcome_artifact_sha256,
            )
            if evidence.population != expected or evidence.row_count != (
                self.partitions.descriptor.populations[role].row_count
            ):
                raise ValueError("forecast_outcome_population_parent_mismatch")
            if (
                evidence.row_count > self.policy.max_rows
                or evidence.size_bytes > self.policy.max_evidence_bytes
            ):
                raise ValueError("forecast_outcome_declared_resource_limit")
        return self


LabelReason = Literal["missing_or_unavailable", "incomplete_source", "not_mature"]
EligibilityReason = Literal[
    "insufficient_history", "stale_history", "unknown_calendar", "closed_target", "censored_label"
]


class RoleLabel(ForecastKey):
    role: DevelopmentRole
    knowledge_cutoff: UtcTime
    label_delay_days: Annotated[int, Field(ge=1, le=30)]
    maturity_not_before: UtcTime
    status: Literal["eligible", "censored"]
    observed_sales_units: Annotated[int, Field(ge=0)] | None
    selected_version: DemandVersion | None
    reason: LabelReason | None

    @model_validator(mode="after")
    def maturity(self) -> Self:
        selected = self.selected_version
        if self.maturity_not_before != end_of_day(
            self.target_date + timedelta(days=self.label_delay_days)
        ):
            raise ValueError("forecast_role_label_maturity_floor_mismatch")
        if selected is not None and (
            (
                selected.product_id,
                selected.selling_location_id,
                selected.channel,
                selected.business_date,
            )
            != (self.product_id, self.selling_location_id, self.channel, self.target_date)
            or selected.curated_available_at is None
            or selected.curated_available_at > self.knowledge_cutoff
        ):
            raise ValueError("forecast_role_label_selection_mismatch")
        if self.status == "eligible":
            if (
                selected is None
                or selected.curated_available_at is None
                or self.knowledge_cutoff < self.maturity_not_before
                or not end_of_day(self.target_date)
                <= selected.curated_available_at
                <= self.knowledge_cutoff
                or selected.quality_status != "valid"
                or selected.source_data_complete is not True
                or selected.observed_units is None
                or self.observed_sales_units != selected.observed_units
                or selected.observation_status == "missing"
                or self.reason is not None
            ):
                raise ValueError("forecast_role_label_not_complete_or_mature")
        elif self.observed_sales_units is not None or self.reason is None:
            raise ValueError("forecast_role_censored_label_is_not_zero")
        return self


class QualifiedForecastOutcome(Contract):
    label: RoleLabel
    feature_row_sha256: Sha256
    eligible: StrictBool
    reasons: tuple[EligibilityReason, ...]

    @model_validator(mode="after")
    def qualification(self) -> Self:
        if (
            self.eligible != (not self.reasons)
            or len(set(self.reasons)) != len(self.reasons)
            or ("censored_label" in self.reasons) != (self.label.status == "censored")
        ):
            raise ValueError("forecast_role_outcome_eligibility_mismatch")
        return self
