"""Bounded state reduction of operational records, with immutable as-of revisions."""

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from retailops_ai.raw_dq.contract import (
    MAX_RECORDS,
    SOURCE,
    TOPIC,
    VERSION,
    Delivery,
    Progress,
    contract_bytes,
    parse_capture,
    stamp,
)
from retailops_ai.source_snapshot.files import SnapshotError, decode_json, json_sha256

GRAIN = ("business_date", "product_id", "store_id", "channel", "currency")
PAYLOAD_FIELDS = frozenset(
    {
        "sale_id",
        "order_id",
        "order_item_id",
        "product_id",
        "sku",
        "store_id",
        "channel",
        "quantity",
        "unit_price",
        "total_amount",
        "currency",
        "promotion_applied",
    }
)
Row = dict[str, Any]


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    schema = decode_json(contract_bytes("realtime-events.schema.json"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def sale_fact(event: Row) -> Row:
    if (
        event.get("event_type") != "sale_completed"
        or event.get("source") != SOURCE
        or event.get("topic") != TOPIC
        or event.get("schema_version") != "1.0"
        or next(validator().iter_errors(event), None) is not None
    ):
        raise SnapshotError("dq_invalid_sales_contract")
    payload = event["payload"]
    if not set(payload) <= PAYLOAD_FIELDS:
        raise SnapshotError("dq_nonoperational_sales_field")
    if not payload.get("sale_id") or payload.get("currency") not in {"PLN", "EUR"}:
        raise SnapshotError("dq_invalid_business_key_or_currency")
    quantity, amount = Decimal(payload["quantity"]), Decimal(payload["total_amount"])
    price = Decimal(payload["unit_price"]) if "unit_price" in payload else None
    if (
        not quantity.is_finite()
        or not amount.is_finite()
        or not 0 < quantity <= 10**9
        or quantity != quantity.to_integral_value()
        or not 0 <= amount <= 10**12
        or amount != amount.quantize(Decimal("0.01"))
        or (
            price is not None
            and (
                not price.is_finite()
                or not 0 <= price <= 10**12
                or price != price.quantize(Decimal("0.01"))
                or price * quantity != amount
            )
        )
    ):
        raise SnapshotError("dq_invalid_sale_units_or_money")
    occurred, ingested = stamp(event["occurred_at"]), stamp(event["ingested_at"])
    if occurred > ingested:
        raise SnapshotError("dq_ingestion_before_event")
    business = {k: v for k, v in payload.items() if k != "sku"}
    business.update(quantity=str(int(quantity)), total_amount=f"{amount:.2f}")
    if price is not None:
        business["unit_price"] = f"{price:.2f}"
    return {
        "source": event["source"],
        "sale_id": payload["sale_id"],
        "business_date": occurred.date().isoformat(),
        "product_id": payload["product_id"],
        "store_id": payload["store_id"],
        "channel": payload["channel"],
        "currency": payload["currency"],
        "quantity": int(quantity),
        "total_amount": f"{amount:.2f}",
        "occurred_at": occurred.isoformat(),
        "ingested_at": ingested.isoformat(),
        "business_version_sha256": json_sha256(
            {"occurred_at": occurred.isoformat(), "payload": business}
        ),
    }


def totals(facts: list[Row]) -> list[Row]:
    grouped: dict[tuple[Any, ...], Row] = {}
    for fact in facts:
        key = tuple(fact[k] for k in GRAIN)
        row = grouped.setdefault(
            key,
            {
                **dict(zip(GRAIN, key, strict=True)),
                "quantity": 0,
                "total_amount": "0.00",
                "fact_count": 0,
            },
        )
        row["quantity"] += fact["quantity"]
        row["total_amount"] = f"{Decimal(row['total_amount']) + Decimal(fact['total_amount']):.2f}"
        row["fact_count"] += 1
    return [grouped[k] for k in sorted(grouped)]


class Replay:
    """A single partition, independent of producer code, truth, DB and brokers."""

    def __init__(self, expected_facts: dict[str, Row] | None = None) -> None:
        self.expected = expected_facts
        self.receipts: dict[str, Row] = {}
        self.events: dict[str, str] = {}
        self.business: dict[tuple[str, str], Row] = {}
        self.facts: list[Row] = []
        self.revisions: list[Row] = []
        self.progress: list[Row] = []
        self.quarantine: list[Row] = []
        self.next_offset = 0
        self.last_received: str | None = None
        self.max_event_time: str | None = None
        self.watermark: str | None = None

    def consume(self, payload: Row) -> Row:
        record = parse_capture(payload)
        if record.record_id in self.receipts:
            return deepcopy(self.receipts[record.record_id])
        if len(self.receipts) >= MAX_RECORDS:
            raise SnapshotError("dq_record_count_limit")
        if self.last_received is not None and stamp(record.received_at) < stamp(self.last_received):
            raise SnapshotError("dq_delivery_clock_regressed")
        if isinstance(record, Progress):
            if record.after_offset != self.next_offset - 1 or (
                self.watermark is not None
                and stamp(record.complete_through) <= stamp(self.watermark)
            ):
                raise SnapshotError("dq_invalid_progress_position_or_frontier")
            self.watermark = record.complete_through
            self.progress.append(
                {
                    "raw_ref": record.record_id,
                    "known_at": record.received_at,
                    "complete_through": stamp(record.complete_through).isoformat(),
                    "after_offset": record.after_offset,
                    "scope": record.scope,
                }
            )
            result = {
                "raw_ref": record.record_id,
                "action": "progress",
                "reason": "declared_selected_stream_frontier",
            }
        else:
            if record.offset != self.next_offset:
                raise SnapshotError("dq_capture_offset_rewrite_or_gap")
            result = self._delivery(record)
            self.next_offset += 1
        self.last_received = record.received_at
        self.receipts[record.record_id] = result
        return deepcopy(result)

    def _delivery(self, record: Delivery) -> Row:
        reason = None
        event: Row = {}
        try:
            event = decode_json(record.body_utf8.encode())
        except (ValueError, TypeError):
            reason = "invalid_json"
        if reason is None and event.get("schema_version") != "1.0":
            reason = "unsupported_schema_version"
        fact: Row = {}
        if reason is None:
            try:
                fact = sale_fact(event)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                reason = "invalid_sales_contract"
        if reason is None and stamp(fact["ingested_at"]) > stamp(record.received_at):
            reason = "fact_unavailable_at_delivery"
        event_hash = json_sha256(event)
        action = "accepted"
        key = fact.get("source", ""), fact.get("sale_id", "")
        if reason is None and event["event_id"] in self.events:
            if event_hash != self.events[event["event_id"]]:
                reason = "event_id_content_conflict"
            else:
                action = "duplicate_event"
                reason = "same_event_id_and_content"
        elif reason is None and key in self.business:
            if fact["business_version_sha256"] != self.business[key]["business_version_sha256"]:
                reason = "business_revision_requires_explicit_version"
            else:
                action = "duplicate_business"
                reason = "same_business_fact"
        if (
            reason is None
            and self.expected is not None
            and self.expected.get(fact["sale_id"]) != fact
        ):
            reason = "canonical_source_fact_mismatch"
        if reason is not None and action == "accepted":
            action = "quarantined"
            import hashlib

            self.quarantine.append(
                {
                    "raw_ref": record.record_id,
                    "action": action,
                    "reason": reason,
                    "topic": record.topic,
                    "partition": record.partition,
                    "offset": record.offset,
                    "received_at": record.received_at,
                    "body_sha256": hashlib.sha256(record.body_utf8.encode()).hexdigest(),
                }
            )
        elif action == "accepted":
            occurred = fact["occurred_at"]
            reason = (
                "late"
                if self.watermark is not None and stamp(occurred) <= stamp(self.watermark)
                else "out_of_order"
                if self.max_event_time is not None and stamp(occurred) < stamp(self.max_event_time)
                else "on_time"
            )
            self.max_event_time = max(self.max_event_time or occurred, occurred)
            self.business[key] = fact
            self.events[event["event_id"]] = event_hash
            self.facts.append(
                {
                    **fact,
                    "event_id": event["event_id"],
                    "raw_ref": record.record_id,
                    "available_at": record.received_at,
                    "timing_status": reason,
                }
            )
            grain = tuple(fact[k] for k in GRAIN)
            previous = next(
                (
                    r["revision_id"]
                    for r in reversed(self.revisions)
                    if tuple(r[k] for k in GRAIN) == grain
                ),
                None,
            )
            revision = {
                **totals([r for r in self.facts if tuple(r[k] for k in GRAIN) == grain])[0],
                "known_at": record.received_at,
                "source_raw_ref": record.record_id,
                "source_offset": record.offset,
                "previous_revision_id": previous,
                "quality_status": "partial_selected_sales_fixture",
            }
            self.revisions.append(
                {"revision_id": "sales-revision-sha256-" + json_sha256(revision), **revision}
            )
        elif action == "duplicate_business":
            self.events[event["event_id"]] = event_hash
        return {"raw_ref": record.record_id, "action": action, "reason": reason}

    def aggregates_as_of(self, cutoff: str) -> list[Row]:
        at = stamp(cutoff)
        latest = {}
        for revision in self.revisions:
            if stamp(revision["known_at"]) <= at:
                latest[tuple(revision[k] for k in GRAIN)] = revision
        return deepcopy([latest[k] for k in sorted(latest)])

    def snapshot(self) -> Row:
        actions = Counter(r["action"] for r in self.receipts.values())
        timing = Counter(r["timing_status"] for r in self.facts)
        return deepcopy(
            {
                "report": {
                    "policy_version": VERSION,
                    "status": "completed",
                    "scope": "offline_selected_sales_only",
                    "input_records": len(self.receipts),
                    "raw_events": self.next_offset,
                    **{
                        key: actions[key]
                        for key in (
                            "accepted",
                            "duplicate_event",
                            "duplicate_business",
                            "quarantined",
                        )
                    },
                    "dlq_fixture": len(self.quarantine),
                    "progress_declarations": len(self.progress),
                    "late": timing["late"],
                    "out_of_order": timing["out_of_order"],
                    "aggregate_revisions": len(self.revisions),
                    "declared_source_watermark": self.watermark,
                    "max_accepted_event_time": self.max_event_time,
                    "watermark_meaning": "explicit_selected_stream_progress_not_curated_completeness",
                    "curated_completeness": "not_qualified",
                    "transport_durability_proven": False,
                },
                "receipts": list(self.receipts.values()),
                "accepted_facts": self.facts,
                "aggregate_revisions": self.revisions,
                "final_aggregates": totals(self.facts),
                "progress": self.progress,
                "quarantine": self.quarantine,
                "dlq_fixture": [
                    {
                        "target": "retailops.dlq.v1",
                        "raw_ref": r["raw_ref"],
                        "reason": r["reason"],
                        "status": "offline_only",
                    }
                    for r in self.quarantine
                ],
            }
        )
