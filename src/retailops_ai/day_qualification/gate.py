"""Query immutable delivery knowledge; final aggregates never supply historical values."""

from copy import deepcopy
from decimal import Decimal
from typing import Any

from retailops_ai.day_qualification.contract import GRAIN, Day, Point
from retailops_ai.full_raw_dq.source import ParentFacts
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError, decode_json

Row = dict[str, Any]


class DayGate:
    def __init__(self, days: list[Day], replay: Row, raw: bytes, parent: ParentFacts) -> None:
        self.days = {tuple(getattr(d, k) for k in GRAIN): deepcopy(d) for d in days}
        self.accepted = {
            (f["event_type"], f["business_id"]): deepcopy(f) for f in replay["accepted_facts"]
        }
        receipts = {r["record_id"]: r for r in (decode_json(line) for line in raw.splitlines())}
        self.unattributed: list[str] = []
        for quarantined in replay["quarantine"]:
            record = receipts[quarantined["raw_ref"]]
            try:
                body = decode_json(record["body_utf8"].encode())
                kind = body.get("event_type")
                payload = body.get("payload")
                if not isinstance(payload, dict) or kind not in {
                    "sale_completed",
                    "return_completed",
                }:
                    raise SnapshotError("unattributed_quarantine")
                key = (kind, payload.get("sale_id" if kind == "sale_completed" else "return_id"))
                provenance = parent.ids.get(body.get("event_id"))
                # UUID is provenance, not a substitute for an operational key.
                # Conflicting identifiers cannot silently localize an unknown row.
                if key not in parent.facts or (provenance is not None and provenance != key):
                    raise SnapshotError("unattributed_quarantine")
            except (ValueError, TypeError, AttributeError):
                self.unattributed.append(quarantined["received_at"])

    def point(self, grain: tuple[Any, ...], as_of: str) -> Point:
        cutoff = stamp(as_of)
        base = {**dict(zip(GRAIN, grain, strict=True)), "as_of": as_of}
        day = self.days.get(grain)
        if day is None:
            return Point(**base, status="no_declaration")
        if stamp(day.known_at) > cutoff:
            return Point(**base, status="closure_unavailable")
        if not day.source_complete:
            return Point(**base, status="source_incomplete")
        if day.activity == "closed":
            return Point(**base, status="location_closed")
        if any(stamp(t) <= cutoff for t in self.unattributed):
            return Point(**base, status="dq_unattributed_quarantine")
        required = {(day.event_type, identifier) for identifier in day.expected_business_ids} | {
            ("sale_completed", identifier) for identifier in day.required_sale_ids
        }
        missing = sorted(
            f"{kind}:{identifier}"
            for kind, identifier in required
            if (kind, identifier) not in self.accepted
            or stamp(self.accepted[kind, identifier]["available_at"]) > cutoff
        )
        if missing:
            return Point(**base, status="dq_missing_facts", missing_fact_keys=missing)
        facts = [
            self.accepted[day.event_type, identifier] for identifier in day.expected_business_ids
        ]
        return Point(
            **base,
            status="qualified",
            raw_dq_completeness="qualified",
            score_eligible=True,
            observed_units=sum(f["quantity"] for f in facts if f["status"] != "rejected"),
            amount=f"{sum((Decimal(f['amount']) for f in facts), Decimal(0)):.2f}",
            rejected_units=sum(f["quantity"] for f in facts if f["status"] == "rejected"),
        )
