"""Rebuild the complete projection from verified public parents, without sampling."""

import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from retailops_ai.curated.builder import build_curated, iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, encoded
from retailops_ai.full_raw_dq.contract import Binding
from retailops_ai.raw_dq.contract import SOURCE, TOPIC
from retailops_ai.raw_dq.source import scalar
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256
from retailops_ai.source_snapshot.importer import verify_import

Row = dict[str, Any]
Key = tuple[str, str]
TABLES = (
    "sales",
    "orders",
    "sale_price_references",
    "product_catalog",
    "inventory_sales",
    "return_events",
)
OPTIONAL = {"sale_completed": "sku", "return_completed": "order_id"}


def business_key(event: Row) -> Key:
    kind = event["event_type"]
    identifier = event["payload"]["sale_id" if kind == "sale_completed" else "return_id"]
    if kind not in OPTIONAL or not isinstance(identifier, str) or not identifier:
        raise SnapshotError("full_dq_invalid_business_key")
    return kind, identifier


class ParentFacts:
    def __init__(self, events: list[Row], facts: dict[Key, Row]) -> None:
        self.events = deepcopy(events)
        self.canonical = {business_key(e): e for e in self.events}
        self.ids = {e["event_id"]: business_key(e) for e in self.events}
        self.facts = deepcopy(facts)
        if not len(self.events) == len(self.canonical) == len(self.ids) == len(facts):
            raise SnapshotError("full_dq_repeated_parent_key")

    def match(self, event: Row) -> tuple[Key, Row]:
        key = business_key(event)
        expected = deepcopy(self.canonical[key])
        # UUID is provenance. Optional context cannot change any operational fact.
        expected["event_id"] = event["event_id"]
        context = OPTIONAL[key[0]]
        if context not in event["payload"]:
            expected["payload"].pop(context)
        if event != expected or (
            event["event_id"] in self.ids and self.ids[event["event_id"]] != key
        ):
            raise SnapshotError("full_dq_canonical_operational_mismatch")
        return key, deepcopy(self.facts[key])


def projection(tables: dict[str, list[Row]], seed: int) -> ParentFacts:
    orders = {r["order_reference"]: r for r in tables["orders"]}
    order_ids = {r["id"]: r for r in tables["orders"]}
    refs = {r["sale_id"]: r for r in tables["sale_price_references"]}
    products = {r["id"]: r for r in tables["product_catalog"]}
    physical = {r["sale_id"]: r for r in tables["inventory_sales"]}
    events: list[Row] = []
    facts: dict[Key, Row] = {}
    for kind, rows in (
        ("sale_completed", tables["sales"]),
        ("return_completed", tables["return_events"]),
    ):
        for row in rows:
            is_sale = kind == "sale_completed"
            native_sale = physical[row["id"] if is_sale else row["sale_id"]]
            order = orders[row["order_reference"]] if is_sale else order_ids[row["order_id"]]
            occurred = scalar(row["sold_at" if is_sale else "returned_at"])
            ingested = scalar(
                max(row["ingested_at"], native_sale["available_at"])
                if is_sale
                else row["available_at"]
            )
            payload: Row = {
                "sale_id" if is_sale else "return_id": row["id"],
                "order_id": order["id"],
                "order_item_id": refs[row["id"]]["order_item_id"]
                if is_sale
                else row["order_item_id"],
                "product_id": row["product_id"],
                "store_id": order["store_id"],
                "channel": row["channel"],
                "quantity": str(row["quantity"]),
            }
            if is_sale:
                payload.update(
                    sku=products[row["product_id"]]["sku"],
                    unit_price=scalar(row["unit_price"]),
                    total_amount=scalar(row["total_amount"]),
                    currency=row["currency"],
                    promotion_applied=row["promotion_applied"],
                )
            else:
                payload.update(refund_amount=scalar(row["refund_amount"]), reason=row["reason"])
                if row["status"] not in {"refunded", "rejected"}:
                    raise SnapshotError("full_dq_unknown_native_return_status")
            revision = json_sha256(
                {"occurred_at": occurred, "ingested_at": ingested, "payload": payload}
            )
            natural = f"event:{seed}:{SOURCE}:{kind}:{row['id']}:{revision}"
            event = {
                "event_id": str(uuid5(uuid5(NAMESPACE_DNS, "retailops-demo-dataset-v1"), natural)),
                "event_type": kind,
                "schema_version": "1.0",
                "source": SOURCE,
                "topic": TOPIC,
                "correlation_id": order["id"],
                "occurred_at": occurred,
                "ingested_at": ingested,
                "payload": payload,
            }
            fact = {
                "event_type": kind,
                "business_id": row["id"],
                "business_date": occurred[:10],
                "product_id": row["product_id"],
                "selling_location_id": native_sale["selling_location_id"],
                "stock_location_id": native_sale["stock_location_id"],
                "channel": native_sale["channel"],
                "currency": row["currency"],
                "quantity": row["quantity"],
                "amount": scalar(row["total_amount" if is_sale else "refund_amount"]),
                "status": "sold" if is_sale else row["status"],
                "occurred_at": occurred,
                "ingested_at": ingested,
            }
            events.append(event)
            facts[kind, row["id"]] = {**fact, "business_version_sha256": json_sha256(fact)}
    events.sort(key=lambda e: (e["occurred_at"], *business_key(e)))
    return ParentFacts(events, facts)


def parent_facts(curated_dir: Path, import_dir: Path, binding: Binding) -> tuple[Row, ParentFacts]:
    snapshot = verify_import(import_dir, required_use_cases=("anomaly_source",))
    document = verify_curated(curated_dir)
    descriptor = document["descriptor"]
    source = snapshot.manifest["source"]["descriptor"]
    if (
        snapshot.manifest["schema_version"] != "1.2.0"
        or document["schema_version"] != "1.2.0"
        or snapshot.source_id != binding.source_dataset_id
        or binding.source_dataset_id != "source-sha256-" + binding.source_descriptor_sha256
        or descriptor["parent_snapshot_id"] != snapshot.snapshot_id
        or descriptor["parent_source_dataset_id"] != snapshot.source_id
        or descriptor["parent_qualification_id"]
        != snapshot.manifest["descriptor"]["parent_qualification_id"]
        or binding.source_event_count != binding.source_sales_count + binding.source_return_count
        or binding.projection_tables["sales"].row_count != binding.source_sales_count
        or binding.projection_tables["return_events"].row_count != binding.source_return_count
        or binding.model_dump(mode="json")["projection_tables"]
        != {name: source["tables"][name] for name in binding.projection_tables}
    ):
        raise SnapshotError("full_dq_parent_binding_mismatch")
    tables: dict[str, list[Row]] = {}
    count = size = 0
    with tempfile.TemporaryDirectory(prefix="full-dq-parent-") as tmp:
        # Full reconstruction proves the declared source → curated link, including
        # reference availability and native routes. A resealed parent is insufficient.
        rebuilt = build_curated(import_dir, Path(tmp).resolve() / "data/generated")
        if rebuilt.manifest != document:
            raise SnapshotError("full_dq_curated_parent_semantic_mismatch")
        for name in TABLES:
            spec = next(t for t in document["tables"] if t["table"] == name)
            digest = Digest(
                Path(tmp) / (name + ".sqlite"), columns_for(name, "1.2.0"), spec["grain"]
            )
            tables[name] = []
            try:
                for row in iter_rows(curated_dir, spec["files"], 256):
                    count += 1
                    size += len(encoded(row))
                    if count > 100000 or size > 128 * 1024**2:
                        raise SnapshotError("full_dq_parent_input_limit")
                    digest.add(row)
                    tables[name].append(row)
                if any(spec[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("full_dq_parent_changed_during_load")
            finally:
                digest.close()
    parent = projection(tables, descriptor["source_parameters"]["seed"])
    if (
        len(tables["sales"]) != binding.source_sales_count
        or len(tables["return_events"]) != binding.source_return_count
        or len(parent.events) != binding.source_event_count
        or json_sha256(parent.events) != binding.source_events_sha256
    ):
        raise SnapshotError("full_dq_full_source_projection_mismatch")
    return {
        "curated_dataset_id": document["curated_dataset_id"],
        "curated_descriptor_sha256": json_sha256(descriptor),
        "source_dataset_id": snapshot.source_id,
        "snapshot_id": snapshot.snapshot_id,
        "qualification_id": descriptor["parent_qualification_id"],
    }, parent
