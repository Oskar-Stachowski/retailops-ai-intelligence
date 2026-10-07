"""Pre-generation export plans and receipts bound to the prospective journal."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy, RoleWindow
from retailops_ai.evaluation_campaign.physical_contract import (
    PhysicalForecastRecipe,
    PhysicalSourceSpec,
)
from retailops_ai.forecasting.contract import OriginWindow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy


class CampaignParentBudget(Contract):
    max_parent_bytes: Annotated[int, Field(ge=1024, le=2 * 1024**3)] = 2 * 1024**3
    max_parent_files: Annotated[int, Field(ge=2, le=10000)] = 10000
    max_rows_per_parent: Annotated[int, Field(ge=1, le=20000000)] = 20000000
    batch_rows: Annotated[int, Field(ge=1, le=8192)] = 256


class CampaignDevelopmentExportPlan(Contract):
    """Freeze temporal/features/resource policy before parent IDs are generated."""

    version: Literal["ai09-campaign-development-export-plan-1.0.0"] = (
        "ai09-campaign-development-export-plan-1.0.0"
    )
    phase: Literal["development"] = "development"
    source_recipe_sha256: Sha256
    generation_operation_id: Symbol
    origins: OriginWindow
    roles: tuple[RoleWindow, ...] = Field(min_length=5, max_length=5)
    purge_days: Annotated[int, Field(ge=15, le=90)] = 15
    label_delay_days: Annotated[int, Field(ge=1, le=30)] = 1
    features: FeaturePolicy = FeaturePolicy()
    parent_budget: CampaignParentBudget = CampaignParentBudget()
    max_population_rows: Annotated[int, Field(ge=5, le=10000000)] = 10000000
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_record_bytes: Annotated[int, Field(ge=4096, le=65536)] = 32768
    exposure: Literal["complete_parent_including_late_and_out_of_role_rows"] = (
        "complete_parent_including_late_and_out_of_role_rows"
    )
    resource_qualified: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def chronology(self) -> Self:
        ForecastPartitionPolicy(
            roles=self.roles,
            purge_days=self.purge_days,
            label_delay_days=self.label_delay_days,
        )
        if (
            self.origins.start != self.roles[0].origins.start
            or self.origins.end != self.roles[-1].origins.end
        ):
            raise ValueError("campaign_export_exact_development_role_span_required")
        return self

    def bind(self, source: PhysicalSourceSpec) -> PhysicalForecastRecipe:
        observed = {k: getattr(source, k) for k in type(self.parent_budget).model_fields}
        if observed != self.parent_budget.model_dump():
            raise ValueError("campaign_export_parent_budget_changed")
        return PhysicalForecastRecipe(
            source=source,
            origins=self.origins,
            roles=self.roles,
            purge_days=self.purge_days,
            label_delay_days=self.label_delay_days,
            features=self.features,
            max_population_rows=self.max_population_rows,
            max_artifact_bytes=self.max_artifact_bytes,
            max_index_bytes=self.max_index_bytes,
            max_record_bytes=self.max_record_bytes,
        )

    def content_sha256(self) -> str:
        document = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(document))
        return canonical_sha256(document)


class CampaignGeneratedParentReceipt(Contract):
    """Accepted only when its digest is the completed generation's journal evidence.

    A standalone instance is a declaration, not proof that generation ran.
    The production generation runner must verify these parents before completion.
    """

    version: Literal["ai09-campaign-generated-parent-receipt-1.0.0"] = (
        "ai09-campaign-generated-parent-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    source_recipe_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    source: PhysicalSourceSpec
    runtime: PreparationRuntime
    holdout_freshness_qualified: FalseFlag = False
    resource_qualified: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    def content_sha256(self) -> str:
        document = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(document))
        return canonical_sha256(document)


class CampaignDevelopmentExportReceipt(Contract):
    version: Literal["ai09-campaign-development-export-receipt-1.0.0"] = (
        "ai09-campaign-development-export-receipt-1.0.0"
    )
    audit_scope: Literal["cooperating_local_runner_ordering_and_budget"] = (
        "cooperating_local_runner_ordering_and_budget"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignDevelopmentExportPlan
    generated_parent_receipt_sha256: Sha256
    recipe: PhysicalForecastRecipe
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    manifest_sha256: Sha256
    runtime_code_sha256: Sha256
    snapshot_inventory_sha256: Sha256
    curated_inventory_sha256: Sha256
    logical_curated_sha256: Sha256
    population_rows: Annotated[int, Field(ge=5)]
    artifact_bytes: Annotated[int, Field(ge=1)]
    holdout_freshness_qualified: FalseFlag = False
    resource_qualified: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.recipe != self.plan.bind(self.recipe.source):
            raise ValueError("campaign_export_receipt_recipe_mismatch")
        if (
            self.population_rows > self.recipe.max_population_rows
            or self.artifact_bytes > self.recipe.max_artifact_bytes
        ):
            raise ValueError("campaign_export_receipt_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        document = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(document))
        return canonical_sha256(document)
