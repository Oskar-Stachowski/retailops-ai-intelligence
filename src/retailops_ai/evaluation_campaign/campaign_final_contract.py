"""Separate final-only facts; declarations never prove global holdout freshness."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    ForecastKey,
    Sha256,
    Symbol,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignParentBudget
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.label_contract import (
    DemandVersion,
    EligibilityReason,
    LabelReason,
    QualifiedForecastOutcome,
    RoleLabel,
)
from retailops_ai.evaluation_campaign.physical_contract import PhysicalRoleFile, PhysicalSourceSpec
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.manifest_contract import FeatureDescriptor, FeaturePolicy


class FinalRoleLabel(ForecastKey):
    role: Literal["final_evaluation"] = "final_evaluation"
    knowledge_cutoff: UtcTime
    label_delay_days: Annotated[int, Field(ge=1, le=30)]
    maturity_not_before: UtcTime
    status: Literal["eligible", "censored"]
    observed_sales_units: Annotated[int, Field(ge=0)] | None
    selected_version: DemandVersion | None
    reason: LabelReason | None

    @model_validator(mode="after")
    def maturity(self) -> Self:
        # Reuse only the pure maturity/selection rules. No legacy artifact or
        # access authorization is created by this in-memory validator adapter.
        value = self.model_dump(mode="json") | {"role": "development_evaluation"}
        RoleLabel.model_validate_json(canonical_bytes(value))
        return self


class FinalForecastOutcome(Contract):
    label: FinalRoleLabel
    feature_row_sha256: Sha256
    eligible: StrictBool
    reasons: tuple[EligibilityReason, ...]

    @model_validator(mode="after")
    def eligibility(self) -> Self:
        value = self.model_dump(mode="json")
        value["label"]["role"] = "development_evaluation"
        QualifiedForecastOutcome.model_validate_json(canonical_bytes(value))
        return self


class FinalForecastExample(Contract):
    key: ForecastKey
    outcome: FinalForecastOutcome

    @model_validator(mode="after")
    def binding(self) -> Self:
        label_key = self.outcome.label.model_dump(include=set(ForecastKey.model_fields))
        if self.key.model_dump() != label_key:
            raise ValueError("final_forecast_label_key_mismatch")
        return self


class CampaignFinalExportPlan(Contract):
    version: Literal["ai09-campaign-final-export-plan-1.0.0"] = (
        "ai09-campaign-final-export-plan-1.0.0"
    )
    phase: Literal["final"] = "final"
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    origins: OriginWindow
    label_knowledge_cutoff: UtcTime
    prior_exposure_end: date
    label_delay_days: Annotated[int, Field(ge=1, le=30)] = 1
    features: FeaturePolicy = FeaturePolicy()
    parent_budget: CampaignParentBudget = CampaignParentBudget()
    max_population_rows: Annotated[int, Field(ge=1, le=10000000)] = 10000000
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=4096, le=65536)] = 32768
    exposure: Literal["complete_parent_including_late_and_out_of_role_rows"] = (
        "complete_parent_including_late_and_out_of_role_rows"
    )
    holdout_freshness_qualified: FalseFlag = False
    resource_qualified: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def chronology(self) -> Self:
        if self.origins.start <= self.prior_exposure_end + timedelta(
            days=14 + self.label_delay_days
        ):
            raise ValueError("final_export_origins_overlap_declared_prior_exposure")
        if self.label_knowledge_cutoff < end_of_day(
            self.origins.end + timedelta(days=14 + self.label_delay_days)
        ):
            raise ValueError("final_export_labels_not_mature")
        return self

    def bind(self, source: PhysicalSourceSpec) -> "FinalForecastRecipe":
        observed = {k: getattr(source, k) for k in type(self.parent_budget).model_fields}
        if observed != self.parent_budget.model_dump():
            raise ValueError("final_export_parent_budget_changed")
        return FinalForecastRecipe(source=source, plan=self)

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class FinalForecastRecipe(Contract):
    source: PhysicalSourceSpec
    plan: CampaignFinalExportPlan

    @model_validator(mode="after")
    def history(self) -> Self:
        p = self.source.source_parameters
        start, end = p.get("start_date"), p.get("end_date")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError("final_export_explicit_source_history_required")
        if date.fromisoformat(
            start
        ) > self.plan.origins.start or self.plan.label_knowledge_cutoff > end_of_day(
            date.fromisoformat(end)
        ):
            raise ValueError("final_export_window_outside_source_history")
        observed = {k: getattr(self.source, k) for k in type(self.plan.parent_budget).model_fields}
        if observed != self.plan.parent_budget.model_dump():
            raise ValueError("final_export_parent_budget_changed")
        return self


class FinalForecastDescriptor(Contract):
    recipe: FinalForecastRecipe
    runtime: PreparationRuntime
    feature_set_id: str
    feature_descriptor: FeatureDescriptor
    population: PhysicalRoleFile
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    observation_rows: Annotated[int, Field(ge=1)]
    version_rows: Annotated[int, Field(ge=1)]
    version_inventory_sha256: Sha256

    @model_validator(mode="after")
    def binding(self) -> Self:
        source, plan = self.recipe.source, self.recipe.plan
        if (
            self.population.row_count != self.feature_descriptor.row_count
            or not 1 <= self.population.row_count <= plan.max_population_rows
            or self.feature_descriptor.parent != source.parent
            or self.feature_descriptor.source_parameters != source.source_parameters
            or self.feature_descriptor.resolved_policy != plan.features
            or self.feature_set_id
            != "features-sha256-"
            + canonical_sha256(self.feature_descriptor.model_dump(mode="json"))
            or self.runtime.code_sha256 != canonical_sha256(self.runtime.code_files)
            or not self.observation_rows <= self.version_rows <= source.max_rows_per_parent
        ):
            raise ValueError("final_forecast_descriptor_population_or_parent_mismatch")
        return self


class FinalForecastManifest(Contract):
    version: Literal["ai09-final-forecast-manifest-1.0.0"] = "ai09-final-forecast-manifest-1.0.0"
    dataset_id: Annotated[str, Field(pattern=r"^ai09-final-forecast-sha256-[0-9a-f]{64}$")]
    descriptor: FinalForecastDescriptor
    holdout_freshness_qualified: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.dataset_id != "ai09-final-forecast-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("final_forecast_dataset_identity_mismatch")
        return self


class CampaignFinalExportReceipt(Contract):
    version: Literal["ai09-campaign-final-export-receipt-1.0.0"] = (
        "ai09-campaign-final-export-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignFinalExportPlan
    generated_parent_receipt_sha256: Sha256
    selection_sha256: Sha256
    recipe: FinalForecastRecipe
    dataset_id: Annotated[str, Field(pattern=r"^ai09-final-forecast-sha256-[0-9a-f]{64}$")]
    manifest_sha256: Sha256
    runtime_code_sha256: Sha256
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    population_rows: Annotated[int, Field(ge=1)]
    artifact_bytes: Annotated[int, Field(ge=1)]
    holdout_freshness_qualified: FalseFlag = False
    resource_qualified: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.recipe != self.plan.bind(self.recipe.source)
            or self.population_rows > self.plan.max_population_rows
            or self.artifact_bytes > self.plan.max_artifact_bytes
        ):
            raise ValueError("final_export_receipt_recipe_or_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
