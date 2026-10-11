"""Full-population stockout threshold/capacity arithmetic without hidden fits.

The caller verifies original predictions, mature membership, model identity,
role and journal authority. This numerical primitive grants no data access,
threshold approval or independent quality acceptance.
"""

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_policy.assessment import capacity_selection, confusion
from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    OperatorCapacity,
    PolicyRow,
    RiskThresholds,
    risk_band,
)


class CampaignStockoutAssessmentPolicy(Contract):
    version: Literal["ai09-full-stockout-assessment-1.0.0"] = "ai09-full-stockout-assessment-1.0.0"
    thresholds: RiskThresholds
    capacity: OperatorCapacity
    costs: AttentionCosts
    expected_categories: Annotated[
        tuple[Symbol | Literal["__unknown__"], ...], Field(min_length=1, max_length=512)
    ]
    expected_stock_locations: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=128)]
    max_rows: Annotated[int, Field(ge=1, le=1_000_000)] = 1_000_000
    max_input_bytes: Annotated[int, Field(ge=1024, le=1024**3)] = 512 * 1024**2
    max_origins: Annotated[int, Field(ge=1, le=10000)] = 2000
    population: Literal["all_verified_eligible_physical_keys_no_sampling"] = (
        "all_verified_eligible_physical_keys_no_sampling"
    )
    queue: Literal["one_global_capacity_per_origin_shared_by_all_segments"] = (
        "one_global_capacity_per_origin_shared_by_all_segments"
    )

    @model_validator(mode="after")
    def complete_inventory(self) -> Self:
        for values in (self.expected_categories, self.expected_stock_locations):
            if values != tuple(sorted(set(values))):
                raise ValueError("campaign_stockout_assessment_sorted_unique_scope_required")
        return self


def assess_full_stockout(
    rows: Iterable[PolicyRow], policy: CampaignStockoutAssessmentPolicy
) -> dict[str, Any]:
    # Python validation must precede JSON serialization: a copied boolean in
    # an integer field can be serialized as 1, losing the original invalid type.
    policy = CampaignStockoutAssessmentPolicy.model_validate(policy.model_dump(mode="python"))
    checked: list[PolicyRow] = []
    keys = set()
    origins = set()
    trace, key_trace = hashlib.sha256(), hashlib.sha256()
    total_bytes = 0
    categories, stocks = set(policy.expected_categories), set(policy.expected_stock_locations)
    for incoming in rows:
        if len(checked) >= policy.max_rows:
            raise ValueError("campaign_stockout_assessment_row_budget")
        row = PolicyRow.model_validate(incoming.model_dump(mode="python"))
        serialized = row.model_dump(mode="json")
        raw = canonical_bytes(serialized)
        total_bytes += len(raw) + 1
        if total_bytes > policy.max_input_bytes:
            raise ValueError("campaign_stockout_assessment_byte_budget")
        if row.category_id not in categories or row.stock_location_id not in stocks:
            raise ValueError("campaign_stockout_assessment_unexpected_physical_scope")
        key = row.product_id, row.stock_location_id, row.as_of
        if key in keys:
            raise ValueError("campaign_stockout_assessment_duplicate_UTC_physical_key")
        keys.add(key)
        origins.add(row.as_of)
        if len(origins) > policy.max_origins:
            raise ValueError("campaign_stockout_assessment_origin_budget")
        trace.update(raw + b"\n")
        key_trace.update(
            canonical_bytes([row.product_id, row.stock_location_id, serialized["as_of"]]) + b"\n"
        )
        checked.append(row)
    if not checked:
        raise ValueError("campaign_stockout_assessment_empty_population")
    selected, origin_counts = capacity_selection(checked, policy.capacity)
    groups: dict[str, list[PolicyRow]] = defaultdict(list)
    for row in checked:
        groups["category:" + row.category_id].append(row)
        groups["stock_location:" + row.stock_location_id].append(row)
        groups[
            "historical_inventory_constraint:" + str(row.historical_inventory_constraint).lower()
        ].append(row)
    required = {
        *("category:" + category for category in policy.expected_categories),
        *("stock_location:" + stock for stock in policy.expected_stock_locations),
        "historical_inventory_constraint:true",
        "historical_inventory_constraint:false",
    }
    thresholds = {}
    for name in ("medium_from", "high_from", "critical_from"):
        threshold = getattr(policy.thresholds, name)
        flagged = {
            (row.product_id, row.stock_location_id, row.as_of.isoformat())
            for row in checked
            if row.probability >= threshold
        }
        thresholds[name] = {"threshold": threshold, **confusion(checked, flagged, policy.costs)}
    bands = Counter(risk_band(row.probability, policy.thresholds) for row in checked)
    return dict(
        version="ai09-full-stockout-assessment-receipt-1.0.0",
        policy_sha256=canonical_sha256(policy.model_dump(mode="json")),
        policy=policy.model_dump(mode="json"),
        rows=len(checked),
        input_bytes=total_bytes,
        ordered_points_sha256=trace.hexdigest(),
        ordered_keys_sha256=key_trace.hexdigest(),
        capacity={**confusion(checked, selected, policy.costs), "origins": origin_counts},
        threshold_costs=thresholds,
        risk_band_counts={band: bands[band] for band in ("low", "medium", "high", "critical")},
        segments_at_same_global_capacity={
            name: confusion(groups.get(name, []), selected, policy.costs)
            for name in sorted(required)
        },
        missing_required_groups=sorted(required - set(groups)),
        statuses_excluded=["already_stockout", "insufficient_data", "stale_input"],
        probability_is_not_unconstrained_demand=True,
        serving_eligible=False,
        thresholds_approved=False,
        membership_and_predictions_verified=False,
        journal_authorization_proven=False,
        quality_qualified=False,
        stage_ready=False,
    )
