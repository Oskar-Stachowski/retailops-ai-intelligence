"""Seasonal demand residuals fitted strictly before each evaluated business day."""

from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

from retailops_ai.anomalies.contract import DEFAULT_POLICY, TABLES, Point, Policy, Reference
from retailops_ai.data_contracts.common import Channel
from retailops_ai.source_snapshot.files import SnapshotError

Row = dict[str, Any]
KEY = ("product_id", "selling_location_id", "channel")
Series = tuple[str, str, Channel]
SCOPE_RANK = {"global": 0, "channel": 1, "location": 2, "location_channel": 3}


def latest(rows: list[Row], cutoff: datetime, keys: tuple[str, ...]) -> list[Row]:
    groups: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
    for row in rows:
        at = row["curated_available_at"]
        if at is not None and at <= cutoff:
            groups[tuple(row[k] for k in keys)].append(row)
    selected = []
    for versions in groups.values():
        version = max(r.get("version", 1) for r in versions)
        top = [r for r in versions if r.get("version", 1) == version]
        if len(top) != 1:
            raise SnapshotError("ambiguous_anomaly_known_version")
        selected.append(top[0])
    return selected


def valid(row: Row) -> bool:
    return (
        row["observation_status"] in {"observed_positive", "observed_zero"}
        and row["observed_units"] is not None
        and row["observed_units"] >= 0
        # The reviewed version-history schema records status/quantity, not a raw
        # stream completeness flag. Canonical qualification is checked upstream;
        # explicit raw completeness remains not_qualified in every output point.
        and row.get("source_data_complete") is not False
        and row.get("quality_status", "valid") == "valid"
    )


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
    def __init__(self, tables: dict[str, list[Row]], policy: Policy = DEFAULT_POLICY) -> None:
        if set(tables) != set(TABLES):
            raise SnapshotError("anomaly_input_table_allowlist_mismatch")
        self.policy = policy
        self.series: dict[Series, list[Row]] = defaultdict(list)
        self.plans: dict[str, dict[str, list[Row]]] = {}
        self.stocks: dict[tuple[str, str, date], list[Row]] = defaultdict(list)
        for row in tables["daily_demand_versions"]:
            self.series[tuple(row[k] for k in KEY)].append(row)
        for table in ("price_plans", "promotion_plans"):
            grouped: dict[str, list[Row]] = defaultdict(list)
            for row in tables[table]:
                grouped[row["product_id"]].append(row)
            self.plans[table] = grouped
        for row in tables["inventory_daily_snapshots"]:
            self.stocks[(row["product_id"], row["stock_location_id"], row["business_date"])].append(
                row
            )

    def plan(self, table: str, series: Series, day: date, cutoff: datetime) -> Row | None:
        key = "plan_key" if table == "price_plans" else "promotion_key"
        candidates = [
            r
            for r in latest(
                self.plans[table].get(series[0], []),
                cutoff,
                (key, "effective_from", "effective_to"),
            )
            if r["effective_from"] <= day < r["effective_to"]
            and r["selling_location_id"] in (None, series[1])
            and r["channel"] in ("all", series[2])
            and (table != "promotion_plans" or r["status"] == "active")
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
            raise SnapshotError("ambiguous_anomaly_plan")
        return candidates[0] if candidates else None

    def point(self, series: Series, day: date) -> Point:
        start = datetime.combine(day, datetime.min.time(), UTC)
        fit = start - timedelta(microseconds=1)
        scoring = (
            start
            + timedelta(days=1, hours=self.policy.scoring_delay_hours)
            - timedelta(microseconds=1)
        )
        source = self.series[series]
        history = {
            r["business_date"]: r
            for r in latest(source, fit, ("business_date",))
            if day - timedelta(days=self.policy.history_days) <= r["business_date"] < day
            and valid(r)
        }
        lag = timedelta(days=self.policy.seasonal_lag_days)
        residuals = [
            r["observed_units"] - history[d - lag]["observed_units"]
            for d, r in sorted(history.items())
            if d - lag in history
        ]
        expected = history.get(day - lag)
        insufficient = (
            len(history) < self.policy.minimum_history_days
            or len(residuals) < self.policy.minimum_residuals
            or expected is None
        )
        scale = None
        floor = False
        if not insufficient:
            center = median(residuals)
            mad = float(median([abs(r - center) for r in residuals]))
            floor = 1.4826 * mad < 1.0
            scale = max(1.0, 1.4826 * mad)
        observed = next(
            (r for r in latest(source, scoring, ("business_date",)) if r["business_date"] == day),
            None,
        )
        reasons = []
        status: Any = "ready_input"
        observation_status: Any = (
            "unavailable" if observed is None else observed["observation_status"]
        )
        if observed is None:
            status = "insufficient_data"
            reasons.append("observation_unavailable_at_scoring_origin")
        elif observation_status == "closed":
            status = "closed"
            reasons.append("closed_business_day")
        elif not valid(observed):
            status = "invalid_input"
            observation_status = "invalid"
            reasons.append("source_quality_invalid")
        if insufficient:
            reasons.append("insufficient_history")
            if status == "ready_input":
                status = "insufficient_data"
        if floor:
            reasons.append("robust_scale_floor_1_pcs")
        observed_units = (
            observed["observed_units"]
            if observed is not None and observation_status != "invalid"
            else None
        )
        expected_units = expected["observed_units"] if expected is not None else None
        residual = (
            observed_units - expected_units
            if status == "ready_input" and observed_units is not None and expected_units is not None
            else None
        )
        refs = [reference("daily_demand_versions", r, "fit") for _, r in sorted(history.items())]
        if observed is not None:
            refs.append(reference("daily_demand_versions", observed, "observed"))
        price = self.plan("price_plans", series, day, fit)
        promo = self.plan("promotion_plans", series, day, fit)
        for table, row in (("price_plans", price), ("promotion_plans", promo)):
            if row is not None:
                refs.append(reference(table, row, "known_plan"))
        stock = observed["mapped_stock_location_id"] if observed is not None else None
        snapshots = (
            [
                r
                for r in self.stocks.get((series[0], stock, day), [])
                if r["curated_available_at"] is not None
                and r["curated_available_at"] <= scoring
                and r["is_full_business_day"]
                and r["status"] == "known"
            ]
            if stock is not None
            else []
        )
        snapshot = max(snapshots, key=lambda r: r["snapshot_at"]) if snapshots else None
        if snapshot is not None:
            refs.append(reference("inventory_daily_snapshots", snapshot, "inventory_context"))
        return Point(
            product_id=series[0],
            selling_location_id=series[1],
            channel=series[2],
            business_date=day,
            fit_cutoff=fit,
            scoring_origin=scoring,
            observation_status=observation_status,
            observed_units=observed_units,
            expected_units=expected_units,
            residual_units=residual,
            robust_scale_units=scale,
            standardized_residual=residual / scale
            if residual is not None and scale is not None
            else None,
            history_days=len(history),
            residual_count=len(residuals),
            insufficient_history=insufficient,
            scale_floor_applied=floor,
            status=status,
            reason_codes=tuple(reasons),
            planned_price=format(price["price"], "f") if price is not None else None,
            currency=price["currency"] if price is not None else None,
            promotion_offered=promo is not None,
            stock_location_id=stock,
            on_hand=snapshot["on_hand"] if snapshot is not None else None,
            inventory_status="available" if snapshot is not None else "unavailable",
            dq_status="unavailable"
            if observed is None
            else "invalid_input"
            if status == "invalid_input"
            else "canonical_source_valid",
            references=tuple(refs),
        )

    def rows(self) -> Iterator[Point]:
        for series, source in sorted(self.series.items()):
            for day in sorted({r["business_date"] for r in source}):
                yield self.point(series, day)
