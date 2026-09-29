"""Curated 1.1 preserves physical grains and causal inventory availability."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from retailops_ai.curated.contract import Config, cell, record_sha
from retailops_ai.curated.transform import Index, Reject, normalize, transform
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.source_snapshot.protocol import contract_document

# These commerce records need the causal sale/return and its original physical route.
COMMERCE_TABLES = frozenset(contract_document()["fact_tables"])
SALE_TABLES = {"sales", "sale_price_references", "return_events"}


def transform_inventory(
    table: str, raw: dict[str, Any], grain: list[str], index: Index, config: Config
) -> dict[str, Any]:
    if table in COMMERCE_TABLES and table not in SALE_TABLES:
        return transform(table, raw, grain, index, config)
    row = normalize(raw)
    available = [
        row[k]
        for k in (
            "available_at",
            "known_at",
            "ingested_at",
            "financial_available_at",
            "inventory_available_at",
            "snapshot_at",
            "source_available_at",
            "covered_through_at",
        )
        if isinstance(row.get(k), datetime)
    ]
    product, stock = row.get("product_id"), row.get("stock_location_id")
    location, channel = row.get("selling_location_id"), row.get("channel")
    day = row.get("business_date")
    refs: list[str] = []
    unit = row.get("unit_of_measure")
    for field in ("sold_at", "occurred_at", "returned_at", "received_at", "ordered_at", "known_at"):
        if day is None and isinstance(row.get(field), datetime):
            day = row[field].date()
    if table in SALE_TABLES or table in {
        "inventory_sales",
        "inventory_returns",
        "return_inventory_decisions",
    }:
        sale_id = row["id"] if table == "sales" else row["sale_id"]
        sale = index.native("inventory_sales", sale_id)
        product, stock = sale["product_id"], sale["stock_location_id"]
        location, channel = sale["selling_location_id"], sale["channel"]
        refs.extend([sale_id, sale["fulfillment_route_id"]])
        available.append(sale["available_at"])
        if day is None:
            day = sale["sold_at"].date()
    if table == "delivery_plan_versions":
        order = index.native("replenishment_orders", row["replenishment_order_id"])
        product, stock = order["product_id"], order["stock_location_id"]
        available.append(order["available_at"])
        refs.append(order["replenishment_order_id"])
    if table == "inventory_products":
        product = row["id"]
    if table == "inventory_stock_locations":
        stock = row["id"]
    if table == "inventory_selling_locations":
        location = row["id"]
    if product is not None:
        catalog = index.get("product_catalog", product)
        if catalog["unit_of_measure"] != "pcs" or unit not in (None, "pcs"):
            raise Reject("unsupported_inventory_unit")
        if row.get("currency") not in (None, *config.currencies):
            raise Reject("unsupported_currency")
        if row.get("currency") not in (None, catalog["currency"]):
            raise Reject("currency_product_mismatch")
        unit = "pcs"
        # A catalog entry cannot make a record without an availability timestamp known.
        if available:
            available.append(catalog["available_at"])
        refs.append(catalog["id"])
    if stock is not None:
        index.get("inventory_stock_locations", stock)
    if location is not None:
        index.get("inventory_selling_locations", location)
    if table == "inventory_daily_snapshots" and row["status"] != "known":
        available = []
    eligible = (
        table == "inventory_daily_snapshots"
        and row["status"] == "known"
        and row["is_full_business_day"]
    )
    return {
        **row,
        "source_record_sha256": record_sha(raw),
        "source_grain_json": canonical_json([cell(raw[k]) for k in grain]).decode(),
        "curated_available_at": max(available) if available else None,
        "availability_status": "known" if available else "not_recorded",
        "curated_business_date": day,
        "mapped_product_id": product,
        "mapped_selling_location_id": location,
        "mapped_stock_location_id": stock,
        "mapped_channel": channel,
        "mapping_reference_ids": canonical_json(sorted(set(refs))).decode(),
        "quantity_unit": unit,
        "scoring_eligible": eligible,
    }


def verify_semantics(root: Any, document: dict[str, Any], scratch: Any, limits: Any) -> None:
    """Recompute causal projections from persisted source fields, including resealed files."""
    from retailops_ai.curated.builder import iter_rows
    from retailops_ai.curated.contract import source_contract
    from retailops_ai.source_snapshot.files import SnapshotError

    specs = source_contract("1.1.0")["fact_tables"]
    index = Index(scratch / "curated-semantics.sqlite", specs)
    config = Config(currencies=tuple(document["descriptor"]["config"]["currencies"]))
    try:
        for table in document["tables"]:
            name = table["table"]
            for row in iter_rows(root, table["files"], limits.batch_rows):
                index.add(name, {c["name"]: row[c["name"]] for c in specs[name]["schema"]})
        index.db.commit()
        for table in document["tables"]:
            name = table["table"]
            for row in iter_rows(root, table["files"], limits.batch_rows):
                raw = {c["name"]: row[c["name"]] for c in specs[name]["schema"]}
                try:
                    expected = transform_inventory(name, raw, specs[name]["grain"], index, config)
                except Reject as exc:
                    raise SnapshotError("invalid_inventory_curated_semantics") from exc
                if expected != row:
                    raise SnapshotError("inventory_curated_causal_projection_mismatch")
    finally:
        index.close()
