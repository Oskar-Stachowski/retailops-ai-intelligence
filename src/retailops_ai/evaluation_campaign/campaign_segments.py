"""Point-in-time context derivation and bounded full critical-population census.

The source exporter must independently seal and audit all input bindings. These
pure components do not authorize source reads, outcomes, models or final access.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from retailops_ai.data_contracts.common import ForecastKey, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    Availability,
    CampaignForecastContextScope,
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
    CampaignForecastSegmentPolicy,
    CampaignOriginRoute,
    CampaignSegmentPopulation,
    CampaignSourceKeyAnnotation,
    Dimension,
    required_population_ids,
)
from retailops_ai.evaluation_campaign.label_contract import EligibilityReason
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.stockout.feature_contract import FeaturePoint
from retailops_ai.stockout.features import FEATURE_TABLES


def context_from_inputs(
    feature: InputRow,
    history: HistoryContext,
    inventory: FeaturePoint | None,
    route: CampaignOriginRoute | None,
    annotation: CampaignSourceKeyAnnotation,
    *,
    scope: CampaignForecastContextScope,
    policy: CampaignForecastSegmentPolicy,
    example_sha256: str,
    eligible: bool,
    exclusion_reasons: tuple[EligibilityReason, ...],
) -> CampaignForecastKeyContext:
    """Use existing AI08 PIT feature semantics without changing its projection."""
    feature = InputRow.model_validate_json(feature.model_dump_json())
    history = HistoryContext.model_validate_json(history.model_dump_json())
    scope = CampaignForecastContextScope.model_validate_json(scope.model_dump_json())
    policy = CampaignForecastSegmentPolicy.model_validate_json(policy.model_dump_json())
    annotation = CampaignSourceKeyAnnotation.model_validate_json(annotation.model_dump_json())
    scope_hash = scope.content_sha256()
    if (
        scope.segment_policy_sha256 != policy.content_sha256()
        or history.content_sha256() != feature.history_context_sha256
        or (
            history.product_id,
            history.selling_location_id,
            history.channel,
            history.forecast_origin,
        )
        != (
            feature.product_id,
            feature.selling_location_id,
            feature.channel,
            feature.forecast_origin,
        )
        or membership_key(annotation) != membership_key(feature)
        or annotation.context_scope_sha256 != scope_hash
        or annotation.source_scenario_plan_sha256 != scope.source_scenario_plan_sha256
        or (inventory is not None and route is None)
        or (
            feature.history_active_days,
            feature.history_known_days,
            feature.history_missing_days,
            feature.history_closed_days,
        )
        != (
            len(history.points),
            sum(p.status != "missing" for p in history.points),
            sum(p.status == "missing" for p in history.points),
            sum(p.status == "closed" for p in history.points),
        )
    ):
        raise SnapshotError("campaign_context_source_feature_history_or_annotation_mismatch")
    inventory_hash = route_hash = None
    quantity = age = quote = None
    if route is not None:
        route = CampaignOriginRoute.model_validate_json(route.model_dump_json())
        if (
            (route.product_id, route.selling_location_id, route.channel)
            != (feature.product_id, feature.selling_location_id, feature.channel)
            or route.available_at > feature.forecast_origin
            or not route.effective_from <= feature.forecast_origin.date() < route.effective_to
        ):
            raise SnapshotError("campaign_context_route_not_known_or_effective_at_origin")
        route_hash = canonical_sha256(route.model_dump(mode="json"))
        if inventory is not None:
            inventory = FeaturePoint.model_validate_json(inventory.model_dump_json())
            if (inventory.product_id, inventory.stock_location_id, inventory.as_of) != (
                feature.product_id,
                route.stock_location_id,
                feature.forecast_origin,
            ) or set(item.table for item in inventory.lineage) != set(FEATURE_TABLES):
                raise SnapshotError("campaign_context_inventory_point_scope_or_lineage_mismatch")
            inventory_hash = canonical_sha256(inventory.model_dump(mode="json"))
            quantity = inventory.values.available_qty
            age = inventory.values.snapshot_age_hours
            quote = inventory.values.quoted_lead_time_days
    inventory_state: Literal["known_positive", "known_zero", "stale", "unknown"] = (
        "unknown"
        if quantity is None or age is None
        else "stale"
        if age > 24
        else "known_zero"
        if quantity == 0
        else "known_positive"
    )
    values = {v.name: v for v in feature.values}
    category = values["category_id"].value
    volume = values["rolling_mean_28"].value
    if category is not None and category not in policy.category_inventory:
        raise SnapshotError("campaign_context_category_outside_complete_source_inventory")
    if (
        category is not None
        and type(category) is not str
        or volume is not None
        and type(volume) is not float
    ):
        raise SnapshotError("campaign_context_categorical_or_volume_feature_type")
    availability: list[Availability] = []
    if feature.history_missing_days:
        availability.append("missing_history")
    if any(
        p.source_available_at is not None
        and p.source_available_at
        > end_of_day(p.business_date) + timedelta(hours=policy.late_observation_hours)
        for p in history.points
    ):
        availability.append("late_history")
    if any(v.kind == "known_plan" and v.status == "missing" for v in feature.values):
        availability.append("missing_known_plan")
    if any(v.kind != "known_plan" and v.status == "missing" for v in feature.values):
        availability.append("other_missing_input")
    if not availability:
        availability.append("complete")
    if annotation.scenario == "promotion" and values["planned_promotion_offered"].value is not True:
        raise SnapshotError("campaign_context_promotion_annotation_without_known_offer")
    known_open = [p for p in history.points if p.status in ("observed_positive", "observed_zero")]
    intermittency: Literal[
        "all_zero_known_open_history",
        "intermittent_known_open_history",
        "positive_on_all_known_open_days",
        "incomplete_history",
        "no_known_open_days",
    ] = (
        "incomplete_history"
        if feature.history_missing_days
        else "no_known_open_days"
        if not known_open
        else "all_zero_known_open_history"
        if all(p.observed_units == 0 for p in known_open)
        else "positive_on_all_known_open_days"
        if all(p.observed_units and p.observed_units > 0 for p in known_open)
        else "intermittent_known_open_history"
    )
    return CampaignForecastKeyContext(
        **feature.model_dump(include=set(ForecastKey.model_fields)),
        context_scope_sha256=scope_hash,
        role=scope.role,
        example_sha256=example_sha256,
        feature_row_sha256=canonical_sha256(feature.model_dump(mode="json")),
        history_context_sha256=feature.history_context_sha256,
        origin_inventory_feature_sha256=inventory_hash,
        origin_route_sha256=route_hash,
        eligible=eligible,
        exclusion_reasons=exclusion_reasons,
        category=category,
        origin_rolling_28_mean=volume,
        history_state="cold_start"
        if feature.history_active_days < 28
        else "insufficient_known_history"
        if feature.history_known_days < 7
        else "established",
        availability=tuple(sorted(availability)),
        inventory_state=inventory_state,
        origin_available_qty=quantity,
        origin_snapshot_age_hours=age,
        origin_quoted_lead_time_days=quote,
        intermittency=intermittency,
        annotation=annotation,
    )


def population_ids(context: CampaignForecastKeyContext) -> tuple[tuple[Dimension, str], ...]:
    mean = context.origin_rolling_28_mean
    quote = context.origin_quoted_lead_time_days
    values: dict[Dimension, str] = {
        "global": "all",
        "horizon": str(context.horizon_days),
        "category": context.category or "[missing]",
        "channel": context.channel,
        "volume": "unknown"
        if mean is None
        else "zero"
        if mean == 0
        else "low"
        if mean < 5
        else "medium"
        if mean < 20
        else "high",
        "scenario": context.annotation.scenario,
        "history": context.history_state,
        "inventory": context.inventory_state,
        "lead_time": "unknown"
        if quote is None
        else "le_2_days"
        if quote <= 2
        else "gt_2_le_7_days"
        if quote <= 7
        else "gt_7_days",
        "intermittency": context.intermittency,
        "anomaly": context.annotation.anomaly,
    }
    return tuple(sorted((*values.items(), *(("availability", s) for s in context.availability))))


@dataclass
class _Population:
    rows: int = 0
    eligible: int = 0
    reasons: Counter[EligibilityReason] = field(default_factory=Counter)
    keys: Any = field(default_factory=hashlib.sha256)
    eligible_keys: Any = field(default_factory=hashlib.sha256)

    def add(self, context: CampaignForecastKeyContext, key: bytes) -> None:
        self.rows += 1
        self.keys.update(key + b"\n")
        self.eligible += int(context.eligible)
        if context.eligible:
            self.eligible_keys.update(key + b"\n")
        self.reasons.update(context.exclusion_reasons)


class SegmentCensus:
    """Retain fixed per-group counts/hashes, including empty required groups."""

    def __init__(
        self, scope: CampaignForecastContextScope, policy: CampaignForecastSegmentPolicy
    ) -> None:
        self.scope = CampaignForecastContextScope.model_validate_json(scope.model_dump_json())
        self.policy = CampaignForecastSegmentPolicy.model_validate_json(policy.model_dump_json())
        self.scope_sha256 = self.scope.content_sha256()
        if self.scope.segment_policy_sha256 != self.policy.content_sha256():
            raise SnapshotError("campaign_context_census_policy_scope_mismatch")
        self.populations = {p: _Population() for p in required_population_ids(self.policy)}
        self.previous: bytes | None = None
        self.trace = hashlib.sha256()
        self.failed = self.complete = False

    def _available(self) -> None:
        if self.failed or self.complete:
            raise SnapshotError("campaign_context_census_stream_unavailable")

    def add(self, context: CampaignForecastKeyContext) -> None:
        self._available()
        try:
            self._add(context)
        except Exception:
            self.failed = True
            raise

    def _add(self, context: CampaignForecastKeyContext) -> None:
        context = CampaignForecastKeyContext.model_validate_json(context.model_dump_json())
        key = membership_key(context)
        if (
            context.context_scope_sha256,
            context.role,
            context.annotation.source_scenario_plan_sha256,
        ) != (self.scope_sha256, self.scope.role, self.scope.source_scenario_plan_sha256):
            raise SnapshotError("campaign_context_census_row_scope_mismatch")
        if self.previous is not None and key <= self.previous:
            raise SnapshotError("campaign_context_census_duplicate_or_unsorted_key")
        if self.populations[("global", "all")].rows >= self.policy.max_rows:
            raise SnapshotError("campaign_context_census_row_budget")
        identities = population_ids(context)
        if any(identity not in self.populations for identity in identities):
            raise SnapshotError("campaign_context_census_undeclared_segment")
        for identity in identities:
            self.populations[identity].add(context, key)
        self.trace.update(canonical_bytes(context.model_dump(mode="json")) + b"\n")
        self.previous = key

    def finish(
        self,
        *,
        expected_rows: int,
        expected_eligible_rows: int,
        expected_keys_sha256: str,
        expected_eligible_keys_sha256: str,
    ) -> CampaignForecastSegmentCensus:
        self._available()
        try:
            global_population = self.populations[("global", "all")]
            if (
                type(expected_rows) is not int
                or type(expected_eligible_rows) is not int
                or (
                    global_population.rows,
                    global_population.eligible,
                    global_population.keys.hexdigest(),
                    global_population.eligible_keys.hexdigest(),
                )
                != (
                    expected_rows,
                    expected_eligible_rows,
                    expected_keys_sha256,
                    expected_eligible_keys_sha256,
                )
            ):
                raise SnapshotError("campaign_context_census_complete_membership_mismatch")
            result = CampaignForecastSegmentCensus(
                scope=self.scope,
                policy=self.policy,
                rows=expected_rows,
                eligible_rows=expected_eligible_rows,
                keys_sha256=expected_keys_sha256,
                eligible_keys_sha256=expected_eligible_keys_sha256,
                context_trace_sha256=self.trace.hexdigest(),
                populations=tuple(
                    CampaignSegmentPopulation(
                        dimension=d,
                        value=v,
                        rows=p.rows,
                        eligible_rows=p.eligible,
                        exclusion_reasons=dict(sorted(p.reasons.items())),
                        keys_sha256=p.keys.hexdigest(),
                        eligible_keys_sha256=p.eligible_keys.hexdigest(),
                    )
                    for (d, v), p in self.populations.items()
                ),
            )
            self.complete = True
            return result
        except Exception:
            self.failed = True
            raise
