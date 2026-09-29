"""Independently verify native source hashes, ledger balances and daily snapshots.

The disk index is private and temporary. Worker facts never read evaluation truth.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    decode_json,
    regular_file,
)
from retailops_ai.source_snapshot.inventory_protocol import contract_document, resource_bytes
from retailops_ai.source_snapshot.protocol import Limits, Snapshot

PROCESSES = {
    "opening_stock": "opening",
    "replenishment_received": "replenishment",
    "sale": "sale",
    "return_to_stock": "return",
    "write_off": "write_off",
    "transfer_in": "transfer",
    "transfer_out": "transfer",
    "inventory_adjustment": "adjustment",
}
POSITIVE = {"replenishment_received", "return_to_stock", "transfer_in"}
NEGATIVE = {"sale", "write_off", "transfer_out"}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SnapshotError(code)


def instant(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    require(
        stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0),
        "inventory_time_requires_utc",
    )
    return stamp.astimezone(UTC)


def native_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v.isoformat()
        if isinstance(v, (date, datetime))
        else format(v, ".2f")
        if isinstance(v, Decimal)
        else v
        for k, v in row.items()
    }


def rows(root: Path, table: dict[str, Any], limits: Limits) -> Iterator[dict[str, Any]]:
    for ref in table["files"]:
        with regular_file(root, ref["path"]) as stream:
            parquet = pq.ParquetFile(stream, pre_buffer=False, page_checksum_verification=True)
            for batch in parquet.iter_batches(batch_size=limits.batch_rows, use_threads=False):
                require(batch.nbytes <= 64 * 1024**2, "inventory_batch_size_limit")
                yield from batch.to_pylist()
            parquet.close()


class Facts:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA cache_size=-4096")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute(
            "CREATE TABLE facts (name TEXT, grain BLOB, row BLOB, PRIMARY KEY(name,grain))"
        )
        self.db.execute(
            "CREATE TABLE movements (stamp TEXT, sequence INTEGER, product TEXT, stock TEXT, known TEXT, row BLOB, UNIQUE(stamp,sequence))"
        )
        self.db.execute("CREATE INDEX position ON movements(product,stock,stamp,sequence)")
        self.db.execute(
            "CREATE INDEX movement_reference ON movements(json_extract(row,'$.movement_type'),json_extract(row,'$.source_reference'))"
        )

    def add(self, name: str, grain: list[str], row: dict[str, Any]) -> None:
        raw = canonical_json(row)
        require(len(raw) <= 64 * 1024, "native_record_size_limit")
        try:
            self.db.execute(
                "INSERT INTO facts VALUES (?,?,?)",
                (name, canonical_json([row[k] for k in grain]), raw),
            )
            if name == "inventory_ledger":
                self.db.execute(
                    "INSERT INTO movements VALUES (?,?,?,?,?,?)",
                    (
                        instant(row["occurred_at"]).isoformat(timespec="microseconds"),
                        row["sequence"],
                        row["product_id"],
                        row["stock_location_id"],
                        instant(row["available_at"]).isoformat(timespec="microseconds"),
                        raw,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise SnapshotError("duplicate_native_grain_or_ordering_key") from exc

    def rows(self, name: str) -> Iterator[dict[str, Any]]:
        for (raw,) in self.db.execute("SELECT row FROM facts WHERE name=? ORDER BY grain", (name,)):
            yield decode_json(raw)

    def get(self, name: str, *key: str) -> dict[str, Any]:
        item = self.db.execute(
            "SELECT row FROM facts WHERE name=? AND grain=?", (name, canonical_json(list(key)))
        ).fetchone()
        require(item is not None, "missing_native_reference")
        return decode_json(item[0])

    def digest(self, name: str) -> str:
        digest = hashlib.sha256(b"[")
        comma = b""
        for row in self.rows(name):
            digest.update(comma + canonical_json(row))
            comma = b","
        digest.update(b"]")
        return digest.hexdigest()


def verify_ledger(facts: Facts, context: dict[str, Any]) -> None:
    units = {r["id"]: r["unit_of_measure"] for r in facts.rows("inventory_products")}
    stocks = {r["id"] for r in facts.rows("inventory_stock_locations")}
    scope = {(r["product_id"], r["stock_location_id"]) for r in facts.rows("inventory_scope")}
    require(
        bool(scope) and all(p in units and s in stocks for p, s in scope),
        "inventory_scope_reference_mismatch",
    )
    balances: dict[tuple[str, str], int] = {}
    transfers: dict[str, list[dict[str, Any]]] = {}
    for (raw,) in facts.db.execute("SELECT row FROM movements ORDER BY stamp,sequence"):
        row = decode_json(raw)
        position = row["product_id"], row["stock_location_id"]
        kind, delta = row["movement_type"], row["quantity_delta"]
        require(
            position in scope and row["unit_of_measure"] == units[position[0]],
            "ledger_master_or_unit_mismatch",
        )
        require(
            instant(row["occurred_at"])
            <= instant(row["ingested_at"])
            <= instant(row["available_at"]),
            "ledger_chronology_mismatch",
        )
        require(PROCESSES[kind] == row["source_process"], "ledger_process_mismatch")
        require(kind not in POSITIVE or delta > 0, "ledger_sign_mismatch")
        require(kind not in NEGATIVE or delta < 0, "ledger_sign_mismatch")
        require(kind != "inventory_adjustment" or delta != 0, "ledger_sign_mismatch")
        if kind == "opening_stock":
            require(
                position not in balances
                and instant(row["occurred_at"]) == instant(context["opening_at"])
                and delta >= 0,
                "ledger_duplicate_or_invalid_opening",
            )
            balances[position] = delta
        else:
            require(position in balances, "ledger_missing_preceding_opening")
            balances[position] += delta
        require(balances[position] >= 0, "ledger_negative_balance")
        if kind in {"transfer_in", "transfer_out"}:
            require(row["transfer_id"] is not None, "missing_transfer_id")
            transfers.setdefault(row["transfer_id"], []).append(row)
        else:
            require(row["transfer_id"] is None, "unexpected_transfer_id")
    require(set(balances) == scope, "ledger_opening_coverage_mismatch")
    for pair in transfers.values():
        require(len(pair) == 2, "incomplete_transfer_pair")
        out, inbound = pair
        require(
            out["movement_type"] == "transfer_out"
            and inbound["movement_type"] == "transfer_in"
            and out["quantity_delta"] == -inbound["quantity_delta"]
            and out["product_id"] == inbound["product_id"]
            and out["unit_of_measure"] == inbound["unit_of_measure"]
            and out["stock_location_id"] != inbound["stock_location_id"]
            and instant(out["available_at"]) <= instant(inbound["available_at"]),
            "transfer_pair_mismatch",
        )
    verify_snapshots(facts, context, scope, units)


def verify_snapshots(
    facts: Facts, context: dict[str, Any], scope: set[tuple[str, str]], units: dict[str, str]
) -> None:
    start, end = (instant(context["projection"][k]) for k in ("start_at", "end_at"))
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    expected_count = 0
    while day < end:
        lower, upper = max(day, start), min(day + timedelta(days=1), end)
        cutoff = upper - timedelta(microseconds=1)
        for product, stock in sorted(scope):
            row = facts.get("inventory_daily_snapshots", product, stock, day.date().isoformat())
            balance: int | None = None
            known: datetime | None = None
            last: str | None = None
            count = 0
            for (raw,) in facts.db.execute(
                "SELECT row FROM movements WHERE product=? AND stock=? AND stamp<=? AND known<=? ORDER BY stamp,sequence",
                (
                    product,
                    stock,
                    cutoff.isoformat(timespec="microseconds"),
                    cutoff.isoformat(timespec="microseconds"),
                ),
            ):
                movement = decode_json(raw)
                if movement["movement_type"] == "opening_stock":
                    require(balance is None, "known_duplicate_opening")
                    balance = movement["quantity_delta"]
                else:
                    require(balance is not None, "known_missing_opening")
                    balance += movement["quantity_delta"]
                require(balance is not None and balance >= 0, "known_negative_inventory")
                stamp = instant(movement["available_at"])
                known = max(known, stamp) if known is not None else stamp
                last, count = movement["inventory_event_id"], count + 1
            require(
                row["on_hand"] == balance == row["available_qty"]
                and row["reserved_qty"] == (0 if balance is not None else None)
                and row["status"] == ("known" if balance is not None else "not_available")
                and row["last_inventory_event_id"] == last
                and row["movement_count"] == count
                and (
                    instant(row["source_available_at"])
                    if row["source_available_at"] is not None
                    else None
                )
                == known
                and instant(row["snapshot_at"]) == instant(row["as_of_time"]) == cutoff
                and instant(row["period_from_at"]) == lower
                and instant(row["period_to_at"]) == upper
                and row["is_full_business_day"]
                == (lower == day and upper == day + timedelta(days=1))
                and row["unit_of_measure"] == units[product],
                "known_snapshot_ledger_mismatch",
            )
            expected_count += 1
        day += timedelta(days=1)
    require(
        expected_count
        == facts.db.execute(
            "SELECT count(*) FROM facts WHERE name='inventory_daily_snapshots'"
        ).fetchone()[0],
        "inventory_snapshot_coverage_mismatch",
    )


def verify_projection(root: Path, snapshot: Snapshot, scratch: Path, limits: Limits) -> None:
    contract = contract_document()
    models = decode_json(resource_bytes("inventory_tables.schema.json"))["properties"]
    facts = Facts(scratch / "native-facts.sqlite")
    try:
        for table in snapshot.manifest["tables"]:
            name = table["table"]
            identity = snapshot.manifest["source"]["descriptor"]["tables"][name]
            if name not in models:
                require(
                    table["content_sha256"] == identity["content_sha256"],
                    "commerce_source_content_mismatch",
                )
                if table["data_class"] != "simulation_truth":
                    for typed in rows(root, table, limits):
                        facts.add(name, table["grain"], native_row(typed))
                continue
            validator = Draft202012Validator(models[name]["items"])
            for typed in rows(root, table, limits):
                row = native_row(typed)
                try:
                    validator.validate(row)
                except ValidationError as exc:
                    raise SnapshotError("invalid_native_inventory_record") from exc
                facts.add(name, contract["source_table_contract"]["tables"][name]["grain"], row)
            require(
                facts.digest(name) == identity["content_sha256"], "native_source_content_mismatch"
            )
        verify_ledger(facts, snapshot.manifest["source"]["descriptor"]["context"])
        verify_operations(facts)
    finally:
        facts.db.close()


def issue(
    facts: Facts, row: dict[str, Any], kind: str, reference: str, time_field: str, delta: int
) -> None:
    records = facts.db.execute(
        "SELECT row FROM movements WHERE json_extract(row,'$.movement_type')=? AND json_extract(row,'$.source_reference')=?",
        (kind, reference),
    ).fetchall()
    require(len(records) == 1, "missing_or_duplicate_inventory_issue")
    movement = decode_json(records[0][0])
    require(
        all(
            movement[k] == row[k]
            for k in ("product_id", "stock_location_id", "unit_of_measure", "sequence")
        )
        and all(instant(movement[k]) == instant(row[k]) for k in ("ingested_at", "available_at"))
        and instant(movement["occurred_at"]) == instant(row[time_field])
        and movement["quantity_delta"] == delta,
        "inventory_issue_fact_mismatch",
    )
    if kind == "replenishment_received":
        require(
            movement["supplier_id"] == row["supplier_id"]
            and movement["order_id"] == row["replenishment_order_id"],
            "receipt_order_link_mismatch",
        )


def chronology(row: dict[str, Any], field: str) -> None:
    require(
        instant(row[field]) <= instant(row["ingested_at"]) <= instant(row["available_at"]),
        "inventory_fact_chronology_mismatch",
    )


def verify_operations(facts: Facts) -> None:
    routes = list(facts.rows("inventory_fulfillment_routes"))
    for route in routes:
        facts.get("inventory_selling_locations", route["selling_location_id"])
        facts.get("inventory_stock_locations", route["stock_location_id"])
        require(route["effective_from"] < route["effective_to"], "invalid_inventory_route_period")
    sales_count = 0
    for sale in facts.rows("inventory_sales"):
        chronology(sale, "sold_at")
        require(
            instant(sale["ordered_at"]) <= instant(sale["sold_at"]),
            "sale_order_chronology_mismatch",
        )
        issue(facts, sale, "sale", sale["sale_id"], "sold_at", -sale["quantity"])
        matched = [
            r
            for r in routes
            if r["selling_location_id"] == sale["selling_location_id"]
            and r["channel"] == sale["channel"]
            and r["effective_from"]
            <= instant(sale["sold_at"]).date().isoformat()
            < r["effective_to"]
            and instant(r["available_at"]) <= instant(sale["sold_at"])
        ]
        require(
            len(matched) == 1
            and matched[0]["id"] == sale["fulfillment_route_id"]
            and matched[0]["stock_location_id"] == sale["stock_location_id"],
            "sale_historical_route_mismatch",
        )
        require(
            Decimal(sale["unit_price"]) > 0
            and Decimal(sale["gross_revenue"]) == Decimal(sale["unit_price"]) * sale["quantity"],
            "inventory_sale_revenue_mismatch",
        )
        commerce = facts.get("sales", sale["sale_id"])
        require(
            all(
                commerce[k] == sale[k]
                for k in ("product_id", "quantity", "currency", "channel", "unit_price")
            )
            and instant(commerce["sold_at"]) == instant(sale["sold_at"])
            and instant(commerce["ingested_at"]) == instant(sale["ingested_at"])
            and commerce["total_amount"] == sale["gross_revenue"]
            and commerce["observed_sales"] == sale["quantity"],
            "commerce_inventory_sale_mismatch",
        )
        sales_count += 1
    require(
        sales_count
        == facts.db.execute(
            "SELECT count(*) FROM movements WHERE json_extract(row,'$.movement_type')='sale'"
        ).fetchone()[0]
        == facts.db.execute("SELECT count(*) FROM facts WHERE name='sales'").fetchone()[0],
        "inventory_sale_issue_coverage_mismatch",
    )
    returned: dict[str, int] = {}
    accepted_count = 0
    for row in facts.rows("inventory_returns"):
        sale = facts.get("inventory_sales", row["sale_id"])
        chronology(row, "returned_at")
        require(
            all(
                row[k] == sale[k]
                for k in ("product_id", "stock_location_id", "unit_of_measure", "currency")
            )
            and (instant(sale["sold_at"]), sale["sequence"])
            < (instant(row["returned_at"]), row["sequence"])
            and instant(sale["available_at"]) <= instant(row["available_at"]),
            "inventory_return_sale_mismatch",
        )
        require(
            Decimal(row["refund_amount"]) == Decimal(sale["unit_price"]) * row["quantity"],
            "inventory_refund_price_mismatch",
        )
        returned[row["sale_id"]] = returned.get(row["sale_id"], 0) + row["quantity"]
        require(returned[row["sale_id"]] <= sale["quantity"], "inventory_return_exceeds_purchase")
        decision = facts.get("return_inventory_decisions", row["return_id"])
        require(
            all(
                row[k] == decision[k]
                for k in (
                    "sale_id",
                    "product_id",
                    "stock_location_id",
                    "quantity",
                    "quality_status",
                )
            ),
            "inventory_return_decision_mismatch",
        )
        if row["quality_status"] == "accepted":
            require(
                decision["inventory_disposition"] == "restocked", "accepted_return_not_restocked"
            )
            issue(facts, row, "return_to_stock", row["return_id"], "returned_at", row["quantity"])
            accepted_count += 1
    require(
        accepted_count
        == facts.db.execute(
            "SELECT count(*) FROM movements WHERE json_extract(row,'$.movement_type')='return_to_stock'"
        ).fetchone()[0],
        "return_restock_coverage_mismatch",
    )
    verify_supply(facts)


def verify_supply(facts: Facts) -> None:
    for offer in facts.rows("product_suppliers"):
        supplier = facts.get("suppliers", offer["supplier_id"])
        product = facts.get("inventory_products", offer["product_id"])
        chronology(offer, "known_at")
        require(
            offer["unit_of_measure"] == product["unit_of_measure"]
            and offer["effective_from"] < offer["effective_to"]
            and Decimal(offer["unit_cost"]) > 0
            and instant(supplier["available_at"]) <= instant(offer["known_at"]),
            "invalid_inventory_supplier_quote",
        )
    for order in facts.rows("replenishment_orders"):
        quote = facts.get("product_suppliers", order["product_supplier_id"])
        supplier = facts.get("suppliers", order["supplier_id"])
        facts.get("inventory_scope", order["product_id"], order["stock_location_id"])
        chronology(order, "ordered_at")
        stamp = instant(order["ordered_at"])
        require(
            all(order[k] == quote[k] for k in ("product_id", "supplier_id", "unit_of_measure"))
            and quote["effective_from"] <= stamp.date().isoformat() < quote["effective_to"]
            and instant(quote["available_at"]) <= stamp
            and supplier["status"] == "active"
            and order["ordered_quantity"] >= supplier["minimum_order_quantity"]
            and instant(order["expected_delivery_at"]) >= stamp,
            "inventory_order_quote_mismatch",
        )
        plans = [
            decode_json(raw)
            for (raw,) in facts.db.execute(
                "SELECT row FROM facts WHERE name='delivery_plan_versions' AND json_extract(row,'$.replenishment_order_id')=? ORDER BY json_extract(row,'$.version')",
                (order["replenishment_order_id"],),
            )
        ]
        require(
            bool(plans) and [p["version"] for p in plans] == list(range(1, len(plans) + 1)),
            "delivery_plan_version_coverage_mismatch",
        )
        first = plans[0]
        require(
            all(
                instant(first[k]) == instant(order[other])
                for k, other in (
                    ("known_at", "ordered_at"),
                    ("ingested_at", "ingested_at"),
                    ("available_at", "available_at"),
                    ("expected_delivery_at", "expected_delivery_at"),
                )
            ),
            "initial_delivery_plan_mismatch",
        )
        previous = stamp
        for plan in plans:
            chronology(plan, "known_at")
            require(
                previous <= instant(plan["known_at"])
                and stamp <= instant(plan["expected_delivery_at"]),
                "delivery_plan_chronology_mismatch",
            )
            previous = instant(plan["known_at"])
    received: dict[str, int] = {}
    count = 0
    for receipt in facts.rows("replenishment_receipts"):
        order = facts.get("replenishment_orders", receipt["replenishment_order_id"])
        chronology(receipt, "received_at")
        require(
            all(
                receipt[k] == order[k]
                for k in ("product_id", "stock_location_id", "supplier_id", "unit_of_measure")
            )
            and instant(order["ordered_at"]) <= instant(receipt["received_at"])
            and instant(order["available_at"]) <= instant(receipt["available_at"]),
            "inventory_receipt_order_mismatch",
        )
        received[receipt["replenishment_order_id"]] = (
            received.get(receipt["replenishment_order_id"], 0) + receipt["received_quantity"]
        )
        require(
            received[receipt["replenishment_order_id"]] <= order["ordered_quantity"],
            "inventory_over_receipt",
        )
        issue(
            facts,
            receipt,
            "replenishment_received",
            receipt["receipt_id"],
            "received_at",
            receipt["received_quantity"],
        )
        count += 1
    require(
        count
        == facts.db.execute(
            "SELECT count(*) FROM movements WHERE json_extract(row,'$.movement_type')='replenishment_received'"
        ).fetchone()[0],
        "receipt_ledger_coverage_mismatch",
    )
