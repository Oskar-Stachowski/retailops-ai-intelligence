"""Fixed-origin calendar features from an explicit facts/plans allowlist."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from statistics import mean, pstdev
from typing import Any, Literal

from retailops_ai.data_contracts.common import Channel
from retailops_ai.forecasting.contract import Origin
from retailops_ai.forecasting.features_contract import (
    FEATURE_TYPES,
    TABLES,
    HistoryContext,
    InputRow,
    InputValue,
    Kind,
    ObservationStatus,
    PanelPoint,
    Reference,
)
from retailops_ai.source_snapshot.files import SnapshotError

Row = dict[str, Any]
Series = tuple[str, str, Channel]
SCOPE_RANK = {"global": 0, "channel": 1, "location": 2, "location_channel": 3}


def reference(table: str, row: Row) -> Reference:
    return Reference(
        table=table,
        record_id=row["id"],
        record_sha256=row["source_record_sha256"],
        available_at=row["curated_available_at"],
    )


def latest(rows: list[Row], key: tuple[str, ...]) -> list[Row]:
    """Latest known revision per logical key and period, rejecting ambiguous highest versions."""
    groups: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in key)].append(row)
    selected = []
    for revisions in groups.values():
        version = max(r.get("version", 1) for r in revisions)
        top = [r for r in revisions if r.get("version", 1) == version]
        if len(top) != 1:
            raise SnapshotError("ambiguous_forecast_known_version")
        selected.append(top[0])
    return selected


def effective(rows: list[Row], day: date) -> list[Row]:
    return [r for r in rows if r["effective_from"] <= day < r["effective_to"]]


def one_interval(rows: list[Row], day: date) -> Row | None:
    candidates = effective(rows, day)
    if len(candidates) > 1:
        raise SnapshotError("ambiguous_forecast_effective_interval")
    return candidates[0] if candidates else None


def value(
    name: str,
    kind: Kind,
    payload: Any,
    refs: tuple[Reference, ...] = (),
    *,
    at: datetime | None = None,
    reason: str = "not_available_at_origin",
    status: Literal["missing", "not_applicable"] = "missing",
    observed_through_date: date | None = None,
) -> InputValue:
    if payload is None:
        return InputValue(
            name=name,
            kind=kind,
            value=None,
            status=status,
            source_available_at=None,
            reason=reason,
            references=refs,
            observed_through_date=observed_through_date,
        )
    return InputValue(
        name=name,
        kind=kind,
        value=payload,
        status="available",
        source_available_at=at if at is not None else max(r.available_at for r in refs),
        reason=None,
        references=refs,
        observed_through_date=observed_through_date,
    )


class OriginFeatures:
    """One frozen view reused across all series, targets and models for this origin."""

    def __init__(self, tables: dict[str, list[Row]], origin: Origin) -> None:
        if set(tables) != set(TABLES):
            raise SnapshotError("forecast_input_tables_not_allowlisted")
        self.origin = origin
        known: dict[str, list[Row]] = {}
        for table, rows in tables.items():
            known[table] = [
                r
                for r in rows
                if r.get("curated_available_at") is not None
                and r["curated_available_at"] <= origin.availability_cutoff
            ]
        self.catalog = {r["id"]: r for r in latest(known["product_catalog"], ("id",))}
        self.assignments: dict[tuple[str, str], list[Row]] = defaultdict(list)
        self.assortment: dict[Series, list[Row]] = defaultdict(list)
        self.prices: dict[str, list[Row]] = defaultdict(list)
        self.promotions: dict[str, list[Row]] = defaultdict(list)
        for table, logical_key, destination, dimensions in (
            (
                "channel_assignments",
                "assignment_key",
                self.assignments,
                ("selling_location_id", "channel"),
            ),
            (
                "assortment",
                "assortment_key",
                self.assortment,
                ("product_id", "selling_location_id", "channel"),
            ),
        ):
            for row in latest(known[table], (logical_key, "effective_from", "effective_to")):
                destination[tuple(row[k] for k in dimensions)].append(row)
        for table, logical_key, plan_destination in (
            ("price_plans", "plan_key", self.prices),
            ("promotion_plans", "promotion_key", self.promotions),
        ):
            for row in latest(known[table], (logical_key, "effective_from", "effective_to")):
                plan_destination[row["product_id"]].append(row)
        self.observations = {
            tuple(
                r[k] for k in ("business_date", "product_id", "selling_location_id", "channel")
            ): r
            for r in latest(
                known["daily_demand_versions"],
                ("business_date", "product_id", "selling_location_id", "channel"),
            )
            if origin.origin_date - timedelta(days=27) <= r["business_date"] <= origin.origin_date
        }
        self.calendar = {
            tuple(r[k] for k in ("business_date", "selling_location_id", "channel")): r
            for r in latest(
                known["business_calendar"], ("business_date", "selling_location_id", "channel")
            )
        }
        self.seasons = {
            tuple(r[k] for k in ("business_date", "category_id")): r
            for r in latest(known["category_calendar"], ("business_date", "category_id"))
        }

    def active(self, series: Series, day: date) -> tuple[Reference, ...] | None:
        product, location, channel = series
        catalog = self.catalog.get(product)
        if (
            catalog is None
            or day < catalog["launch_date"]
            or (catalog["discontinue_date"] is not None and day >= catalog["discontinue_date"])
        ):
            return None
        assignment = one_interval(self.assignments.get((location, channel), []), day)
        assortment = one_interval(self.assortment.get(series, []), day)
        if assignment is None or assortment is None:
            return None
        return (
            reference("product_catalog", catalog),
            reference("channel_assignments", assignment),
            reference("assortment", assortment),
        )

    def history(self, series: Series) -> HistoryContext:
        points = []
        for offset in range(27, -1, -1):
            day = self.origin.origin_date - timedelta(days=offset)
            refs = self.active(series, day)
            if refs is None:
                continue
            observation = self.observations.get((day, *series))
            calendar = self.calendar.get((day, series[1], series[2]))
            location_open = calendar["location_open"] if calendar is not None else None
            if calendar is not None:
                refs += (reference("business_calendar", calendar),)
            status: ObservationStatus
            if (
                observation is None
                or observation.get("observed_units") is None
                or observation.get("observation_status") in {"missing", "unknown", "data_gap"}
                or observation.get("source_data_complete") is False
                or observation.get("quality_status", "valid") != "valid"
            ):
                status, units, available = "missing", None, None
            else:
                status, units = observation["observation_status"], observation["observed_units"]
                if (status == "closed" and location_open is True) or (
                    status != "closed" and location_open is False
                ):
                    raise SnapshotError("forecast_calendar_observation_closed_mismatch")
                refs += (reference("daily_demand_versions", observation),)
                available = max(r.available_at for r in refs)
            points.append(
                PanelPoint(
                    product_id=series[0],
                    selling_location_id=series[1],
                    channel=series[2],
                    forecast_origin=self.origin.forecast_origin,
                    business_date=day,
                    status=status,
                    observed_units=units,
                    source_data_complete=status != "missing",
                    location_open=location_open,
                    source_available_at=available,
                    references=refs,
                )
            )
        return HistoryContext(
            product_id=series[0],
            selling_location_id=series[1],
            channel=series[2],
            forecast_origin=self.origin.forecast_origin,
            points=tuple(points),
        )

    def historical_values(self, history: HistoryContext) -> dict[str, InputValue]:
        result = {}
        by_day = {p.business_date: p for p in history.points}
        for lag in (1, 7, 14, 28):
            point = by_day.get(self.origin.origin_date + timedelta(days=1 - lag))
            result[f"origin_lag_{lag}_units"] = value(
                f"origin_lag_{lag}_units",
                "observed",
                point.observed_units if point is not None else None,
                at=point.source_available_at if point is not None else None,
                reason="inactive_history_day" if point is None else "missing_history_day",
                observed_through_date=self.origin.origin_date + timedelta(days=1 - lag),
            )
        for window in (7, 14, 28):
            known = [
                p
                for p in history.points
                if p.business_date >= self.origin.origin_date + timedelta(days=1 - window)
                and p.observed_units is not None
            ]
            quantities = [p.observed_units for p in known if p.observed_units is not None]
            at = max(
                (p.source_available_at for p in known if p.source_available_at is not None),
                default=self.origin.forecast_origin,
            )
            result[f"rolling_mean_{window}"] = value(
                f"rolling_mean_{window}",
                "observed",
                float(mean(quantities)) if quantities else None,
                at=at,
                reason="no_known_history",
                observed_through_date=self.origin.origin_date,
            )
            result[f"rolling_std_{window}"] = value(
                f"rolling_std_{window}",
                "observed",
                float(pstdev(quantities)) if quantities else None,
                at=at,
                reason="no_known_history",
                observed_through_date=self.origin.origin_date,
            )
            result[f"rolling_count_{window}"] = value(
                f"rolling_count_{window}",
                "observed",
                len(known),
                at=self.origin.forecast_origin,
                observed_through_date=self.origin.origin_date,
            )
        return result

    def plan_values(self, series: Series, day: date) -> dict[str, InputValue]:
        product, location, channel = series

        def matches(row: Row) -> bool:
            return row["selling_location_id"] in (None, location) and row["channel"] in (
                "all",
                channel,
            )

        prices = [r for r in effective(self.prices.get(product, []), day) if matches(r)]
        if prices:
            rank = max(SCOPE_RANK[r["scope"]] for r in prices)
            prices = [r for r in prices if SCOPE_RANK[r["scope"]] == rank]
        if len(prices) > 1:
            raise SnapshotError("ambiguous_forecast_price_scope")
        price = prices[0] if prices else None
        refs = (reference("price_plans", price),) if price is not None else ()
        regular = None
        if price is not None:
            if (
                price["pricing_policy_version"] != "retail-pricing-1.0.0"
                or price["currency"] != self.catalog[product]["currency"]
            ):
                raise SnapshotError("unsupported_forecast_price_policy_or_currency")
            with localcontext() as context:
                context.prec = 50
                amount = price["price"] * Decimal(100)
            if amount != amount.to_integral_value() or amount < 0:
                raise SnapshotError("forecast_price_precision")
            regular = int(amount)
        campaigns = [
            r
            for r in effective(self.promotions.get(product, []), day)
            if matches(r) and r["status"] == "active"
        ]
        if campaigns:
            priority = max(r["priority"] for r in campaigns)
            campaigns = [r for r in campaigns if r["priority"] == priority]
        if len(campaigns) > 1:
            raise SnapshotError("ambiguous_forecast_promotion_priority")
        promotion = campaigns[0] if campaigns else None
        promo_refs = (reference("promotion_plans", promotion),) if promotion is not None else ()
        if promotion is not None and (
            promotion["stacking_policy"] != "exclusive_highest_priority"
            or promotion["pricing_policy_version"] != "retail-pricing-1.0.0"
        ):
            raise SnapshotError("unsupported_forecast_promotion_policy")
        discount_basis_points = None
        if promotion is not None:
            with localcontext() as context:
                context.prec = 50
                amount = promotion["discount_percent"] * Decimal(100)
            if (
                amount != amount.to_integral_value()
                or not 0 <= amount <= 10000
                or promotion["minimum_quantity"] < 1
            ):
                raise SnapshotError("forecast_promotion_precision_or_quantity")
            discount_basis_points = int(amount)
        return {
            "planned_regular_price_minor_units": value(
                "planned_regular_price_minor_units", "known_plan", regular, refs
            ),
            "currency": value(
                "currency", "known_plan", price["currency"] if price is not None else None, refs
            ),
            "planned_promotion_offered": value(
                "planned_promotion_offered",
                "known_plan",
                promotion is not None,
                promo_refs,
                at=self.origin.forecast_origin,
            ),
            "planned_promotion_type": value(
                "planned_promotion_type",
                "known_plan",
                promotion["promotion_type"] if promotion is not None else None,
                promo_refs,
                status="not_applicable",
                reason="no_known_campaign",
            ),
            "planned_promotion_discount_basis_points": value(
                "planned_promotion_discount_basis_points",
                "known_plan",
                discount_basis_points,
                promo_refs,
                status="not_applicable",
                reason="no_known_campaign",
            ),
            "planned_promotion_minimum_quantity": value(
                "planned_promotion_minimum_quantity",
                "known_plan",
                promotion["minimum_quantity"] if promotion is not None else None,
                promo_refs,
                status="not_applicable",
                reason="no_known_campaign",
            ),
        }

    def targets(self, history: HistoryContext) -> list[InputRow]:
        series = (history.product_id, history.selling_location_id, history.channel)
        historical = self.historical_values(history)
        context_sha256 = history.content_sha256()
        rows = []
        for target in self.origin.targets:
            refs = self.active(series, target.target_date)
            if refs is None:
                continue
            values = dict(historical)
            day = target.target_date
            for name, payload in (
                ("target_weekday", day.weekday()),
                ("target_week_of_year", day.isocalendar().week),
                ("target_month", day.month),
                ("target_quarter", (day.month - 1) // 3 + 1),
                ("target_is_weekend", day.weekday() >= 5),
            ):
                values[name] = value(name, "calendar", payload, at=self.origin.forecast_origin)
            calendar = self.calendar.get((day, series[1], series[2]))
            calendar_refs = (
                (reference("business_calendar", calendar),) if calendar is not None else ()
            )
            for name in (
                "is_public_holiday",
                "is_easter",
                "is_christmas",
                "is_black_friday",
                "is_cyber_monday",
                "location_open",
            ):
                values["target_" + name] = value(
                    "target_" + name,
                    "calendar",
                    calendar[name] if calendar is not None else None,
                    calendar_refs,
                )
            for name in ("country_code", "calendar_jurisdiction"):
                values[name] = value(
                    name,
                    "categorical",
                    calendar[name] if calendar is not None else None,
                    calendar_refs,
                )
            catalog = self.catalog[series[0]]
            for name in ("category_id", "brand"):
                values[name] = value(name, "categorical", catalog[name], refs)
            values["channel"] = value("channel", "categorical", series[2], refs)
            season = self.seasons.get((day, catalog["category_id"]))
            values["target_is_category_season"] = value(
                "target_is_category_season",
                "calendar",
                season["is_category_season"] if season is not None else None,
                (reference("category_calendar", season),) if season is not None else (),
            )
            values.update(self.plan_values(series, day))
            rows.append(
                InputRow(
                    product_id=series[0],
                    selling_location_id=series[1],
                    channel=series[2],
                    forecast_origin=self.origin.forecast_origin,
                    business_timezone="UTC",
                    cutoff_policy="end_of_day_second_v1",
                    target_date=day,
                    horizon_days=target.horizon_days,
                    history_context_sha256=context_sha256,
                    history_active_days=len(history.points),
                    history_known_days=sum(p.status != "missing" for p in history.points),
                    history_missing_days=sum(p.status == "missing" for p in history.points),
                    history_closed_days=sum(p.status == "closed" for p in history.points),
                    insufficient_history=len(history.points) < 28
                    or sum(p.status != "missing" for p in history.points) < 7,
                    target_calendar_eligible=calendar is not None and calendar["location_open"],
                    values=tuple(
                        values[n]
                        if values[n].kind == "observed"
                        else values[n].model_copy(update={"effective_date": day})
                        for n in FEATURE_TYPES
                    ),
                )
            )
        return rows
