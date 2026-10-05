"""Strict labels separate current state, observation coverage and mature outcomes."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol, UtcTime, utc_time

Quantity = Annotated[StrictInt, Field(ge=0)]


class LabelPolicy(Contract):
    version: Literal["incident-stockout-labels-1.0.0"] = "incident-stockout-labels-1.0.0"
    horizon_days: Literal[7] = 7
    stock_measure: Literal["available_qty"] = "available_qty"
    reservation_policy: Literal["none"] = "none"
    episode_policy: Literal["zero_after_event_including_instantaneous"] = (
        "zero_after_event_including_instantaneous"
    )
    max_snapshot_age_seconds: Annotated[StrictInt, Field(ge=0, le=86400)] = 86400
    max_ledger_rows: Annotated[StrictInt, Field(ge=1, le=100000)] = 100000
    max_windows: Annotated[StrictInt, Field(ge=1, le=10000)] = 10000


DEFAULT_POLICY = LabelPolicy()


class PhysicalKey(Contract):
    product_id: Symbol
    stock_location_id: Symbol


class StockState(PhysicalKey):
    snapshot_at: UtcTime
    available_at: UtcTime | None
    status: Literal["known", "not_available"]
    on_hand: Quantity | None
    reserved_qty: Quantity | None
    available_qty: Quantity | None

    @model_validator(mode="after")
    def consistent_quantity(self) -> Self:
        if self.status == "known":
            if (
                self.on_hand is None
                or self.reserved_qty != 0
                or self.available_qty != self.on_hand
                or self.available_at is None
                or self.available_at < self.snapshot_at
            ):
                raise ValueError("stockout_state_requires_known_unreserved_stock")
        elif any(
            value is not None
            for value in (self.on_hand, self.reserved_qty, self.available_qty, self.available_at)
        ):
            raise ValueError("unknown_inventory_is_not_zero")
        return self


class LedgerMovement(PhysicalKey):
    event_id: Symbol
    occurred_at: UtcTime
    available_at: UtcTime
    sequence: Annotated[StrictInt, Field(ge=0)]
    quantity_delta: StrictInt
    movement_type: Literal[
        "opening_stock",
        "replenishment_received",
        "sale",
        "return_to_stock",
        "write_off",
        "transfer_in",
        "transfer_out",
        "inventory_adjustment",
    ]

    @model_validator(mode="after")
    def causal(self) -> Self:
        if self.available_at < self.occurred_at:
            raise ValueError("ledger_available_before_occurrence")
        if self.movement_type == "opening_stock" and self.quantity_delta < 0:
            raise ValueError("negative_opening_stock")
        if (
            self.movement_type in {"replenishment_received", "return_to_stock", "transfer_in"}
            and self.quantity_delta <= 0
        ):
            raise ValueError("positive_inventory_movement_required")
        if self.movement_type in {"sale", "write_off", "transfer_out"} and self.quantity_delta >= 0:
            raise ValueError("negative_inventory_movement_required")
        if self.movement_type == "inventory_adjustment" and self.quantity_delta == 0:
            raise ValueError("nonzero_inventory_adjustment_required")
        return self


class Eligibility(Contract):
    """A source-qualified window; supplied only by the verified private handoff."""

    reason: str | None = None
    covered_from_at: UtcTime | None = None
    covered_through_at: UtcTime | None = None
    available_at: UtcTime | None = None
    truth_delay_seconds: Annotated[StrictInt, Field(ge=0)] = 0


class LabelPoint(PhysicalKey):
    as_of: UtcTime
    window_end_at: UtcTime
    evaluated_at: UtcTime
    status: Literal["evaluable", "already_stockout", "not_evaluable"]
    reason: str | None
    incident_stockout: Annotated[StrictInt, Field(ge=0, le=1)] | None
    label_available_at: UtcTime | None
    first_incident_at: UtcTime | None
    first_incident_event_id: Symbol | None

    @model_validator(mode="after")
    def coherent_label(self) -> Self:
        if self.window_end_at != self.as_of + timedelta(days=7) or self.evaluated_at < self.as_of:
            raise ValueError("stockout_label_window_or_evaluation_mismatch")
        if self.status == "evaluable":
            if (
                self.incident_stockout is None
                or self.reason is not None
                or self.label_available_at is None
                or self.label_available_at < self.window_end_at
                or self.label_available_at > self.evaluated_at
            ):
                raise ValueError("evaluable_label_requires_mature_outcome")
            if (self.incident_stockout == 1) != (self.first_incident_at is not None):
                raise ValueError("incident_requires_observed_onset")
        elif self.incident_stockout is not None or self.first_incident_at is not None:
            raise ValueError("non_evaluable_label_is_not_negative")
        if (self.first_incident_at is None) != (self.first_incident_event_id is None):
            raise ValueError("incident_reference_mismatch")
        if self.first_incident_at is not None and not (
            self.as_of < self.first_incident_at <= self.window_end_at
        ):
            raise ValueError("incident_outside_prediction_window")
        if self.status == "not_evaluable" and self.reason is None:
            raise ValueError("non_evaluable_reason_required")
        if self.status == "already_stockout" and (
            self.reason is not None or self.label_available_at is not None
        ):
            raise ValueError("current_stockout_is_separate_from_incident_labels")
        return self

    def eligible_at(self, training_cutoff: UtcTime) -> bool:
        """A completed label is usable only when its outcome was available at fit time."""
        training_cutoff = utc_time(training_cutoff)
        return (
            self.status == "evaluable"
            and self.label_available_at is not None
            and self.label_available_at <= training_cutoff
        )
