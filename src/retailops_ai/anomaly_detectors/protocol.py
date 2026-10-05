"""Requested windows remain explicit, including missing declarations and cutoff exclusions."""

from datetime import UTC, date, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, UtcTime
from retailops_ai.day_qualification.contract import ID
from retailops_ai.qualified_anomalies.contract import Point, Policy
from retailops_ai.source_snapshot.files import SnapshotError

EventType = Literal["sale_completed", "return_completed"]
Currency = Literal["PLN", "EUR"]
Role = Literal["train", "validation", "test", "gap"]
MAX_REQUESTED = 10000


class Scope(Contract):
    event_type: EventType
    product_id: ID
    selling_location_id: ID
    channel: Literal["store", "online", "marketplace", "wholesale"]
    currency: Currency


def series_key(value: Scope | Point) -> tuple[str, ...]:
    return tuple(getattr(value, field) for field in Scope.model_fields)


def point_key(value: Point) -> tuple[str, ...]:
    return (*series_key(value), value.business_date.isoformat())


class Window(Contract):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start > self.end:
            raise ValueError("anomaly_window_order")
        return self


def scoring_origin(day: date, event: EventType, policy: Policy) -> datetime:
    delay = policy.sales_delay_hours if event == "sale_completed" else policy.returns_delay_hours
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC) + timedelta(
        hours=delay
    )


class Protocol(Contract):
    version: Literal["anomaly-detector-protocol-1.0.0"] = "anomaly-detector-protocol-1.0.0"
    purpose: Literal["development_mechanics_only"] = "development_mechanics_only"
    scopes: tuple[Scope, ...] = Field(min_length=1, max_length=1000)
    train: Window
    validation: Window
    test: Window
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    feature_policy: Policy = Policy()
    membership_clock: Literal["business_window_and_available_scoring_outcome"] = (
        "business_window_and_available_scoring_outcome"
    )
    gap_policy: Literal["requested_and_counted_never_fitted"] = "requested_and_counted_never_fitted"
    missing_policy: Literal["requested_unknown_not_clean_negative"] = (
        "requested_unknown_not_clean_negative"
    )
    training_labels: Literal["unavailable_no_oracle_filter"] = "unavailable_no_oracle_filter"
    final_portfolio_test: Literal["not_included_not_opened"] = "not_included_not_opened"

    @model_validator(mode="after")
    def causal(self) -> Self:
        keys = [series_key(scope) for scope in self.scopes]
        earliest_validation = min(
            scoring_origin(self.validation.start, s.event_type, self.feature_policy)
            for s in self.scopes
        )
        earliest_test = min(
            scoring_origin(self.test.start, s.event_type, self.feature_policy) for s in self.scopes
        )
        if (
            keys != sorted(set(keys))
            or not self.train.end < self.validation.start
            or not self.validation.end < self.test.start
            or not self.training_cutoff < self.selection_cutoff < earliest_test
            or not self.training_cutoff < earliest_validation
            or (self.test.end - self.train.start).days > 2000
            or len(keys) * ((self.test.end - self.train.start).days + 1) > MAX_REQUESTED
        ):
            raise ValueError("anomaly_protocol_order_scope_cutoff_or_budget")
        return self

    def role(self, day: date) -> Role:
        for role, window in (
            ("train", self.train),
            ("validation", self.validation),
            ("test", self.test),
        ):
            if window.start <= day <= window.end:
                return role  # type: ignore[return-value]
        return "gap"


class Membership(Scope):
    business_date: date
    scoring_origin: UtcTime
    role: Role
    input_status: Literal[
        "ready_input", "insufficient_history", "day_unqualified", "no_declaration"
    ]
    eligible: bool
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def check_eligibility(self) -> Self:
        if self.eligible != (not self.reason_codes) or (
            self.eligible and (self.input_status != "ready_input" or self.role == "gap")
        ):
            raise ValueError("anomaly_membership_eligibility")
        return self


def requested(protocol: Protocol, points: list[Point]) -> list[tuple[Membership, Point | None]]:
    protocol = type(protocol).model_validate_json(protocol.model_dump_json())
    indexed = {point_key(point): point for point in points}
    if len(indexed) != len(points):
        raise SnapshotError("anomaly_duplicate_input_key")
    output = []
    for scope in protocol.scopes:
        for offset in range((protocol.test.end - protocol.train.start).days + 1):
            day = protocol.train.start + timedelta(days=offset)
            point = indexed.get((*series_key(scope), day.isoformat()))
            origin = scoring_origin(day, scope.event_type, protocol.feature_policy)
            if point is not None and point.scoring_origin != origin:
                raise SnapshotError("anomaly_protocol_feature_clock_mismatch")
            role = protocol.role(day)
            status = point.status if point else "no_declaration"
            reasons: list[str] = [] if status == "ready_input" else [status]
            if role == "gap":
                reasons.append("split_gap")
            elif role == "train" and origin > protocol.training_cutoff:
                reasons.append("outcome_after_training_cutoff")
            elif role == "validation" and origin > protocol.selection_cutoff:
                reasons.append("outcome_after_selection_cutoff")
            output.append(
                (
                    Membership(
                        **scope.model_dump(),
                        business_date=day,
                        scoring_origin=origin,
                        role=role,
                        input_status=status,
                        eligible=not reasons,
                        reason_codes=tuple(reasons),
                    ),
                    point,
                )
            )
    return output
