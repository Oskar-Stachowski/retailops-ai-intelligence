"""Verify the selected stream against native curated sales, without producer imports."""

import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, encoded
from retailops_ai.raw_dq.contract import SOURCE, TOPIC, Binding
from retailops_ai.raw_dq.replay import sale_fact
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256

TABLES = ("sales", "orders", "sale_price_references", "product_catalog", "inventory_sales")


def scalar(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def parent_facts(root: Path, binding: Binding) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    document = verify_curated(root)
    descriptor = document["descriptor"]
    if (
        document["schema_version"] != "1.2.0"
        or descriptor["parent_source_dataset_id"] != binding.source_dataset_id
        or binding.source_descriptor_sha256
        != binding.source_dataset_id.removeprefix("source-sha256-")
        or binding.source_facts_ready is not True
    ):
        raise SnapshotError("dq_parent_source_binding_mismatch")
    tables: dict[str, list[dict[str, Any]]] = {}
    count = size = 0
    with tempfile.TemporaryDirectory(prefix="dq-source-check-") as tmp:
        for name in TABLES:
            spec = next(t for t in document["tables"] if t["table"] == name)
            digest = Digest(
                Path(tmp) / (name + ".sqlite"), columns_for(name, "1.2.0"), spec["grain"]
            )
            tables[name] = []
            try:
                for row in iter_rows(root, spec["files"], 256):
                    count += 1
                    size += len(encoded(row))
                    if count > 100000 or size > 128 * 1024**2:
                        raise SnapshotError("dq_parent_input_limit")
                    digest.add(row)
                    tables[name].append(row)
                if any(spec[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("dq_parent_changed_during_load")
            finally:
                digest.close()
    sales = sorted(tables["sales"], key=lambda r: (r["sold_at"], r["id"]))
    if len(sales) != binding.source_sales_count or binding.source_event_count > len(sales):
        raise SnapshotError("dq_selected_source_count_mismatch")
    n = binding.source_event_count
    selected = (
        sales if len(sales) == n else [sales[i * (len(sales) - 1) // (n - 1)] for i in range(n)]
    )
    orders = {r["order_reference"]: r for r in tables["orders"]}
    refs = {r["sale_id"]: r for r in tables["sale_price_references"]}
    products = {r["id"]: r for r in tables["product_catalog"]}
    physical = {r["sale_id"]: r for r in tables["inventory_sales"]}
    events = []
    for sale in selected:
        order, ref = orders[sale["order_reference"]], refs[sale["id"]]
        occurred = scalar(sale["sold_at"])
        ingested = scalar(max(sale["ingested_at"], physical[sale["id"]]["available_at"]))
        payload = {
            "sale_id": sale["id"],
            "order_id": order["id"],
            "order_item_id": ref["order_item_id"],
            "product_id": sale["product_id"],
            "sku": products[sale["product_id"]]["sku"],
            "store_id": order["store_id"],
            "channel": sale["channel"],
            **{key: scalar(sale[key]) for key in ("quantity", "unit_price", "total_amount")},
            "currency": sale["currency"],
            "promotion_applied": sale["promotion_applied"],
        }
        revision = json_sha256(
            {"occurred_at": occurred, "ingested_at": ingested, "payload": payload}
        )
        namespace = uuid5(NAMESPACE_DNS, "retailops-demo-dataset-v1")
        natural = f"event:{descriptor['source_parameters']['seed']}:{SOURCE}:sale_completed:{sale['id']}:{revision}"
        events.append(
            {
                "event_id": str(uuid5(namespace, natural)),
                "event_type": "sale_completed",
                "schema_version": "1.0",
                "source": SOURCE,
                "topic": TOPIC,
                "correlation_id": order["id"],
                "occurred_at": occurred,
                "ingested_at": ingested,
                "payload": payload,
            }
        )
    if json_sha256(events) != binding.source_events_sha256:
        raise SnapshotError("dq_selected_source_projection_mismatch")
    return (
        {
            "curated_dataset_id": document["curated_dataset_id"],
            "curated_descriptor_sha256": json_sha256(descriptor),
            "source_dataset_id": descriptor["parent_source_dataset_id"],
            "snapshot_id": descriptor["parent_snapshot_id"],
            "qualification_id": descriptor["parent_qualification_id"],
        },
        {event["payload"]["sale_id"]: sale_fact(event) for event in events},
    )
