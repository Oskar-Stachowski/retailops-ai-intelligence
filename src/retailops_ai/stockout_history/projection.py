"""Frozen v1 point assembly with indexed daily history in an origin-local view."""

import hashlib
from datetime import datetime, timedelta
from math import fsum, sqrt

from retailops_ai.data_contracts.common import utc_time
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_contract import (
    DEFAULT_FEATURE_POLICY,
    DailyHistory,
    FeaturePoint,
    FeaturePolicy,
)
from retailops_ai.stockout.features import FEATURE_TABLES, Records, active_pairs, supply_features
from retailops_ai.stockout_history.index import HistoryIndex


def feature_point(
    records: Records,
    *,
    product: str,
    stock: str,
    as_of: datetime,
    policy: FeaturePolicy = DEFAULT_FEATURE_POLICY,
) -> FeaturePoint:
    """History and source plans are clipped before calculations and version selection."""
    as_of = utc_time(as_of)
    if set(records) != set(FEATURE_TABLES):
        raise ValueError("stockout_features_require_only_allowlisted_fact_tables")
    known = {}
    for table in FEATURE_TABLES:
        selected = []
        for row in records[table]:
            available = row["curated_available_at"]
            if available is None or available > as_of:
                continue
            if row.get("product_id", product) != product:
                continue
            if table == "product_catalog" and row["id"] != product:
                continue
            if (
                table not in {"fulfillment_routes", "daily_demand_versions"}
                and row.get("stock_location_id", stock) != stock
            ):
                continue
            if any(
                row.get(k) is not None and row[k] > as_of
                for k in ("occurred_at", "snapshot_at", "ordered_at", "received_at", "known_at")
            ):
                continue
            if table == "daily_demand_versions" and row["business_date"] > as_of.date():
                continue
            selected.append(row)
        known[table] = selected
    index = HistoryIndex(known)
    history: list[DailyHistory] = []
    for offset in range(policy.history_days - 1, -1, -1):
        day = as_of.date() - timedelta(days=offset)
        verified, in_stock, onsets = index.inventory_day(day)
        history.append(
            DailyHistory.model_validate(
                dict(
                    business_date=day,
                    observed_units=index.observed_day(product, stock, day),
                    inventory_day_verified=verified,
                    in_stock_all_day=in_stock,
                    stockout_onsets=onsets,
                )
            )
        )
    sales = [h.observed_units for h in history if h.observed_units is not None]
    uncensored = [
        h.observed_units
        for h in history
        if h.observed_units is not None and h.in_stock_all_day is True
    ]
    mean = fsum(sales) / len(sales) if sales else None
    in_stock_mean = fsum(uncensored) / len(uncensored) if uncensored else None
    snapshots = known["inventory_daily_snapshots"]
    state = max(snapshots, key=lambda r: r["snapshot_at"]) if snapshots else None
    quantity = state["available_qty"] if state and state["status"] == "known" else None
    if quantity is not None and state is not None:
        quantity += sum(
            r["quantity_delta"]
            for r in known["inventory_ledger"]
            if r["occurred_at"] > state["snapshot_at"] or r["available_at"] > state["snapshot_at"]
        )
        if quantity < 0:
            raise ValueError("negative_known_stockout_origin_balance")
    age = (as_of - state["snapshot_at"]).total_seconds() if state else None
    if state is not None and (
        state["reserved_qty"] != 0 or state["available_qty"] != state["on_hand"]
    ):
        raise ValueError("stockout_features_require_unreserved_stock")
    status, reason = "eligible", None
    catalog = known["product_catalog"]
    if quantity is None:
        status, reason = "insufficient_data", "inventory_unknown"
    elif age is not None and age > policy.max_snapshot_age_seconds:
        status, reason = "insufficient_data", "stale_inventory_snapshot"
    elif (
        not catalog
        or catalog[0]["launch_date"] > as_of.date()
        or (
            catalog[0]["discontinue_date"] is not None
            and catalog[0]["discontinue_date"] <= as_of.date()
        )
    ):
        status, reason = "insufficient_data", "inactive_or_unknown_product"
    elif not active_pairs(known, product, stock, as_of.date()):
        status, reason = "insufficient_data", "inactive_or_unknown_assortment_routing"
    elif quantity == 0:
        status = "already_stockout"
    elif len(sales) < policy.minimum_known_days:
        status, reason = "insufficient_data", "insufficient_sales_history"
    lineage = [
        dict(
            table=table,
            rows=len(rows),
            source_records_sha256=hashlib.sha256(
                canonical_json(sorted(r["source_record_sha256"] for r in rows))
            ).hexdigest(),
            max_available_at=max((r["curated_available_at"] for r in rows), default=None),
        )
        for table, rows in sorted(known.items())
    ]
    values = dict(
        available_qty=quantity,
        snapshot_age_hours=age / 3600 if age is not None else None,
        history_known_days=len(sales),
        history_missing_days=policy.history_days - len(sales),
        observed_sales_mean=mean,
        observed_sales_std=sqrt(fsum((v - mean) ** 2 for v in sales) / len(sales))
        if sales and mean is not None
        else None,
        in_stock_sales_mean=in_stock_mean,
        history_in_stock_days=len(uncensored),
        history_constrained_days=sum(h.in_stock_all_day is False for h in history),
        history_inventory_unknown_days=sum(not h.inventory_day_verified for h in history),
        historical_stockout_onsets=sum(
            h.stockout_onsets
            for h in history
            if h.business_date < as_of.date() and h.stockout_onsets is not None
        )
        if all(h.inventory_day_verified for h in history if h.business_date < as_of.date())
        else None,
        days_of_supply_observed=quantity / mean if quantity is not None and mean else None,
        days_of_supply_in_stock=quantity / in_stock_mean
        if quantity is not None and in_stock_mean
        else None,
        **supply_features(known, as_of),
    )
    return FeaturePoint.model_validate(
        dict(
            product_id=product,
            stock_location_id=stock,
            as_of=as_of,
            status=status,
            reason=reason,
            feature_available_at=max(
                (r["max_available_at"] for r in lineage if r["max_available_at"] is not None),
                default=None,
            ),
            values=values,
            history=tuple(history),
            lineage=tuple(lineage),
        )
    )
