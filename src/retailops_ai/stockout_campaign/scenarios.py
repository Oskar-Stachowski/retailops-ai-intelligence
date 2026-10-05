"""Prospective evaluation tags, using known plans and predetermined source treatments."""

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Any

from retailops_ai.data_contracts.common import utc_time

REQUIRED = ("normal", "promotion", "demand_shock", "inventory_constraint")
SHOCK_START, SHOCK_END = date(2026, 7, 28), date(2026, 8, 4)


def active_versions(
    rows: list[dict[str, Any]], key: str, origin: datetime, day: date
) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        if utc_time(row["curated_available_at"]) > origin or not (
            row["effective_from"] <= day < row["effective_to"]
        ):
            continue
        prior = selected.get(row[key])
        if prior is None or row["version"] > prior["version"]:
            selected[row[key]] = row
        elif row["version"] == prior["version"] and row != prior:
            raise ValueError("stockout_final_ambiguous_known_plan_version")
    return list(selected.values())


def promotion_exposure(row: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> bool:
    origin = datetime.fromisoformat(row["as_of"])
    for offset in range(1, 8):
        day = origin.date() + timedelta(days=offset)
        plans = active_versions(tables["promotion_plans"], "promotion_key", origin, day)
        plans = [
            p for p in plans if p["product_id"] == row["product_id"] and p["status"] == "active"
        ]
        if not plans:
            continue
        routes = active_versions(tables["fulfillment_routes"], "route_key", origin, day)
        routes = [r for r in routes if r["stock_location_id"] == row["stock_location_id"]]
        assortment = active_versions(tables["assortment"], "assortment_key", origin, day)
        assortment = [a for a in assortment if a["product_id"] == row["product_id"]]
        for plan in plans:
            for route in routes:
                pair = route["selling_location_id"], route["channel"]
                if not any((a["selling_location_id"], a["channel"]) == pair for a in assortment):
                    continue
                if (
                    plan["scope"] in {"global", "channel"} or plan["selling_location_id"] == pair[0]
                ) and (plan["channel"] == "all" or plan["channel"] == pair[1]):
                    return True
    return False


def memberships(
    rows: list[dict[str, Any]],
    *,
    world: str,
    tables: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[int]], dict[str, int]]:
    if world not in {"matching", "future_stress"} or set(tables) != {
        "promotion_plans",
        "fulfillment_routes",
        "assortment",
    }:
        raise ValueError("stockout_final_scenario_inputs")
    groups: dict[str, list[int]] = defaultdict(list)
    sets: list[set[str]] = []
    for i, row in enumerate(rows):
        origin = datetime.fromisoformat(row["as_of"])
        start, end = origin.date() + timedelta(days=1), origin.date() + timedelta(days=8)
        promo = promotion_exposure(row, tables)
        promotion_windows = [
            (p["effective_from"], p["effective_to"])
            for p in tables["promotion_plans"]
            if p["status"] == "active" and utc_time(p["curated_available_at"]) <= origin
        ]
        shock_window = world == "future_stress" and start < SHOCK_END and end > SHOCK_START
        shock_sku = int(hashlib.sha256(row["product_id"].encode()).hexdigest(), 16) % 3 == 0
        shock = shock_window and shock_sku
        constrained = row["values"]["history_constrained_days"] > 0
        tags = {
            n
            for n, active in (
                ("promotion", promo),
                ("demand_shock", shock),
                ("inventory_constraint", constrained),
            )
            if active
        }
        if not tags:
            tags.add("normal")
        sets.append(tags)
        for tag in tags:
            groups[tag].append(i)
        if shock_window and not shock_sku:
            groups["control:demand_shock"].append(i)
        if not promo and any(start < last and end > first for first, last in promotion_windows):
            groups["control:promotion"].append(i)
        if not constrained:
            groups["control:inventory_constraint"].append(i)
    for name in (
        *REQUIRED,
        "control:promotion",
        "control:demand_shock",
        "control:inventory_constraint",
    ):
        groups.setdefault(name, [])
    overlaps = {
        a + "&" + b: sum(a in tags and b in tags for tags in sets)
        for a, b in combinations(REQUIRED, 2)
    }
    return dict(groups), overlaps
