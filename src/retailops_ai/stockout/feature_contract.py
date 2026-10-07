"""Causal inventory features; no labels or fitted preprocessing in their schema."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.stockout.contract import PhysicalKey

Nonnegative = Annotated[StrictInt, Field(ge=0)]
Rate = Annotated[StrictFloat, Field(ge=0)]
FeatureTable = Literal[
    "assortment",
    "delivery_plan_versions",
    "daily_demand_versions",
    "fulfillment_routes",
    "inventory_daily_snapshots",
    "inventory_history_coverage",
    "inventory_ledger",
    "product_catalog",
    "product_suppliers",
    "replenishment_orders",
    "replenishment_receipts",
]


class FeaturePolicy(Contract):
    version: Literal["stockout-pit-features-1.0.0"] = "stockout-pit-features-1.0.0"
    history_days: Literal[28] = 28
    minimum_known_days: Annotated[StrictInt, Field(ge=1, le=28)] = 7
    max_snapshot_age_seconds: Annotated[StrictInt, Field(ge=0, le=86400)] = 86400
    sales_measure: Literal["observed_sales_units_not_latent_demand"] = (
        "observed_sales_units_not_latent_demand"
    )
    stock_measure: Literal["available_qty_unreserved"] = "available_qty_unreserved"
    censored_sales_policy: Literal["separate_all_sales_and_verified_in_stock_days"] = (
        "separate_all_sales_and_verified_in_stock_days"
    )
    preprocessing: Literal["none_fit_only_in_training_later"] = "none_fit_only_in_training_later"


DEFAULT_FEATURE_POLICY = FeaturePolicy()


class DailyHistory(Contract):
    business_date: date
    observed_units: Nonnegative | None
    inventory_day_verified: StrictBool
    in_stock_all_day: StrictBool | None
    stockout_onsets: Nonnegative | None

    @model_validator(mode="after")
    def inventory_coverage(self) -> Self:
        if self.inventory_day_verified != (
            self.in_stock_all_day is not None and self.stockout_onsets is not None
        ):
            raise ValueError("stockout_history_coverage_mismatch")
        return self


class FeatureValues(Contract):
    available_qty: Nonnegative | None
    snapshot_age_hours: Rate | None
    history_known_days: Nonnegative
    history_missing_days: Nonnegative
    observed_sales_mean: Rate | None
    observed_sales_std: Rate | None
    in_stock_sales_mean: Rate | None
    history_in_stock_days: Nonnegative
    history_constrained_days: Nonnegative
    history_inventory_unknown_days: Nonnegative
    historical_stockout_onsets: Nonnegative | None
    days_of_supply_observed: Rate | None
    days_of_supply_in_stock: Rate | None
    open_order_quantity: Nonnegative
    due_within_7d_quantity: Nonnegative
    overdue_order_quantity: Nonnegative
    next_expected_delivery_hours: Rate | None
    quoted_lead_time_days: Rate | None


class FeatureLineage(Contract):
    table: FeatureTable
    rows: Nonnegative
    source_records_sha256: Sha256
    max_available_at: UtcTime | None


class FeaturePoint(PhysicalKey):
    as_of: UtcTime
    status: Literal["eligible", "already_stockout", "insufficient_data"]
    reason: str | None
    feature_available_at: UtcTime | None
    values: FeatureValues
    history: tuple[DailyHistory, ...] = Field(min_length=28, max_length=28)
    lineage: tuple[FeatureLineage, ...]

    @model_validator(mode="after")
    def causal(self) -> Self:
        if (
            self.feature_available_at is not None and self.feature_available_at > self.as_of
        ) or any(
            r.max_available_at is not None and r.max_available_at > self.as_of for r in self.lineage
        ):
            raise ValueError("stockout_feature_available_after_origin")
        dates = tuple(h.business_date for h in self.history)
        if dates != tuple(self.as_of.date() - timedelta(days=n) for n in range(27, -1, -1)):
            raise ValueError("stockout_history_order_or_future_date")
        if (
            self.values.history_known_days
            != sum(h.observed_units is not None for h in self.history)
            or self.values.history_known_days + self.values.history_missing_days != 28
            or self.values.history_in_stock_days
            != sum(
                h.observed_units is not None and h.in_stock_all_day is True for h in self.history
            )
            or self.values.history_constrained_days
            != sum(h.in_stock_all_day is False for h in self.history)
            or self.values.history_inventory_unknown_days
            != sum(not h.inventory_day_verified for h in self.history)
        ):
            raise ValueError("stockout_feature_history_counts_mismatch")
        maximum = max(
            (r.max_available_at for r in self.lineage if r.max_available_at is not None),
            default=None,
        )
        if self.feature_available_at != maximum or len({r.table for r in self.lineage}) != len(
            self.lineage
        ):
            raise ValueError("stockout_feature_lineage_mismatch")
        if (self.status == "insufficient_data") != (self.reason is not None):
            raise ValueError("stockout_feature_status_reason_mismatch")
        if self.status == "already_stockout" and self.values.available_qty != 0:
            raise ValueError("current_stockout_requires_known_zero")
        if self.status == "eligible" and (
            self.values.available_qty is None
            or self.values.available_qty == 0
            or self.feature_available_at is None
        ):
            raise ValueError("incident_features_require_positive_stock")
        return self
