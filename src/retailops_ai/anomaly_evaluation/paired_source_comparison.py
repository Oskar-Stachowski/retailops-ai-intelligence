"""Offline comparison of complete, independently replayed Source 2.7/2.8 tables.

This numerical component grants no Source access and creates no campaign receipt.
The caller must independently replay both native datasets, bind their generation
and configuration, and reserve the read before calling it. A disk multiset keeps
only one complete Source world in RAM at a time. No detector scores enter here.
"""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

POLICY = {
    "version": "anomaly-paired-source-comparison-1.0.0",
    "population": "all_58_native_tables_all_rows_all_columns_both_parents",
    "comparison": "canonical_row_multiset_with_shared_order_product_joins",
    "clean": "native_complete_days_strictly_before_first_product_difference_or_intervention",
    "spillover": "changed_product_all_channels_and_event_types_unknown_from_earliest_row_date",
    "recovery": "never_inferred_from_later_equal_rows",
    "positive": "requires_separate_native_scenario_effect_and_maturity_verification",
    "training_truth": "never_accessed_by_training_or_scoring",
}

# Exact table inventory is deliberately versioned. A new producer table must be
# reviewed, not silently omitted from the proof of equality. Dimensions, private
# simulation parameters and public policies must match in full, including dates.
INVARIANT_TABLES = frozenset(
    "assortment business_calendar catalog_categories category_calendar channel_assignments "
    "daily_demand_exclusions daily_price_observations fulfillment_routes "
    "inventory_fulfillment_routes inventory_products inventory_reorder_rules "
    "inventory_route_versions inventory_scope inventory_selling_locations "
    "inventory_stock_locations price_history price_plans product_catalog "
    "product_simulation_parameters product_suppliers products promotion_effect_truth "
    "promotion_plans promotions return_policies selling_locations stock_locations "
    "store_simulation_parameters stores suppliers warehouses".split()
)
PRODUCT_TABLES = frozenset(
    "daily_demand_observations daily_demand_truth daily_demand_versions daily_return_cohorts "
    "inventory_daily_snapshots inventory_demand_arrivals inventory_demand_outcomes "
    "inventory_history_coverage inventory_ledger inventory_lost_sales_impacts "
    "inventory_physical_daily_balances inventory_returns inventory_sales "
    "inventory_scheduled_receipt_tail inventory_window_diagnostics replenishment_orders "
    "replenishment_receipts return_events return_inventory_decisions returns "
    "sale_price_references sales stockout_episodes".split()
)
JOIN_TABLES = frozenset(
    {"orders", "order_items", "delivery_plan_versions", "inventory_supplier_samples"}
)
SOURCE_TABLES = INVARIANT_TABLES | PRODUCT_TABLES | JOIN_TABLES
DATE_FIELDS = frozenset(
    "business_date sold_at ordered_at created_at occurred_at returned_at received_at "
    "ingested_at available_at financial_available_at inventory_available_at "
    "source_available_at known_at snapshot_at period_from period_to covered_from_at "
    "covered_through_at start_at end_at start_available_at end_available_at "
    "observed_through_exclusive_at diagnostic_available_at origin window_end_at "
    "evaluated_at label_available_at expected_delivery_at as_of_time".split()
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _earliest_day(*rows: Mapping[str, Any]) -> str:
    days = []
    for row in rows:
        for key in row:
            if key not in DATE_FIELDS and not key.endswith(("_at", "_date")):
                continue
            value = row[key]
            if value is None or value == "":
                continue
            if not isinstance(value, str):
                raise ValueError("anomaly_paired_truth_date_scalar")
            if len(value) == 10:
                parsed = date.fromisoformat(value)
            else:
                stamp = datetime.fromisoformat(value)
                if stamp.utcoffset() != timedelta(0):
                    raise ValueError("anomaly_paired_truth_non_utc_date")
                parsed = stamp.date()
            days.append(parsed)
    if not days:
        raise ValueError("anomaly_paired_truth_undated_dynamic_row")
    # An interval summary or forward window may change because of a later
    # intervention. Starting at its earliest date deliberately underclaims clean
    # coverage; it cannot move the boundary forward to improve an evaluation.
    return min(days).isoformat()


def _unique(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError("anomaly_paired_truth_duplicate_join_key")
    return result


class PairedSourceComparison:
    """One-use private index; caller owns its resource guard and enclosing directory.

    All rows participate as multisets, including deletions, additions, duplicates
    and private process truth. Shared basket headers affect every linked SKU.
    Identity churn is treated conservatively as a difference, never normalized
    away. Input order has no effect on the resulting proof.
    """

    def __init__(self, path: Path, *, max_rows: int = 20000000) -> None:
        if not 1 <= max_rows <= 20000000:
            raise ValueError("anomaly_paired_truth_row_budget")
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA cache_size=-8192")
        self.connection.execute("PRAGMA temp_store=FILE")
        self.connection.execute(
            "CREATE TABLE rows (table_name TEXT, product TEXT, day TEXT, row_sha TEXT, "
            "balance INTEGER NOT NULL, PRIMARY KEY(table_name, product, day, row_sha)) "
            "WITHOUT ROWID"
        )
        self.max_rows = max_rows
        self.state = "empty"
        self.products: tuple[str, ...] = ()
        self.counts: dict[str, dict[str, int]] = {}

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "PairedSourceComparison":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _rows(
        self, tables: Mapping[str, Sequence[Mapping[str, Any]]], sign: int
    ) -> Iterator[tuple[str, str, str, str, int]]:
        orders = _unique(tables["orders"], "id")
        replenishments = _unique(tables["replenishment_orders"], "replenishment_order_id")
        members: dict[str, set[str]] = {}
        for row in tables["order_items"]:
            if row["order_id"] not in orders:
                raise ValueError("anomaly_paired_truth_orphan_order_item")
            members.setdefault(row["order_id"], set()).add(row["product_id"])
        if members.keys() != orders.keys():
            raise ValueError("anomaly_paired_truth_order_without_product")
        allowed = set(self.products)
        for name in sorted(SOURCE_TABLES):
            for row in tables[name]:
                row_sha = hashlib.sha256(_canonical(row).encode()).hexdigest()
                if name in INVARIANT_TABLES:
                    yield name, "", "", row_sha, sign
                    continue
                if name == "orders":
                    owners, day = members[row["id"]], _earliest_day(row)
                elif name == "order_items":
                    owners = {row["product_id"]}
                    day = _earliest_day(orders[row["order_id"]])
                elif name in {"delivery_plan_versions", "inventory_supplier_samples"}:
                    key = (
                        "replenishment_order_id" if name == "delivery_plan_versions" else "order_id"
                    )
                    parent = replenishments.get(row[key])
                    if parent is None:
                        raise ValueError("anomaly_paired_truth_orphan_supply_row")
                    owners, day = {parent["product_id"]}, _earliest_day(row, parent)
                else:
                    owners, day = {row["product_id"]}, _earliest_day(row)
                if not owners <= allowed:
                    raise ValueError("anomaly_paired_truth_unknown_product")
                for product in sorted(owners):
                    yield name, product, day, row_sha, sign

    def add(self, tables: Mapping[str, Sequence[Mapping[str, Any]]], *, parent: str) -> None:
        expected = "ordinary" if self.state == "empty" else "planned"
        if self.state not in {"empty", "ordinary"} or parent != expected:
            raise ValueError("anomaly_paired_truth_parent_order")
        # A failed partial load poisons this one-use index, including failures
        # before a transaction. Retrying against a partial baseline is forbidden.
        self.state = "failed"
        if set(tables) != SOURCE_TABLES:
            raise ValueError("anomaly_paired_truth_complete_table_inventory")
        counts = {name: len(rows) for name, rows in tables.items()}
        if sum(counts.values()) > self.max_rows:
            raise ValueError("anomaly_paired_truth_row_budget")
        products = tuple(sorted(_unique(tables["products"], "id")))
        if not products or (parent == "planned" and products != self.products):
            raise ValueError("anomaly_paired_truth_product_population")
        self.products = products
        with self.connection:
            self.connection.executemany(
                "INSERT INTO rows VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(table_name,product,day,row_sha) DO UPDATE "
                "SET balance=balance+excluded.balance",
                self._rows(tables, 1 if parent == "ordinary" else -1),
            )
            if parent == "planned":
                self.connection.execute("DELETE FROM rows WHERE balance=0")
        self.counts[parent] = counts
        self.state = parent

    def finish(self, injections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if self.state != "planned":
            raise ValueError("anomaly_paired_truth_incomplete_comparison")
        self.state = "failed"
        if self.connection.execute("SELECT 1 FROM rows WHERE product='' LIMIT 1").fetchone():
            raise ValueError("anomaly_paired_truth_invariant_tables_differ")
        earliest = dict(
            self.connection.execute("SELECT product, MIN(day) FROM rows GROUP BY product")
        )
        first_difference = dict(earliest)
        # A zero observed effect does not make an injected scope clean. The
        # native positive-effect checks remain an independent required gate.
        for item in injections:
            product, start = item["product_id"], date.fromisoformat(item["start_date"]).isoformat()
            if product not in self.products:
                raise ValueError("anomaly_paired_truth_unknown_injected_product")
            earliest[product] = min(earliest.get(product, start), start)
        changes = {
            name: {"ordinary_only_memberships": a, "planned_only_memberships": b}
            for name, a, b in self.connection.execute(
                "SELECT table_name, SUM(MAX(balance,0)), SUM(MAX(-balance,0)) "
                "FROM rows GROUP BY table_name ORDER BY table_name"
            )
        }
        digest = hashlib.sha256()
        for row in self.connection.execute(
            "SELECT table_name, product, day, row_sha, balance FROM rows "
            "ORDER BY table_name, product, day, row_sha"
        ):
            digest.update((_canonical(row) + "\n").encode())
        self.state = "complete"
        return {
            "policy": dict(POLICY),
            "parent_table_rows": self.counts,
            "product_count": len(self.products),
            "changed_product_count": len(first_difference),
            "first_difference_by_product": dict(sorted(first_difference.items())),
            "unknown_from_by_product": dict(sorted(earliest.items())),
            "changed_table_memberships": changes,
            "difference_multiset_sha256": digest.hexdigest(),
            "quality_qualified": False,
            "stage_ready": False,
        }


def paired_clean_windows(
    ordinary: Sequence[Mapping[str, Any]],
    planned: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Intersect actual native complete windows, never extending gaps or maturity.

    These are only candidate clean windows from the numerical comparison. Positive
    episodes, Source verification and the campaign receipt must be bound separately.
    Unknown observations remain in the full scoring census and coverage denominator.
    """
    if comparison["policy"] != POLICY:
        raise ValueError("anomaly_paired_truth_policy")
    key = ("event_type", "product_id", "selling_location_id", "channel", "currency")
    by_scope: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for item in ordinary:
        by_scope.setdefault(tuple(item[k] for k in key), []).append(item)
    result = []
    for item in planned:
        for baseline in by_scope.get(tuple(item[k] for k in key), []):
            start = max(date.fromisoformat(w["window"]["start"]) for w in (item, baseline))
            end = min(date.fromisoformat(w["window"]["end"]) for w in (item, baseline))
            boundary = comparison["unknown_from_by_product"].get(item["product_id"])
            if boundary is not None:
                end = min(end, date.fromisoformat(boundary) - timedelta(days=1))
            if start > end:
                continue
            clocks = [datetime.fromisoformat(w["available_at"]) for w in (item, baseline)]
            if any(clock.utcoffset() != timedelta(0) for clock in clocks):
                raise ValueError("anomaly_paired_truth_non_utc_maturity")
            known = max(clocks)
            result.append(
                {
                    **{k: item[k] for k in key},
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "available_at": known.isoformat().replace("+00:00", "Z"),
                }
            )
            if len(result) > 10000:
                raise ValueError("anomaly_paired_truth_window_budget")
    return sorted(result, key=lambda w: (*(w[k] for k in key), w["window"]["start"]))
