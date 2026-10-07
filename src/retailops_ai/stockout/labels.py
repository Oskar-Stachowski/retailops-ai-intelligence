"""Replay physical movements independently; future facts never become origin state."""

from collections.abc import Sequence
from datetime import datetime, timedelta

from retailops_ai.stockout.contract import (
    DEFAULT_POLICY,
    Eligibility,
    LabelPoint,
    LabelPolicy,
    LedgerMovement,
    StockState,
)


def label_window(
    state: StockState,
    movements: Sequence[LedgerMovement],
    *,
    as_of: datetime,
    evaluated_at: datetime,
    eligibility: Eligibility,
    policy: LabelPolicy = DEFAULT_POLICY,
) -> LabelPoint:
    """Outcome replay includes a zero reached and replenished at the same timestamp."""
    end = as_of + timedelta(days=policy.horizon_days)
    base = dict(
        product_id=state.product_id,
        stock_location_id=state.stock_location_id,
        as_of=as_of,
        window_end_at=end,
        evaluated_at=evaluated_at,
        incident_stockout=None,
        label_available_at=None,
        first_incident_at=None,
        first_incident_event_id=None,
    )

    def unavailable(reason: str, available: datetime | None = None) -> LabelPoint:
        return LabelPoint.model_validate(
            {
                **base,
                "status": "not_evaluable",
                "reason": reason,
                "label_available_at": available,
            }
        )

    # Constructing the point also validates UTC clocks and the evaluation boundary.
    unavailable("validation")
    if len(movements) > policy.max_ledger_rows:
        raise ValueError("stockout_ledger_row_limit")
    if eligibility.reason in {
        "inactive_lifecycle",
        "inactive_assortment",
        "dimension_not_available",
        "route_missing",
        "calendar_missing",
    }:
        return unavailable(eligibility.reason)
    key = state.product_id, state.stock_location_id
    if any((m.product_id, m.stock_location_id) != key for m in movements):
        raise ValueError("stockout_physical_grain_mismatch")
    ordered = sorted(movements, key=lambda m: (m.occurred_at, m.sequence))
    if len({m.event_id for m in ordered}) != len(ordered) or len(
        {(m.occurred_at, m.sequence) for m in ordered}
    ) != len(ordered):
        raise ValueError("stockout_duplicate_movement_or_ordering_key")
    relevant = [m for m in ordered if m.occurred_at <= end]
    if state.status != "known" or state.available_at is None or state.available_at > as_of:
        return unavailable("inventory_unknown")
    if state.snapshot_at > as_of:
        return unavailable("inventory_unknown")
    if as_of - state.snapshot_at > timedelta(seconds=policy.max_snapshot_age_seconds):
        return unavailable("stale_inventory_snapshot")
    if any(m.occurred_at <= as_of < m.available_at for m in relevant):
        return unavailable("origin_state_unavailable")

    balance: int | None = None
    origin_balance: int | None = None
    onset: LedgerMovement | None = None
    for movement in relevant:
        if movement.movement_type == "opening_stock":
            if balance is not None:
                raise ValueError("stockout_duplicate_opening")
            balance = movement.quantity_delta
        else:
            if balance is None:
                raise ValueError("stockout_missing_opening")
            previous = balance
            balance += movement.quantity_delta
            if (
                previous > 0
                and balance == 0
                and as_of < movement.occurred_at <= end
                and onset is None
            ):
                onset = movement
        if balance < 0:
            raise ValueError("stockout_negative_balance")
        if movement.occurred_at <= as_of:
            origin_balance = balance
    if origin_balance is None:
        return unavailable("inventory_unknown")
    # Reconcile the supplied snapshot at its own cutoff, then account for newer origin facts.
    snapshot_balance = sum(
        m.quantity_delta
        for m in relevant
        if m.occurred_at <= state.snapshot_at and m.available_at <= state.snapshot_at
    )
    if snapshot_balance != state.available_qty:
        raise ValueError("stockout_snapshot_ledger_mismatch")
    if origin_balance == 0:
        return LabelPoint.model_validate({**base, "status": "already_stockout", "reason": None})
    if eligibility.reason is not None:
        return unavailable(eligibility.reason)
    if (
        eligibility.covered_from_at is None
        or eligibility.covered_through_at is None
        or eligibility.available_at is None
        or eligibility.covered_from_at > relevant[0].occurred_at
        or eligibility.covered_through_at <= end
    ):
        return unavailable("inventory_coverage_incomplete")
    available = max(
        end + timedelta(seconds=eligibility.truth_delay_seconds),
        eligibility.available_at,
        *(m.available_at for m in relevant),
    )
    if evaluated_at < available:
        return unavailable("outcomes_not_available", available)
    return LabelPoint.model_validate(
        {
            **base,
            "status": "evaluable",
            "reason": None,
            "incident_stockout": int(onset is not None),
            "label_available_at": available,
            "first_incident_at": onset.occurred_at if onset else None,
            "first_incident_event_id": onset.event_id if onset else None,
        }
    )
