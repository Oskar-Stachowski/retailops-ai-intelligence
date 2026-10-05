"""Bounded operational state, per-parent coverage and immutable delivery-time views."""

import hashlib
import json
import math
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal
from typing import Any
from uuid import UUID

from retailops_ai.full_raw_dq.contract import (
    MAX_RECORDS,
    SCOPE,
    VERSION,
    Delivery,
    Progress,
    parse_capture,
)
from retailops_ai.full_raw_dq.source import Key, ParentFacts
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.raw_dq.replay import validator
from retailops_ai.source_snapshot.files import SnapshotError, json_sha256, nonfinite, unique_keys

Row = dict[str, Any]
GRAIN = ("event_type", "business_date", "product_id", "selling_location_id", "channel", "currency")


def finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise SnapshotError("nonfinite_json_number")
    return value


def totals(facts: list[Row]) -> list[Row]:
    groups: dict[tuple[Any, ...], Row] = {}
    for fact in facts:
        key = tuple(fact[k] for k in GRAIN)
        row = groups.setdefault(
            key,
            {
                **dict(zip(GRAIN, key, strict=True)),
                "fact_count": 0,
                "claim_units": 0,
                "units": 0,
                "rejected_units": 0,
                "amount": "0.00",
                "stock_location_ids": [],
            },
        )
        row["fact_count"] += 1
        row["claim_units"] += fact["quantity"]
        row["rejected_units" if fact["status"] == "rejected" else "units"] += fact["quantity"]
        row["amount"] = f"{Decimal(row['amount']) + Decimal(fact['amount']):.2f}"
        row["stock_location_ids"] = sorted(
            set(row["stock_location_ids"]) | {fact["stock_location_id"]}
        )
    return [groups[k] for k in sorted(groups)]


class Replay:
    def __init__(self, parent: ParentFacts) -> None:
        self.parent = parent
        self.receipts: dict[str, Row] = {}
        self.events: dict[str, str] = {}
        self.business: set[Key] = set()
        self.facts: list[Row] = []
        self.revisions: list[Row] = []
        self.latest: dict[tuple[Any, ...], Row] = {}
        self.groups: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
        self.progress: list[Row] = []
        self.quarantine: list[Row] = []
        self.next_offset = 0
        self.last_received: str | None = None
        self.watermark: str | None = None
        self.max_event_time: str | None = None

    def consume(self, payload: Row) -> Row:
        record = parse_capture(payload)
        if record.record_id in self.receipts:
            return deepcopy(self.receipts[record.record_id])
        if len(self.receipts) >= MAX_RECORDS:
            raise SnapshotError("full_dq_record_limit")
        if self.last_received is not None and stamp(record.received_at) < stamp(self.last_received):
            raise SnapshotError("full_dq_delivery_time_regressed")
        if isinstance(record, Progress):
            if record.after_offset != self.next_offset - 1:
                raise SnapshotError("full_dq_progress_position_mismatch")
            if self.watermark is not None and stamp(record.complete_through) <= stamp(
                self.watermark
            ):
                raise SnapshotError("full_dq_frontier_not_advanced")
            self.watermark = record.complete_through
            self.progress.append(
                {
                    "raw_ref": record.record_id,
                    "known_at": record.received_at,
                    "complete_through": record.complete_through,
                    "after_offset": record.after_offset,
                    "scope": record.scope,
                }
            )
            action, reason = "progress", "declared_parent_stream_frontier"
        else:
            if record.offset != self.next_offset:
                raise SnapshotError("full_dq_noncontiguous_offset")
            action, reason = self._event(record)
            self.next_offset += 1
        receipt = {"raw_ref": record.record_id, "action": action, "reason": reason}
        self.receipts[record.record_id] = receipt
        self.last_received = record.received_at
        return deepcopy(receipt)

    def _reject(self, record: Delivery, reason: str) -> tuple[str, str]:
        self.quarantine.append(
            {
                "raw_ref": record.record_id,
                "action": "quarantined",
                "reason": reason,
                "topic": record.topic,
                "partition": record.partition,
                "offset": record.offset,
                "received_at": record.received_at,
                "body_sha256": hashlib.sha256(record.body_utf8.encode()).hexdigest(),
            }
        )
        return "quarantined", reason

    def _event(self, record: Delivery) -> tuple[str, str]:
        try:
            event = json.loads(
                record.body_utf8,
                object_pairs_hook=unique_keys,
                parse_constant=nonfinite,
                parse_float=finite_float,
            )
            event_hash = json_sha256(event)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            return self._reject(record, "invalid_json")
        if isinstance(event, dict) and event.get("schema_version") != "1.0":
            return self._reject(record, "unsupported_schema_version")
        identifier = event.get("event_id") if isinstance(event, dict) else None
        if isinstance(identifier, str) and identifier in self.events:
            return (
                ("duplicate_event", "same_event_id_and_content")
                if self.events[identifier] == event_hash
                else self._reject(record, "event_id_content_conflict")
            )
        try:
            if not isinstance(event, dict) or not isinstance(identifier, str):
                raise SnapshotError("full_dq_object_and_event_id_required")
            UUID(identifier)
            if next(validator().iter_errors(event), None) is not None:
                raise SnapshotError("full_dq_invalid_wire_event")
            key, fact = self.parent.match(event)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return self._reject(record, "invalid_operational_contract")
        if stamp(fact["ingested_at"]) > stamp(record.received_at):
            return self._reject(record, "fact_unavailable_at_delivery")
        self.events[identifier] = event_hash
        if key in self.business:
            return "duplicate_business", "same_business_fact"
        occurred = fact["occurred_at"]
        timing = (
            "late"
            if self.watermark is not None and stamp(occurred) <= stamp(self.watermark)
            else "out_of_order"
            if self.max_event_time is not None and stamp(occurred) < stamp(self.max_event_time)
            else "on_time"
        )
        if self.max_event_time is None or stamp(occurred) > stamp(self.max_event_time):
            self.max_event_time = occurred
        self.business.add(key)
        accepted = {
            **fact,
            "event_id": identifier,
            "raw_ref": record.record_id,
            "available_at": record.received_at,
            "timing_status": timing,
        }
        self.facts.append(accepted)
        grain = tuple(fact[k] for k in GRAIN)
        self.groups[grain].append(accepted)
        previous = self.latest.get(grain)
        revision = {
            **totals(self.groups[grain])[0],
            "known_at": record.received_at,
            "source_raw_ref": record.record_id,
            "source_offset": record.offset,
            "previous_revision_id": previous["revision_id"] if previous else None,
            "quality_status": "partial_parent_fact_coverage",
            "business_event_day_completeness": "not_qualified",
        }
        revision = {
            "revision_id": "operational-revision-sha256-" + json_sha256(revision),
            **revision,
        }
        self.revisions.append(revision)
        self.latest[grain] = revision
        return "accepted", timing

    def aggregates_as_of(self, cutoff: str) -> list[Row]:
        latest = {}
        for revision in self.revisions:
            if stamp(revision["known_at"]) <= stamp(cutoff):
                latest[tuple(revision[k] for k in GRAIN)] = revision
        return deepcopy([latest[k] for k in sorted(latest)])

    def coverage(self) -> list[Row]:
        groups: dict[tuple[Any, ...], Row] = {}
        for key, fact in self.parent.facts.items():
            grain = tuple(fact[k] for k in GRAIN)
            row = groups.setdefault(
                grain,
                {
                    **dict(zip(GRAIN, grain, strict=True)),
                    "expected_parent_facts": 0,
                    "accepted_parent_facts": 0,
                    "missing_business_ids": [],
                },
            )
            row["expected_parent_facts"] += 1
            if key in self.business:
                row["accepted_parent_facts"] += 1
            else:
                row["missing_business_ids"].append(key[1])
        return [
            {
                **groups[k],
                "missing_business_ids": sorted(groups[k]["missing_business_ids"]),
                "status": "incomplete_parent_fact_coverage"
                if groups[k]["missing_business_ids"]
                else "complete_parent_fact_coverage",
                "business_event_day_completeness": "not_qualified",
            }
            for k in sorted(groups)
        ]

    def snapshot(self) -> Row:
        counts = Counter(r["action"] for r in self.receipts.values())
        timing = Counter(r["timing_status"] for r in self.facts)
        kinds = Counter(r["event_type"] for r in self.facts)
        if self.next_offset != sum(
            counts[k] for k in ("accepted", "duplicate_event", "duplicate_business", "quarantined")
        ):
            raise SnapshotError("full_dq_accounting_mismatch")
        return deepcopy(
            {
                "report": {
                    "policy_version": VERSION,
                    "status": "completed",
                    "scope": SCOPE,
                    "input_records": len(self.receipts),
                    "raw_events": self.next_offset,
                    **{
                        k: counts[k]
                        for k in (
                            "accepted",
                            "duplicate_event",
                            "duplicate_business",
                            "quarantined",
                        )
                    },
                    "accepted_sales": kinds["sale_completed"],
                    "accepted_return_claims": kinds["return_completed"],
                    "missing_parent_facts": len(self.parent.facts) - len(self.business),
                    "late": timing["late"],
                    "out_of_order": timing["out_of_order"],
                    "aggregate_revisions": len(self.revisions),
                    "progress_declarations": len(self.progress),
                    "declared_source_watermark": self.watermark,
                    "max_accepted_event_time": self.max_event_time,
                    "watermark_meaning": "explicit_parent_stream_progress_not_business_event_day_completeness",
                    "curated_completeness": "not_qualified",
                    "missing_grain_policy": "unknown_not_zero",
                    "transport_durability_proven": False,
                },
                "receipts": list(self.receipts.values()),
                "accepted_facts": self.facts,
                "aggregate_revisions": self.revisions,
                "final_aggregates": totals(self.facts),
                "parent_fact_coverage": self.coverage(),
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
