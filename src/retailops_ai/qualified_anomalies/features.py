"""Fit accepted historical event days, then query the outcome on a separate clock."""

from collections import Counter
from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

from retailops_ai.anomalies.features import SCOPE_RANK, latest
from retailops_ai.day_qualification.contract import GRAIN, KEY, Day
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.qualified_anomalies.contract import (
    Context,
    ModelRow,
    Point,
    Policy,
    Reference,
    statistics,
)
from retailops_ai.source_snapshot.files import SnapshotError

Row = dict[str, Any]
TABLES = ("daily_demand_versions", "price_plans", "promotion_plans", "inventory_daily_snapshots")


def reference(table: str, row: Row, role: str) -> Reference:
    return Reference.model_validate(
        {
            "table": table,
            "record_sha256": row["source_record_sha256"],
            "available_at": row["curated_available_at"],
            "business_date": row.get("business_date"),
            "role": role,
        }
    )


class Features:
    def __init__(
        self, gate: DayGate, tables: dict[str, list[Row]], policy: Policy | None = None
    ) -> None:
        if set(tables) != set(TABLES):
            raise SnapshotError("qualified_anomaly_context_allowlist_mismatch")
        self.gate = gate
        self.tables = deepcopy(tables)
        self.policy = policy or Policy()

    def plan(self, table: str, day: Day, fit: datetime) -> Row | None:
        key = "plan_key" if table == "price_plans" else "promotion_key"
        candidates = [
            r
            for r in latest(
                [r for r in self.tables[table] if r["product_id"] == day.product_id],
                fit,
                (key, "effective_from", "effective_to"),
            )
            if r["effective_from"] <= date.fromisoformat(day.business_date) < r["effective_to"]
            and r["selling_location_id"] in (None, day.selling_location_id)
            and r["channel"] in ("all", day.channel)
            and (
                r["currency"] == day.currency if table == "price_plans" else r["status"] == "active"
            )
        ]
        if candidates:
            ranks = [
                SCOPE_RANK[r["scope"]] if table == "price_plans" else r["priority"]
                for r in candidates
            ]
            candidates = [
                r for r, rank in zip(candidates, ranks, strict=True) if rank == max(ranks)
            ]
        if len(candidates) > 1:
            raise SnapshotError("qualified_anomaly_ambiguous_plan")
        return candidates[0] if candidates else None

    def context(self, day: Day, fit: datetime, scoring: datetime) -> Context:
        price = self.plan("price_plans", day, fit)
        promo = self.plan("promotion_plans", day, fit)
        refs = [
            reference(table, row, "known_plan")
            for table, row in (("price_plans", price), ("promotion_plans", promo))
            if row is not None
        ]
        # The curated daily version supplies only the causal stock mapping.
        # Neither its quantity nor its final quality supplies an observed/fit value.
        mappings = latest(
            [
                r
                for r in self.tables["daily_demand_versions"]
                if r["business_date"] == date.fromisoformat(day.business_date)
                and all(r[k] == getattr(day, k) for k in KEY if k != "currency")
            ],
            scoring,
            ("business_date",),
        )
        mapping = mappings[0] if mappings else None
        stock = mapping["mapped_stock_location_id"] if mapping is not None else None
        if mapping is not None:
            refs.append(reference("daily_demand_versions", mapping, "stock_mapping"))
        candidates = [
            r
            for r in self.tables["inventory_daily_snapshots"]
            if stock is not None
            and r["stock_location_id"] == stock
            and r["product_id"] == day.product_id
            and r["business_date"] == date.fromisoformat(day.business_date)
            and r["curated_available_at"] is not None
            and r["curated_available_at"] <= scoring
            and r["snapshot_at"] <= scoring
            and r["is_full_business_day"]
            and r["status"] == "known"
        ]
        snapshot = max(candidates, key=lambda r: r["snapshot_at"]) if candidates else None
        if snapshot is not None:
            refs.append(reference("inventory_daily_snapshots", snapshot, "inventory_context"))
        units = snapshot["on_hand"] if snapshot is not None else None
        return Context(
            planned_price=format(price["price"], ".2f") if price is not None else None,
            promotion_offered=promo is not None,
            stock_location_id=stock,
            on_hand=units,
            stock_status="unavailable"
            if units is None
            else "potential_stockout"
            if units == 0
            else "no_stockout_signal",
            references=tuple(refs),
        )

    def point(self, day: Day) -> Point:
        day_date = date.fromisoformat(day.business_date)
        start = datetime.combine(day_date, datetime.min.time(), UTC)
        fit = start - timedelta(microseconds=1)
        delay = (
            self.policy.sales_delay_hours
            if day.event_type == "sale_completed"
            else self.policy.returns_delay_hours
        )
        scoring = start + timedelta(days=1, hours=delay)
        series = tuple(getattr(day, k) for k in KEY)
        history = tuple(
            self.gate.point(
                (day.event_type, (day_date - timedelta(days=i)).isoformat(), *series),
                fit.isoformat(),
            )
            for i in range(28, 0, -1)
        )
        observation = self.gate.point(tuple(getattr(day, k) for k in GRAIN), scoring.isoformat())
        counts = dict(Counter(p.status for p in history))
        expected, residuals, scale, floor = statistics(history, day_date)
        status = (
            "day_unqualified"
            if observation.status != "qualified"
            else "insufficient_history"
            if scale is None
            else "ready_input"
        )
        reasons = (
            ([] if observation.status == "qualified" else [observation.status])
            + (["insufficient_history"] if scale is None else [])
            + (["robust_scale_floor_1_pcs"] if floor else [])
        )
        observed = observation.observed_units
        residual = (
            observed - expected
            if status == "ready_input" and observed is not None and expected is not None
            else None
        )
        return Point.model_validate(
            {
                **{k: getattr(day, k) for k in GRAIN},
                "business_date": day_date,
                "fit_cutoff": fit,
                "scoring_origin": scoring,
                "observation": observation,
                "history": history,
                "history_status_counts": counts,
                "usable_history_days": counts.get("qualified", 0),
                "residual_count": len(residuals),
                "expected_units": expected,
                "robust_scale_units": scale,
                "scale_floor_applied": floor,
                "residual_units": residual,
                "standardized_residual": residual / scale
                if residual is not None and scale is not None
                else None,
                "status": status,
                "reason_codes": tuple(reasons),
                "context": self.context(day, fit, scoring),
            }
        )

    def rows(self) -> Iterator[Point]:
        for grain in sorted(self.gate.days):
            yield self.point(self.gate.days[grain])


def model_row(point: Point) -> ModelRow | None:
    """Expose only approved numeric fields after day and history qualification."""
    if point.status != "ready_input":
        return None
    return ModelRow.model_validate(
        {
            "observed_units": point.observation.observed_units,
            "expected_units": point.expected_units,
            "residual_units": point.residual_units,
            "robust_scale_units": point.robust_scale_units,
            "standardized_residual": point.standardized_residual,
            "planned_price": float(point.context.planned_price)
            if point.context.planned_price is not None
            else None,
            "promotion_offered": point.context.promotion_offered,
            "on_hand": point.context.on_hand,
        }
    )
