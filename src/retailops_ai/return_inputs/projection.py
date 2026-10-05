"""Project visible operational returns using the original purchased line and route."""

from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from retailops_ai.return_inputs.contract import TABLES, Point, Policy
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256

Row = dict[str, Any]
KEY = ("product_id", "selling_location_id", "channel", "currency")


def unique(rows: list[Row], key: str) -> dict[str, Row]:
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise SnapshotError("duplicate_return_source_key")
    return result


class Returns:
    def __init__(self, tables: dict[str, list[Row]], policy: Policy) -> None:
        if set(tables) != set(TABLES):
            raise SnapshotError("return_source_table_allowlist_mismatch")
        self.policy = policy
        sales = unique(tables["inventory_sales"], "sale_id")
        refs = unique(tables["sale_price_references"], "sale_id")
        catalog = unique(tables["product_catalog"], "id")
        policies = unique(tables["return_policies"], "id")
        unique(tables["return_events"], "id")
        self.groups: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
        self.series: set[tuple[Any, ...]] = set()
        for sale in sales.values():
            if (
                sale["curated_available_at"] is not None
                and sale["curated_available_at"] <= policy.as_of_time
                and sale["sold_at"] <= policy.as_of_time
            ):
                self.series.add(tuple(sale[k] for k in KEY))
        claims: Counter[str] = Counter()
        for event in sorted(tables["return_events"], key=lambda r: (r["returned_at"], r["id"])):
            available = event["curated_available_at"]
            # Future or unavailable records do not influence a historical view.
            if (
                available is None
                or available > policy.as_of_time
                or event["returned_at"] > policy.as_of_time
            ):
                continue
            try:
                sale = sales[event["sale_id"]]
                ref = refs[event["sale_id"]]
                product = catalog[event["product_id"]]
                rule = policies[event["policy_id"]]
            except KeyError as exc:
                raise SnapshotError("missing_return_purchase_or_policy") from exc
            if (
                any(event[k] != sale[k] for k in KEY)
                or any(
                    event[k] != ref[k]
                    for k in ("product_id", "selling_location_id", "channel", "order_item_id")
                )
                or event["order_id"] != sale["order_id"]
                or event["currency"] != product["currency"]
                or event["mapped_stock_location_id"] != sale["stock_location_id"]
                or rule["channel"] != event["channel"]
                or rule["category_id"] != product["category_id"]
                or event["returns_policy_version"] != rule["returns_policy_version"]
                or any(
                    r["curated_available_at"] is None or r["curated_available_at"] > available
                    for r in (sale, ref, product, rule)
                )
                or not rule["known_at"] <= sale["ordered_at"] <= sale["sold_at"]
                or not sale["sold_at"] + timedelta(days=1)
                <= event["returned_at"]
                <= sale["sold_at"] + timedelta(days=rule["window_days"])
                or not event["returned_at"]
                <= event["ingested_at"]
                <= event["available_at"]
                <= event["returned_at"] + timedelta(days=rule["max_ingestion_delay_days"])
                or available < event["available_at"]
            ):
                raise SnapshotError("invalid_return_causal_reference_or_window")
            if event["status"] not in {"refunded", "rejected"} or event["quantity"] <= 0:
                raise SnapshotError("invalid_return_claim")
            claims[event["sale_id"]] += event["quantity"]
            expected = (
                sale["unit_price"] * event["quantity"]
                if event["status"] == "refunded"
                else Decimal(0)
            )
            if claims[event["sale_id"]] > sale["quantity"] or event["refund_amount"] != expected:
                raise SnapshotError("invalid_return_quantity_or_refund")
            day = event["returned_at"].date()
            if policy.start_date <= day <= policy.end_date:
                self.groups[(day, *(event[k] for k in KEY))].append(event)

    def point(self, day: date, series: tuple[Any, ...]) -> Point:
        members = sorted(self.groups.get((day, *series), []), key=lambda r: r["id"])
        return Point(
            product_id=series[0],
            selling_location_id=series[1],
            channel=series[2],
            currency=series[3],
            business_date=day,
            as_of_time=self.policy.as_of_time,
            known_event_count=len(members),
            known_refunded_units=sum(r["quantity"] for r in members if r["status"] == "refunded"),
            known_rejected_units=sum(r["quantity"] for r in members if r["status"] == "rejected"),
            known_refund_amount=format(
                sum((r["refund_amount"] for r in members), Decimal(0)), ".2f"
            ),
            source_stock_location_ids=tuple(
                sorted({r["mapped_stock_location_id"] for r in members})
            ),
            member_records_sha256=json_sha256(
                [
                    {
                        "id": r["id"],
                        "source_record_sha256": r["source_record_sha256"],
                        "available_at": r["curated_available_at"].isoformat(),
                    }
                    for r in members
                ]
            ),
            latest_member_available_at=max(
                (r["curated_available_at"] for r in members), default=None
            ),
        )

    def rows(self) -> Iterator[Point]:
        for series in sorted(self.series):
            day = self.policy.start_date
            while day <= self.policy.end_date:
                yield self.point(day, series)
                day += timedelta(days=1)
