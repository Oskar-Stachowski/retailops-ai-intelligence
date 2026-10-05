"""Exact prospective final campaign and separately recorded owner permission."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    Sha256,
    SourceID,
    Symbol,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.stockout.split import SplitPolicy
from retailops_ai.stockout_selection.contract import QualityRequirements

CampaignID = Annotated[str, Field(pattern=r"^stockout-final-campaign-sha256-[0-9a-f]{64}$")]
GitSHA = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class BalancedQualityRequirements(QualityRequirements):
    """Prospectively accepted calibration warning, solely for small categories."""

    version: Literal["stockout-balanced-quality-2.0.0"] = "stockout-balanced-quality-2.0.0"
    small_category_rows_lt: Literal[100] = 100
    small_category_warning_maximum_ece: Annotated[float, Field(ge=0.2, le=0.2)] = 0.2
    warning_scope: Literal["category_only_AP_Brier_and_support_must_pass"] = (
        "category_only_AP_Brier_and_support_must_pass"
    )


class SourceRef(Contract):
    world: Literal["matching", "future_stress"]
    seed: Literal[42, 137, 2026]
    workflow_run_id: Annotated[int, Field(gt=0)]
    artifact_id: Annotated[int, Field(gt=0)]
    artifact_bytes: Annotated[int, Field(gt=0, le=512 * 1024**2)]
    artifact_sha256: Sha256
    checkpoint_sha256: Sha256
    resource_sha256: Sha256
    producer_commit: GitSHA
    consumer_commit: Literal["4faaf4b6c1997fda3a609645595165643bf302a9"]
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    qualification_id: Annotated[str, Field(pattern=r"^inventory-labels-sha256-[0-9a-f]{64}$")]
    feature_bundle_id: Annotated[str, Field(pattern=r"^feature-partitions-sha256-[0-9a-f]{64}$")]
    upstream_bundle_id: Annotated[str, Field(pattern=r"^upstream-partitions-sha256-[0-9a-f]{64}$")]
    label_bundle_id: Annotated[str, Field(pattern=r"^label-partitions-sha256-[0-9a-f]{64}$")]
    temporal_bundle_id: Annotated[str, Field(pattern=r"^temporal-partitions-sha256-[0-9a-f]{64}$")]
    eligible_test_membership: Annotated[int, Field(ge=1, le=10000)]
    split_policy: SplitPolicy

    @model_validator(mode="after")
    def source_version(self) -> Self:
        expected = (
            "08639e9188badb352ed64686a088fe237badad41"
            if self.world == "matching"
            else "61eb215193106cc3f41e6b79e0470585cc9e791b"
        )
        if self.producer_commit != expected:
            raise ValueError("stockout_final_source_producer_pin")
        return self


class ScenarioPolicy(Contract):
    version: Literal["stockout-final-scenarios-1.0.0"] = "stockout-final-scenarios-1.0.0"
    promotion: Literal["known_active_plan_and_known_assortment_route_intersection_in_next7days"] = (
        "known_active_plan_and_known_assortment_route_intersection_in_next7days"
    )
    demand_shock: Literal["future_world_SHA256_product_mod3_zero_window_2026_07_28_to_08_03"] = (
        "future_world_SHA256_product_mod3_zero_window_2026_07_28_to_08_03"
    )
    inventory_constraint: Literal["PIT_history_constrained_days_positive"] = (
        "PIT_history_constrained_days_positive"
    )
    normal: Literal["no_promotion_no_shock_no_history_constraint"] = (
        "no_promotion_no_shock_no_history_constraint"
    )
    controls: Literal["same_calendar_window_untreated_SKUs_and_unconstrained_history"] = (
        "same_calendar_window_untreated_SKUs_and_unconstrained_history"
    )
    overlaps: Literal["explicit_counts_no_disjoint_or_causal_effect_claim"] = (
        "explicit_counts_no_disjoint_or_causal_effect_claim"
    )
    required_scenarios_world: Literal["future_stress_each_seed"] = "future_stress_each_seed"


class CampaignFreeze(Contract):
    version: Literal["stockout-final-campaign-1.0.0", "stockout-final-campaign-2.0.0"] = (
        "stockout-final-campaign-1.0.0"
    )
    campaign_id: CampaignID
    prepared_at: UtcTime
    sources: tuple[SourceRef, ...] = Field(min_length=6, max_length=6)
    selection_id: Annotated[str, Field(pattern=r"^stockout-selection-sha256-[0-9a-f]{64}$")]
    selection_content_sha256: Sha256
    recipe_content_sha256: Sha256
    policy_content_sha256: Sha256
    evaluator_code_sha256: Sha256
    dependency_lock_sha256: Sha256
    expected_categories: tuple[Symbol, ...] = Field(min_length=8, max_length=8)
    expected_stock_locations: tuple[Symbol, ...] = Field(min_length=2, max_length=2)
    train_positives: Literal[550] = 550
    train_rows: Literal[1305] = 1305
    model_refits_permitted: Literal[0] = 0
    recalibration_permitted: Literal[False] = False
    thresholds_change_permitted: Literal[False] = False
    quality_requirements: QualityRequirements | BalancedQualityRequirements = Field(
        default_factory=QualityRequirements
    )
    scenarios: ScenarioPolicy = Field(default_factory=ScenarioPolicy)
    worlds: Literal["separate_reports_never_pool_repeated_physical_keys"] = (
        "separate_reports_never_pool_repeated_physical_keys"
    )
    capacity: Literal["one_global_physical_origin_budget_before_any_segment_projection"] = (
        "one_global_physical_origin_budget_before_any_segment_projection"
    )
    approval_status: Literal["requires_separate_owner_permission"] = (
        "requires_separate_owner_permission"
    )

    @model_validator(mode="after")
    def identity(self) -> Self:
        required = {(w, seed) for w in ("matching", "future_stress") for seed in (42, 137, 2026)}
        if (
            {(s.world, s.seed) for s in self.sources} != required
            or len({s.source_dataset_id for s in self.sources}) != 6
            or len({s.artifact_id for s in self.sources}) != 6
            or self.expected_categories != tuple(sorted(set(self.expected_categories)))
            or self.expected_stock_locations != tuple(sorted(set(self.expected_stock_locations)))
            or self.quality_requirements
            != (
                BalancedQualityRequirements()
                if self.version == "stockout-final-campaign-2.0.0"
                else QualityRequirements()
            )
            or self.campaign_id
            != "stockout-final-campaign-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"campaign_id"}))
        ):
            raise ValueError("stockout_final_campaign_identity_sources_or_gates")
        return self


class CampaignPermission(Contract):
    version: Literal["stockout-final-permission-1.0.0", "stockout-final-permission-2.0.0"] = (
        "stockout-final-permission-1.0.0"
    )
    campaign_id: CampaignID
    approved_by: Symbol
    approved_at: UtcTime
    authorization_evidence: Annotated[str, Field(min_length=1, max_length=2048)]
    final_outcome_access: Literal[True]
    thresholds_and_capacity_approved: Literal[True]
    model_refits_permitted: Literal[0] = 0
    promotion_authorized: Literal[False] = False
    small_category_warnings_approved: bool = False


def require_permission(freeze: CampaignFreeze, permission: CampaignPermission | None) -> None:
    freeze = CampaignFreeze.model_validate_json(freeze.model_dump_json())
    if permission is None:
        raise ValueError("stockout_final_owner_permission_required")
    permission = CampaignPermission.model_validate_json(permission.model_dump_json())
    balanced = freeze.version == "stockout-final-campaign-2.0.0"
    if (
        permission.campaign_id != freeze.campaign_id
        or permission.approved_at < freeze.prepared_at
        or permission.version
        != ("stockout-final-permission-2.0.0" if balanced else "stockout-final-permission-1.0.0")
        or permission.small_category_warnings_approved != balanced
    ):
        raise ValueError("stockout_final_permission_campaign_or_time_mismatch")
