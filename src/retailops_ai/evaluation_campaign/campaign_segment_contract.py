"""Source-bound evaluation context and complete critical-population census.

These declarations do not prove an audited source read. Context and scenario
annotations are evaluation metadata, never additional model inputs.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    FalseFlag,
    ForecastKey,
    Sha256,
    SourceID,
    Symbol,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import EvaluationRole
from retailops_ai.evaluation_campaign.label_contract import EligibilityReason

Dimension = Literal[
    "global",
    "horizon",
    "category",
    "channel",
    "volume",
    "scenario",
    "history",
    "availability",
    "inventory",
    "lead_time",
    "intermittency",
    "anomaly",
]
Scenario = Literal["normal", "promotion", "demand_shock", "inventory_constraint"]
Anomaly = Literal[
    "unannotated",
    "clean_control",
    "one_day_spike",
    "multi_day_spike",
    "sustained_drop",
    "return_spike",
    "inventory_censored_episode",
]
Availability = Literal[
    "complete",
    "missing_history",
    "late_history",
    "missing_known_plan",
    "other_missing_input",
]


class CampaignForecastSegmentPolicy(Contract):
    version: Literal["ai09-complete-forecast-segments-1.0.0"] = (
        "ai09-complete-forecast-segments-1.0.0"
    )
    category_inventory: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=256)]
    categories: Literal["complete_verified_source_catalog_not_selected_from_outcomes"] = (
        "complete_verified_source_catalog_not_selected_from_outcomes"
    )
    volume_basis: Literal["origin_known_rolling_28_mean_not_target_outcome"] = (
        "origin_known_rolling_28_mean_not_target_outcome"
    )
    volume_boundaries: tuple[Literal[0], Literal[5], Literal[20]] = (0, 5, 20)
    intermittency_basis: Literal[
        "known_open_history_days_missing_or_closed_are_not_sales_zeros"
    ] = "known_open_history_days_missing_or_closed_are_not_sales_zeros"
    late_observation_hours: Annotated[int, Field(ge=1, le=168)] = 24
    maximum_inventory_snapshot_age_hours: Literal[24] = 24
    inventory_basis: Literal["origin_known_unreserved_available_quantity_not_future_stock"] = (
        "origin_known_unreserved_available_quantity_not_future_stock"
    )
    lead_time_basis: Literal["origin_known_supplier_quote_not_realized_delivery_or_truth"] = (
        "origin_known_supplier_quote_not_realized_delivery_or_truth"
    )
    lead_time_boundaries: tuple[Literal[2], Literal[7]] = (2, 7)
    scenario_basis: Literal["source_bound_preregistered_annotation_never_actual_target"] = (
        "source_bound_preregistered_annotation_never_actual_target"
    )
    availability_membership: Literal["independent_boolean_strata_overlap_not_a_partition"] = (
        "independent_boolean_strata_overlap_not_a_partition"
    )
    max_rows: Annotated[int, Field(ge=1, le=100000000)] = 10000000
    max_segments: Annotated[int, Field(ge=60, le=4096)] = 512
    final_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_categories(self) -> Self:
        if self.category_inventory != tuple(sorted(set(self.category_inventory))):
            raise ValueError("campaign_segments_sorted_complete_category_inventory_required")
        if len(self.category_inventory) + 55 > self.max_segments:
            raise ValueError("campaign_segments_required_inventory_exceeds_budget")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastContextScope(Contract):
    data_seed: Literal[42, 137, 2026]
    role: EvaluationRole
    dataset_id: Annotated[
        str, Field(pattern=r"^ai09-(physical|final)-forecast-sha256-[0-9a-f]{64}$")
    ]
    source_recipe_sha256: Sha256
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    source_scenario_plan_sha256: Sha256 | None
    segment_policy_sha256: Sha256

    @model_validator(mode="after")
    def role_seed(self) -> Self:
        if (self.role == "final_test") != self.dataset_id.startswith("ai09-final-") or (
            self.role == "development_evaluation" and self.data_seed != 42
        ):
            raise ValueError("campaign_context_dataset_role_or_seed_mismatch")
        return self

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignContextStoragePolicy(Contract):
    """Prospective AI09 limits; the closed AI08 storage wire is unchanged."""

    version: Literal["ai09-source-context-disk-facts-1.0.0"] = (
        "ai09-source-context-disk-facts-1.0.0"
    )
    max_parent_rows: Annotated[int, Field(ge=1, le=100000000)] = 20000000
    max_parent_bytes: Annotated[int, Field(ge=1, le=64 * 1024**3)] = 2 * 1024**3
    max_parent_files: Annotated[int, Field(ge=1, le=100000)] = 10000
    max_index_bytes: Annotated[int, Field(ge=4096, le=16 * 1024**3)] = 2 * 1024**3
    max_selected_rows: Annotated[int, Field(ge=1, le=1000000)] = 100000
    max_selected_bytes: Annotated[int, Field(ge=1, le=512 * 1024**2)] = 64 * 1024**2
    batch_rows: Annotated[int, Field(ge=1, le=512)] = 256
    cache_kib: Literal[8192] = 8192
    supported_curated_versions: tuple[Literal["1.1.0"], Literal["1.2.0"]] = ("1.1.0", "1.2.0")
    retention: Literal["private_disposable_all_versions_original_order"] = (
        "private_disposable_all_versions_original_order"
    )
    projection: Literal["closed_ai08_origin_pit_feature_point_unchanged"] = (
        "closed_ai08_origin_pit_feature_point_unchanged"
    )
    series_index: Literal["closed_ai08_fact_index_one_physical_series_clip_again_per_origin"] = (
        "closed_ai08_fact_index_one_physical_series_clip_again_per_origin"
    )
    maximum_cached_physical_series: Literal[1] = 1
    audited_source_read_proved_by_this_policy: FalseFlag = False
    final_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignSourceKeyAnnotation(ForecastKey):
    context_scope_sha256: Sha256
    source_scenario_plan_sha256: Sha256 | None
    scenario: Scenario
    anomaly: Anomaly
    data_class: Literal["evaluation_annotation_never_model_input"] = (
        "evaluation_annotation_never_model_input"
    )
    effect_independence_claimed: FalseFlag = False

    @model_validator(mode="after")
    def planned_effect(self) -> Self:
        if self.source_scenario_plan_sha256 is None and (
            self.scenario in ("demand_shock", "inventory_constraint")
            or self.anomaly != "unannotated"
        ):
            raise ValueError("campaign_context_planned_annotation_has_no_source_plan")
        return self


class CampaignOriginRoute(Contract):
    product_id: Symbol
    selling_location_id: Symbol
    channel: Literal["store", "online"]
    stock_location_id: Symbol
    source_record_sha256: Sha256
    available_at: UtcTime
    effective_from: date
    effective_to: date

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.effective_from >= self.effective_to:
            raise ValueError("campaign_context_invalid_origin_route_interval")
        return self


class CampaignForecastKeyContext(ForecastKey):
    context_scope_sha256: Sha256
    role: EvaluationRole
    example_sha256: Sha256
    feature_row_sha256: Sha256
    history_context_sha256: Sha256
    origin_inventory_feature_sha256: Sha256 | None
    origin_route_sha256: Sha256 | None
    eligible: bool
    exclusion_reasons: tuple[EligibilityReason, ...]
    category: Symbol | None
    origin_rolling_28_mean: Annotated[float, Field(ge=0)] | None
    history_state: Literal["cold_start", "insufficient_known_history", "established"]
    availability: Annotated[tuple[Availability, ...], Field(min_length=1, max_length=4)]
    inventory_state: Literal["known_positive", "known_zero", "stale", "unknown"]
    origin_available_qty: Annotated[int, Field(ge=0)] | None
    origin_snapshot_age_hours: Annotated[float, Field(ge=0)] | None
    origin_quoted_lead_time_days: Annotated[float, Field(ge=0)] | None
    intermittency: Literal[
        "all_zero_known_open_history",
        "intermittent_known_open_history",
        "positive_on_all_known_open_days",
        "incomplete_history",
        "no_known_open_days",
    ]
    annotation: CampaignSourceKeyAnnotation
    data_class: Literal["evaluation_context_never_model_input"] = (
        "evaluation_context_never_model_input"
    )
    audited_source_read_proved_by_this_row: FalseFlag = False
    final_access_authorized: FalseFlag = False

    @model_validator(mode="after")
    def population_binding(self) -> Self:
        key = set(ForecastKey.model_fields)
        if (
            self.eligible != (not self.exclusion_reasons)
            or len(set(self.exclusion_reasons)) != len(self.exclusion_reasons)
            or self.annotation.context_scope_sha256 != self.context_scope_sha256
            or self.model_dump(include=key) != self.annotation.model_dump(include=key)
            or self.availability != tuple(sorted(set(self.availability)))
            or "complete" in self.availability
            and len(self.availability) != 1
            or self.origin_inventory_feature_sha256 is not None
            and self.origin_route_sha256 is None
            or self.origin_inventory_feature_sha256 is None
            and any(
                v is not None
                for v in (
                    self.origin_available_qty,
                    self.origin_snapshot_age_hours,
                    self.origin_quoted_lead_time_days,
                )
            )
        ):
            raise ValueError("campaign_context_key_or_metadata_binding_mismatch")
        expected = (
            "unknown"
            if self.origin_available_qty is None or self.origin_snapshot_age_hours is None
            else "stale"
            if self.origin_snapshot_age_hours > 24
            else "known_zero"
            if self.origin_available_qty == 0
            else "known_positive"
        )
        if self.inventory_state != expected:
            raise ValueError("campaign_context_inventory_state_mismatch")
        return self


def required_population_ids(
    policy: CampaignForecastSegmentPolicy,
) -> tuple[tuple[Dimension, str], ...]:
    values: dict[Dimension, tuple[str, ...]] = {
        "global": ("all",),
        "horizon": tuple(str(n) for n in range(1, 15)),
        "category": (*policy.category_inventory, "[missing]"),
        "channel": ("store", "online"),
        "volume": ("zero", "low", "medium", "high", "unknown"),
        "scenario": ("normal", "promotion", "demand_shock", "inventory_constraint"),
        "history": ("cold_start", "insufficient_known_history", "established"),
        "availability": (
            "complete",
            "missing_history",
            "late_history",
            "missing_known_plan",
            "other_missing_input",
        ),
        "inventory": ("known_positive", "known_zero", "stale", "unknown"),
        "lead_time": ("le_2_days", "gt_2_le_7_days", "gt_7_days", "unknown"),
        "intermittency": (
            "all_zero_known_open_history",
            "intermittent_known_open_history",
            "positive_on_all_known_open_days",
            "incomplete_history",
            "no_known_open_days",
        ),
        "anomaly": (
            "unannotated",
            "clean_control",
            "one_day_spike",
            "multi_day_spike",
            "sustained_drop",
            "return_spike",
            "inventory_censored_episode",
        ),
    }
    return tuple(sorted((d, v) for d, items in values.items() for v in items))


class CampaignSegmentPopulation(Contract):
    dimension: Dimension
    value: Annotated[str, Field(min_length=1, max_length=256)]
    rows: Annotated[int, Field(ge=0)]
    eligible_rows: Annotated[int, Field(ge=0)]
    exclusion_reasons: dict[EligibilityReason, Annotated[int, Field(ge=1)]]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.eligible_rows > self.rows or any(
            n > self.rows - self.eligible_rows for n in self.exclusion_reasons.values()
        ):
            raise ValueError("campaign_segment_population_counts_mismatch")
        return self


class CampaignForecastSegmentCensus(Contract):
    version: Literal["ai09-complete-forecast-segment-census-1.0.0"] = (
        "ai09-complete-forecast-segment-census-1.0.0"
    )
    scope: CampaignForecastContextScope
    policy: CampaignForecastSegmentPolicy
    rows: Annotated[int, Field(ge=0)]
    eligible_rows: Annotated[int, Field(ge=0)]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    context_trace_sha256: Sha256
    populations: Annotated[
        tuple[CampaignSegmentPopulation, ...], Field(min_length=1, max_length=4096)
    ]
    complete_declared_context_census: TrueFlag = True
    audited_source_read_proved_by_this_document: FalseFlag = False
    metric_gates_evaluated: FalseFlag = False
    quality_qualified: FalseFlag = False
    final_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def population(self) -> Self:
        ids = tuple((p.dimension, p.value) for p in self.populations)
        global_population = next(
            (p for p in self.populations if (p.dimension, p.value) == ("global", "all")), None
        )
        if (
            self.scope.segment_policy_sha256 != self.policy.content_sha256()
            or ids != tuple(sorted(set(ids)))
            or ids != required_population_ids(self.policy)
            or self.rows > self.policy.max_rows
            or len(ids) > self.policy.max_segments
            or self.eligible_rows > self.rows
            or global_population is None
            or (
                global_population.rows,
                global_population.eligible_rows,
                global_population.keys_sha256,
                global_population.eligible_keys_sha256,
            )
            != (self.rows, self.eligible_rows, self.keys_sha256, self.eligible_keys_sha256)
            or any(
                p.rows > self.rows or p.eligible_rows > self.eligible_rows for p in self.populations
            )
        ):
            raise ValueError("campaign_segment_census_inventory_mismatch")
        for dimension in {p.dimension for p in self.populations} - {"availability"}:
            groups = [p for p in self.populations if p.dimension == dimension]
            if (sum(p.rows for p in groups), sum(p.eligible_rows for p in groups)) != (
                self.rows,
                self.eligible_rows,
            ):
                raise ValueError("campaign_segment_census_partition_counts_mismatch")
            reasons: dict[EligibilityReason, int] = {}
            for group in groups:
                for reason, count in group.exclusion_reasons.items():
                    reasons[reason] = reasons.get(reason, 0) + count
            if reasons != global_population.exclusion_reasons:
                raise ValueError("campaign_segment_census_partition_exclusions_mismatch")
        availability = [p for p in self.populations if p.dimension == "availability"]
        complete = next(p for p in availability if p.value == "complete")
        if not (
            self.rows
            <= sum(p.rows for p in availability)
            <= complete.rows + 4 * (self.rows - complete.rows)
            and self.eligible_rows
            <= sum(p.eligible_rows for p in availability)
            <= complete.eligible_rows + 4 * (self.eligible_rows - complete.eligible_rows)
        ):
            raise ValueError("campaign_segment_census_availability_membership_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
