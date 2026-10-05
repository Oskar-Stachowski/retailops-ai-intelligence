"""Reconstruct publisher assertions independently from typed public curated facts."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from retailops_ai.day_qualification.contract import GRAIN, KEY, MAX_ROWS, TABLES, Day
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.source_snapshot.files import SnapshotError

Row = dict[str, Any]


def declarations(tables: dict[str, list[Row]], start_date: str) -> list[Day]:
    if set(tables) != set(TABLES):
        raise SnapshotError("day_coverage_table_allowlist_mismatch")
    sales = tables["inventory_sales"]
    products = {p["id"]: p for p in tables["product_catalog"]}
    rules = {(r["category_id"], r["channel"]): r for r in tables["return_policies"]}
    grouped: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
    returns: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
    for sale in sales:
        grouped[tuple(sale[k] for k in KEY)].append(sale)
    for claim in tables["return_events"]:
        returns[(claim["returned_at"].date().isoformat(), *(claim[k] for k in KEY))].append(claim)
    result = []
    for obs in tables["daily_demand_observations"]:
        day = obs["business_date"]
        series = tuple(obs[k] for k in KEY)
        members = [s for s in grouped[series] if s["sold_at"].date() == day]
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
        complete = obs["source_data_complete"] and obs["quality_status"] == "valid"
        closed = obs["observation_status"] == "closed"
        if (closed and members) or (
            complete and sum(s["quantity"] for s in members) != obs["observed_units"]
        ):
            raise SnapshotError("day_coverage_sales_observation_mismatch")
        result.append(
            Day(
                event_type="sale_completed",
                business_date=day.isoformat(),
                **dict(zip(KEY, series, strict=True)),
                window_end=end.isoformat(),
                known_at=max(
                    end, obs["available_at"], *(s["available_at"] for s in members)
                ).isoformat(),
                source_complete=complete,
                activity="closed" if closed else "open" if complete else "missing",
                expected_business_ids=sorted(s["sale_id"] for s in members),
                required_sale_ids=[],
            )
        )
    for series, purchases in sorted(grouped.items()):
        rule = rules[products[series[0]]["category_id"], series[2]]
        finish = max(s["sold_at"].date() for s in purchases) + timedelta(days=rule["window_days"])
        day = date.fromisoformat(start_date)
        while day <= finish:
            end = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
            known = max(
                end + timedelta(days=rule["max_ingestion_delay_days"]),
                min(s["available_at"] for s in purchases),
                rule["known_at"],
            )
            members = returns[(day.isoformat(), *series)]
            if any(c["available_at"] > known for c in members):
                raise SnapshotError("day_coverage_return_delay_bound_mismatch")
            result.append(
                Day(
                    event_type="return_completed",
                    business_date=day.isoformat(),
                    **dict(zip(KEY, series, strict=True)),
                    window_end=end.isoformat(),
                    known_at=known.isoformat(),
                    source_complete=True,
                    activity="open",
                    expected_business_ids=sorted(c["id"] for c in members),
                    required_sale_ids=sorted(s["sale_id"] for s in purchases if s["sold_at"] < end),
                )
            )
            day += timedelta(days=1)
    if not 0 < len(result) <= MAX_ROWS:
        raise SnapshotError("day_coverage_row_limit")
    result.sort(key=lambda d: tuple(getattr(d, k) for k in GRAIN))
    if len({tuple(getattr(d, k) for k in GRAIN) for d in result}) != len(result):
        raise SnapshotError("day_coverage_duplicate_grain")
    if any(stamp(d.known_at) < stamp(d.window_end) for d in result):
        raise SnapshotError("day_coverage_closure_before_day_end")
    return result
