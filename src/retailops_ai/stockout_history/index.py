"""Index an already clipped origin view; retain v1 opening/coverage/onset rules."""

from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from retailops_ai.stockout.features import Records, observed_day


class HistoryIndex:
    """One sorted ledger and one demand bucket map per causal origin, never global latest."""

    def __init__(self, known: Records) -> None:
        self.known = known
        self.ledger = sorted(
            known["inventory_ledger"], key=lambda r: (r["occurred_at"], r["sequence"])
        )
        self.times = [r["occurred_at"] for r in self.ledger]
        self.prefix = [0]
        for row in self.ledger:
            self.prefix.append(self.prefix[-1] + row["quantity_delta"])
        first = self.ledger[0]["occurred_at"] if self.ledger else None
        self.covered_through = max(
            (
                row["covered_through_at"]
                for row in known["inventory_history_coverage"]
                if first is not None and row["covered_from_at"] <= first
            ),
            default=None,
        )
        demand: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in known["daily_demand_versions"]:
            demand[row["business_date"]].append(row)
        self.demand = dict(demand)

    def inventory_day(self, day: date) -> tuple[bool, bool | None, int | None]:
        start = datetime.combine(day, time(), tzinfo=UTC)
        end = start + timedelta(days=1)
        if (
            not self.ledger
            or self.ledger[0]["movement_type"] != "opening_stock"
            or self.times[0] > start
            or self.covered_through is None
            or self.covered_through < end
        ):
            return False, None, None
        left, right = bisect_left(self.times, start), bisect_left(self.times, end)
        before = self.prefix[left]
        if self.times[0] == start:
            before = self.ledger[0]["quantity_delta"]
        constrained, onsets = before == 0, 0
        balance = before
        for row in self.ledger[left:right]:
            if row["movement_type"] == "opening_stock":
                continue
            previous = balance
            balance += row["quantity_delta"]
            if balance < 0:
                raise ValueError("invalid_known_stockout_ledger_balance")
            constrained |= balance == 0
            onsets += int(previous > 0 and balance == 0)
        return True, not constrained, onsets

    def observed_day(self, product: str, stock: str, day: date) -> int | None:
        # Keep every revision and original order in the selected business day.
        # Latest/effective route selection remains in the frozen v1 helper.
        daily = {**self.known, "daily_demand_versions": self.demand.get(day, [])}
        return observed_day(daily, product, stock, day)
