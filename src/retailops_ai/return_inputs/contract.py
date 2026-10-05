"""A bounded as-of view of known returns is not a complete business observation."""

from datetime import date, datetime, time
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, SellingKey, Sha256, Symbol, UtcTime

TABLES = (
    "return_events",
    "inventory_sales",
    "sale_price_references",
    "product_catalog",
    "return_policies",
)
MAX_INPUT_ROWS = 100_000
MAX_INPUT_BYTES = 128 * 1024**2
MAX_OUTPUT_ROWS = 100_000
MAX_OUTPUT_BYTES = 128 * 1024**2
Units = Annotated[int, Field(ge=0)]
Money = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")]


class Policy(Contract):
    version: Literal["return-event-day-view-1.0.0"] = "return-event-day-view-1.0.0"
    start_date: date
    end_date: date
    as_of_time: UtcTime
    metric: Literal["known_refunded_units_by_return_day"] = "known_refunded_units_by_return_day"
    scope: Literal["purchases_in_parent_source_only"] = "purchases_in_parent_source_only"
    missing_policy: Literal["unknown_without_event_day_coverage"] = (
        "unknown_without_event_day_coverage"
    )
    rejected_policy: Literal["count_claim_exclude_from_refunded_units_and_money"] = (
        "count_claim_exclude_from_refunded_units_and_money"
    )

    @model_validator(mode="after")
    def bounded_window(self) -> Self:
        if not 0 <= (self.end_date - self.start_date).days < 366:
            raise ValueError("return_view_requires_1_to_366_days")
        if self.end_date > self.as_of_time.date():
            raise ValueError("return_view_window_after_origin")
        return self


class Point(SellingKey):
    schema_version: Literal["1.0.0"] = "1.0.0"
    business_date: date
    as_of_time: UtcTime
    currency: Literal["PLN", "EUR"]
    known_event_count: Units
    known_refunded_units: Units
    known_rejected_units: Units
    known_refund_amount: Money
    observed_return_units: None = None
    coverage_status: Literal["not_qualified"] = "not_qualified"
    status: Literal["insufficient_data"] = "insufficient_data"
    reason_codes: tuple[Literal["return_event_day_coverage_unavailable"], ...] = (
        "return_event_day_coverage_unavailable",
    )
    source_stock_location_ids: tuple[Symbol, ...] = Field(max_length=1000)
    member_records_sha256: Sha256
    latest_member_available_at: UtcTime | None

    @model_validator(mode="after")
    def temporal_contract(self) -> Self:
        if datetime.combine(self.business_date, time.min, self.as_of_time.tzinfo) > self.as_of_time:
            raise ValueError("return_point_after_origin")
        if self.reason_codes != ("return_event_day_coverage_unavailable",):
            raise ValueError("return_point_coverage_reason_required")
        if (self.latest_member_available_at is None) != (self.known_event_count == 0):
            raise ValueError("return_point_membership_clock_mismatch")
        if self.latest_member_available_at is not None and (
            self.latest_member_available_at > self.as_of_time
        ):
            raise ValueError("return_point_member_after_origin")
        if not self.known_event_count and (
            self.known_refunded_units
            or self.known_rejected_units
            or self.known_refund_amount != "0.00"
            or self.source_stock_location_ids
        ):
            raise ValueError("empty_return_point_has_facts")
        return self
