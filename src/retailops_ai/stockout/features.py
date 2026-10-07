"""Pure as-of feature projection from already verified curated facts/plans."""

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from math import fsum, sqrt
from typing import Any

from retailops_ai.data_contracts.common import utc_time
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.feature_contract import (
    DEFAULT_FEATURE_POLICY,
    DailyHistory,
    FeaturePoint,
    FeaturePolicy,
)

FEATURE_TABLES = (
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
)
Records = Mapping[str, Sequence[dict[str, Any]]]


def latest(rows: Sequence[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row[k] for k in fields)
        if key not in result or row["version"] > result[key]["version"]:
            result[key] = row
        elif row["version"] == result[key]["version"]:
            raise ValueError("ambiguous_stockout_as_of_version")
    return list(result.values())


def effective(rows: Sequence[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    return [r for r in rows if r["effective_from"] <= day < r["effective_to"]]


def active_pairs(records: Records, product: str, stock: str, day: date) -> set[tuple[str, str]]:
    routes = latest(effective(records["fulfillment_routes"], day), ("route_key",))
    pairs = {
        (r["selling_location_id"], r["channel"]) for r in routes if r["stock_location_id"] == stock
    }
    assortment = latest(effective(records["assortment"], day), ("assortment_key",))
    return pairs & {
        (r["selling_location_id"], r["channel"]) for r in assortment if r["product_id"] == product
    }


def observed_day(records: Records, product: str, stock: str, day: date) -> int | None:
    pairs = active_pairs(records, product, stock, day)
    if not pairs:
        return None
    selected = latest(
        [r for r in records["daily_demand_versions"] if r["business_date"] == day],
        ("product_id", "selling_location_id", "channel", "business_date"),
    )
    by_pair = {(r["selling_location_id"], r["channel"]): r for r in selected}
    values: list[int] = []
    for pair in sorted(pairs):
        row = by_pair.get(pair)
        if (
            row is None
            or row["observation_status"] not in {"observed_positive", "observed_zero", "closed"}
            or row["observed_units"] is None
        ):
            return None
        if row["mapped_stock_location_id"] != stock:
            return None  # A later route must not move a historical physical observation.
        values.append(row["observed_units"])
    return sum(values)


def inventory_day(records: Records, day: date) -> tuple[bool, bool | None, int | None]:
    start = datetime.combine(day, time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    ledger = sorted(records["inventory_ledger"], key=lambda r: (r["occurred_at"], r["sequence"]))
    if (
        not ledger
        or ledger[0]["movement_type"] != "opening_stock"
        or ledger[0]["occurred_at"] > start
    ):
        return False, None, None
    covered = any(
        r["covered_from_at"] <= ledger[0]["occurred_at"] and r["covered_through_at"] >= end
        for r in records["inventory_history_coverage"]
    )
    if not covered:
        return False, None, None
    before = sum(r["quantity_delta"] for r in ledger if r["occurred_at"] < start)
    if ledger[0]["occurred_at"] == start:
        before = ledger[0]["quantity_delta"]
    constrained, onsets = before == 0, 0
    balance = before
    for row in ledger:
        if not start <= row["occurred_at"] < end or row["movement_type"] == "opening_stock":
            continue
        previous = balance
        balance += row["quantity_delta"]
        if balance < 0:
            raise ValueError("invalid_known_stockout_ledger_balance")
        constrained |= balance == 0
        onsets += int(previous > 0 and balance == 0)
    return True, not constrained, onsets


def supply_features(records: Records, origin: datetime) -> dict[str, Any]:
    plans = {
        r["replenishment_order_id"]: r
        for r in latest(records["delivery_plan_versions"], ("replenishment_order_id",))
    }
    receipts: dict[str, int] = {}
    for receipt in records["replenishment_receipts"]:
        key = receipt["replenishment_order_id"]
        receipts[key] = receipts.get(key, 0) + receipt["received_quantity"]
    total = due = overdue = 0
    dates = []
    for order in records["replenishment_orders"]:
        quantity = order["ordered_quantity"] - receipts.get(order["replenishment_order_id"], 0)
        if quantity < 0:
            raise ValueError("stockout_receipt_exceeds_known_order")
        if not quantity:
            continue
        total += quantity
        plan = plans.get(order["replenishment_order_id"], order)
        expected = plan["expected_delivery_at"]
        if expected < origin:
            overdue += quantity
        elif expected <= origin + timedelta(days=7):
            due += quantity
        dates.append(max(0.0, (expected - origin).total_seconds() / 3600))
    quotes = effective(records["product_suppliers"], origin.date())
    # Quotes have no source revision key; the verified contract rejects ambiguity.
    quotes.sort(key=lambda r: (r["priority"], r["product_supplier_id"]))
    return dict(
        open_order_quantity=total,
        due_within_7d_quantity=due,
        overdue_order_quantity=overdue,
        next_expected_delivery_hours=min(dates) if dates else None,
        quoted_lead_time_days=float(quotes[0]["quoted_lead_time_days"]) if quotes else None,
    )


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
    history: list[DailyHistory] = []
    for offset in range(policy.history_days - 1, -1, -1):
        day = as_of.date() - timedelta(days=offset)
        verified, in_stock, onsets = inventory_day(known, day)
        history.append(
            DailyHistory.model_validate(
                dict(
                    business_date=day,
                    observed_units=observed_day(known, product, stock, day),
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
