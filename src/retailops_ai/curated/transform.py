"""Disk-backed explicit mappings; no guesses for missing dimensions or observations."""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from retailops_ai.curated.contract import Config, cell, decoded, encoded, record_sha
from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.source_snapshot.protocol import contract_document


class Reject(ValueError):
    """A deterministic row reason, retained with source lineage in quarantine."""


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        k: unicodedata.normalize("NFC", v) if isinstance(v, str) else v for k, v in row.items()
    }
    for key in (
        "channel",
        "scope",
        "unit_of_measure",
        "pack_unit",
        "status",
        "margin_band",
        "location_type",
        "observation_status",
        "quality_status",
    ):
        if isinstance(result.get(key), str):
            result[key] = result[key].strip().lower()
    for key in ("currency", "country", "country_code"):
        if isinstance(result.get(key), str):
            result[key] = result[key].strip().upper()
    return result


class Index:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute(
            "CREATE TABLE records (kind TEXT, id TEXT, body BLOB, PRIMARY KEY(kind,id))"
        )
        self.db.execute(
            "CREATE TABLE intervals (kind TEXT, product TEXT, location TEXT, channel TEXT, legacy TEXT, start TEXT, end TEXT, available TEXT, version INTEGER, body BLOB)"
        )
        self.db.execute(
            "CREATE INDEX interval_lookup ON intervals(kind,location,channel,start,end,available)"
        )
        self.db.execute(
            "CREATE INDEX sale_reference_lookup ON records(json_extract(CAST(body AS TEXT),'$.sale_id')) WHERE kind='sale_price_references'"
        )
        self.db.execute(
            "CREATE INDEX history_lookup ON records(json_extract(CAST(body AS TEXT),'$.observation_id'),json_extract(CAST(body AS TEXT),'$.version')) WHERE kind='daily_demand_versions'"
        )
        self.columns = {k: v["schema"] for k, v in contract_document()["fact_tables"].items()}

    def add(self, table: str, raw: dict[str, Any]) -> None:
        row = normalize(raw)
        self.db.execute("INSERT INTO records VALUES (?,?,?)", (table, row["id"], encoded(row)))
        if table in {"channel_assignments", "fulfillment_routes", "assortment"}:
            self.db.execute(
                "INSERT INTO intervals VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    table,
                    row.get("product_id", ""),
                    row["selling_location_id"],
                    row["channel"],
                    row.get("legacy_store_id", ""),
                    cell(row["effective_from"]),
                    cell(row["effective_to"]),
                    cell(row["available_at"]),
                    row["version"],
                    encoded(row),
                ),
            )

    def get(self, table: str, identifier: str) -> dict[str, Any]:
        result = self.db.execute(
            "SELECT body FROM records WHERE kind=? AND id=?", (table, identifier)
        ).fetchone()
        if result is None:
            raise Reject("missing_" + table + "_reference")
        return decoded(result[0], self.columns[table])

    def interval(
        self,
        table: str,
        location: str,
        channel: str,
        day: date,
        origin: datetime,
        product: str = "",
        legacy: str | None = None,
    ) -> dict[str, Any]:
        rows = self.db.execute(
            "SELECT body FROM intervals WHERE kind=? AND location=? AND channel=? AND product=? AND start<=? AND end>? AND available<=? ORDER BY version DESC LIMIT 1025",
            (table, location, channel, product, day.isoformat(), day.isoformat(), cell(origin)),
        ).fetchall()
        if len(rows) > 1024:
            raise Reject("mapping_version_limit")
        matches = [decoded(r[0], self.columns[table]) for r in rows]
        if legacy is not None:
            matches = [r for r in matches if r.get("legacy_store_id") == legacy]
        if not matches:
            raise Reject("missing_available_" + table + "_mapping")
        highest = matches[0]["version"]
        top = [r for r in matches if r["version"] == highest]
        if len(top) != 1:
            raise Reject("ambiguous_" + table + "_mapping")
        return top[0]

    def legacy_assignment(
        self, legacy: str, channel: str, day: date, origin: datetime
    ) -> dict[str, Any]:
        rows = self.db.execute(
            "SELECT DISTINCT location FROM intervals WHERE kind='channel_assignments' AND legacy=? AND channel=? AND start<=? AND end>? AND available<=?",
            (legacy, channel, day.isoformat(), day.isoformat(), cell(origin)),
        ).fetchall()
        if len(rows) != 1:
            raise Reject("missing_or_ambiguous_legacy_store_mapping")
        return self.interval("channel_assignments", rows[0][0], channel, day, origin, legacy=legacy)

    def close(self) -> None:
        self.db.close()


def basic(row: dict[str, Any], config: Config) -> None:
    for key, allowed in {
        "country": {"PL", "DE"},
        "country_code": {"PL", "DE"},
        "margin_band": {"low", "standard", "high"},
        "location_type": {"warehouse", "store"},
        "status": {
            "active",
            "inactive",
            "completed",
            "pending",
            "confirmed",
            "cancelled",
            "refunded",
            "rejected",
        },
    }.items():
        if row.get(key) is not None and row[key] not in allowed:
            raise Reject("unknown_" + key)
    if row.get("channel") not in (None, "all", "store", "online", "marketplace", "wholesale"):
        raise Reject("unknown_channel")
    if row.get("currency") not in (None, *config.currencies):
        raise Reject("unsupported_currency")
    if row.get("business_timezone") not in (None, "UTC"):
        raise Reject("unsupported_business_timezone")
    if "effective_from" in row and row["effective_from"] >= row["effective_to"]:
        raise Reject("invalid_effective_interval")
    if "version" in row and row["version"] < 1:
        raise Reject("invalid_version")
    if row.get("scope") not in (None, "global", "channel", "location", "location_channel"):
        raise Reject("unknown_scope")
    if "scope" in row:
        scope = row["scope"]
        if (scope in {"location", "location_channel"}) != (
            row.get("selling_location_id") is not None
        ) or (scope in {"channel", "location_channel"}) != (row["channel"] != "all"):
            raise Reject("scope_mapping_mismatch")
    if "known_at" in row and "available_at" in row and row["known_at"] > row["available_at"]:
        raise Reject("available_before_known")
    if "sku" in row and (not row["sku"] or any(c.isspace() for c in row["sku"])):
        raise Reject("invalid_sku")
    if any(
        row[k] < 0
        for k in ("quantity", "observed_units", "observed_orders", "return_units", "pack_quantity")
        if row.get(k) is not None
    ):
        raise Reject("negative_quantity")


def transform(
    table: str, raw: dict[str, Any], grain: list[str], index: Index, config: Config
) -> dict[str, Any]:
    row = normalize(raw)
    basic(row, config)
    available = [
        row[k]
        for k in ("available_at", "ingested_at", "known_at")
        if isinstance(row.get(k), datetime)
    ]
    day = row.get("business_date")
    for key in ("sold_at", "ordered_at", "returned_at"):
        if day is None and isinstance(row.get(key), datetime):
            day = row[key].date()
    product = row.get("product_id") or (
        row["id"] if table in {"products", "product_catalog"} else None
    )
    location, channel = row.get("selling_location_id"), row.get("channel")
    stock, unit, refs = row.get("stock_location_id"), None, []
    catalog = None
    if product is not None:
        index.get("products", product)
        catalog = index.get("product_catalog", product)
        basic(catalog, config)
        if (
            catalog["unit_of_measure"] != "pcs"
            or catalog["pack_quantity"] < 1
            or catalog["pack_unit"] not in {"g", "ml", "pcs"}
        ):
            raise Reject("unsupported_product_unit")
        index.get("catalog_categories", catalog["category_id"])
        if row.get("currency") not in (None, catalog["currency"]):
            raise Reject("currency_product_mismatch")
        unit = "pcs"
        available.append(catalog["available_at"])
        refs.append(catalog["id"])
    if table == "order_items":
        order = index.get("orders", row["order_id"])
        day, channel = order["ordered_at"].date(), order["channel"]
        assignment = index.legacy_assignment(order["store_id"], channel, day, order["ordered_at"])
        location = assignment["selling_location_id"]
        refs.extend([order["id"], assignment["id"]])
        # order_items has no historical ingestion/availability time in source v2.6.
    if table == "orders":
        assignment = index.legacy_assignment(
            row["store_id"], row["channel"], row["ordered_at"].date(), row["ordered_at"]
        )
        location = assignment["selling_location_id"]
        refs.append(assignment["id"])
    if table == "sales":
        result = index.db.execute(
            "SELECT body FROM records WHERE kind='sale_price_references' AND json_extract(CAST(body AS TEXT),'$.sale_id')=?",
            (row["id"],),
        ).fetchall()
        if len(result) != 1:
            raise Reject("missing_or_ambiguous_sale_reference")
        ref = decoded(result[0][0], index.columns["sale_price_references"])
        if ref["product_id"] != product or ref["channel"] != channel or ref["business_date"] != day:
            raise Reject("sale_reference_grain_mismatch")
        location = ref["selling_location_id"]
        refs.append(ref["id"])
    if row.get("category_id") is not None:
        index.get("catalog_categories", row["category_id"])
    if location is not None:
        index.get("selling_locations", location)
    if stock is not None:
        index.get("stock_locations", stock)
    if table == "channel_assignments":
        index.get("stores", row["legacy_store_id"])
    if table == "return_events":
        index.get("sales", row["sale_id"])
        index.get("orders", row["order_id"])
        index.get("order_items", row["order_item_id"])
        index.get("return_policies", row["policy_id"])
    if table == "sale_price_references":
        for kind, key in (
            ("sales", "sale_id"),
            ("order_items", "order_item_id"),
            ("price_plans", "price_plan_id"),
            ("promotion_plans", "promotion_plan_id"),
        ):
            if row[key] is not None:
                index.get(kind, row[key])
    if table in {"daily_demand_observations", "daily_demand_versions"}:
        if (
            row.get("observed_units") is None
            or row["observation_status"] in {"missing", "unknown", "data_gap"}
            or row.get("source_data_complete") is False
            or row.get("quality_status", "valid") != "valid"
        ):
            raise Reject("source_data_gap_or_invalid_observation")
        if row["observation_status"] not in {"closed", "observed_zero", "observed_positive"}:
            raise Reject("unknown_observation_status")
        if (
            row["observation_status"] in {"closed", "observed_zero"} and row["observed_units"] != 0
        ) or (row["observation_status"] == "observed_positive" and row["observed_units"] <= 0):
            raise Reject("observation_status_quantity_mismatch")
        if table == "daily_demand_versions":
            observation = index.get("daily_demand_observations", row["observation_id"])
            if any(
                row[k] != observation[k]
                for k in ("business_date", "product_id", "selling_location_id", "channel")
            ):
                raise Reject("observation_history_grain_mismatch")
            previous = index.db.execute(
                "SELECT body FROM records WHERE kind='daily_demand_versions' AND json_extract(CAST(body AS TEXT),'$.observation_id')=? AND json_extract(CAST(body AS TEXT),'$.version')=?",
                (row["observation_id"], row["version"] - 1),
            ).fetchall()
            if row["version"] > 1 and len(previous) != 1:
                raise Reject("observation_history_version_gap")
            if (
                previous
                and decoded(previous[0][0], index.columns[table])["available_at"]
                > row["available_at"]
            ):
                raise Reject("observation_history_availability_regression")
    if day is not None and location is not None and channel not in (None, "all"):
        # A return is mapped to its original selling location; route on return date
        # can end before the declared return tail and is not a new shipment.
        mapping_day = day
        if table == "return_events":
            mapping_day = index.get("sales", row["sale_id"])["sold_at"].date()
        origin = max(available, default=datetime.combine(day, datetime.max.time(), UTC))
        assignment = index.interval("channel_assignments", location, channel, mapping_day, origin)
        route = index.interval("fulfillment_routes", location, channel, mapping_day, origin)
        index.get("stock_locations", route["stock_location_id"])
        refs.extend([assignment["id"], route["id"]])
        stock = route["stock_location_id"]
        if available:
            available.extend([assignment["available_at"], route["available_at"]])
        if product is not None and table != "daily_demand_exclusions":
            assortment = index.interval(
                "assortment", location, channel, mapping_day, origin, product
            )
            refs.append(assortment["id"])
            if available:
                available.append(assortment["available_at"])
            if catalog is None:
                raise Reject("missing_product_catalog_reference")
            if mapping_day < catalog["launch_date"] or (
                catalog["discontinue_date"] is not None
                and mapping_day >= catalog["discontinue_date"]
            ):
                raise Reject("inactive_product_lifecycle")
    # Legacy/static source records without availability remain explicitly unknown.
    if table in {
        "products",
        "stores",
        "warehouses",
        "selling_locations",
        "stock_locations",
        "catalog_categories",
        "orders",
        "order_items",
        "daily_demand_exclusions",
    }:
        available = []
    eligible = table == "daily_demand_versions" and row["observation_status"] != "closed"
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
