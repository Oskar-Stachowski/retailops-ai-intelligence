"""Reuse AI04's fixed-origin baseline with real selling keys and physical routing."""

import hashlib
from datetime import datetime
from math import fsum
from typing import Any

from retailops_ai.data_contracts.common import end_of_day, utc_time
from retailops_ai.forecasting.baselines import predict
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.evaluation_contract import BaselinePolicy
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import TABLES, InputRow
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.stockout.features import Records, effective, latest
from retailops_ai.stockout.upstream_contract import (
    DEFAULT_UPSTREAM_POLICY,
    SeriesForecast,
    UpstreamPoint,
    UpstreamPolicy,
)

UPSTREAM_TABLES = tuple(sorted(set(TABLES) | {"fulfillment_routes"}))


def baseline_code() -> dict[str, str]:
    from importlib.resources import files

    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(files("retailops_ai.forecasting").iterdir(), key=lambda r: r.name)
        if r.is_file() and r.name.endswith(".py")
    }
    code["data_contracts/identity.py"] = hashlib.sha256(
        files("retailops_ai.data_contracts").joinpath("identity.py").read_bytes()
    ).hexdigest()
    return code


def model_version(policy: UpstreamPolicy = DEFAULT_UPSTREAM_POLICY) -> str:
    return (
        "baseline-sha256-"
        + hashlib.sha256(
            canonical_json(dict(policy=policy.model_dump(mode="json"), code=baseline_code()))
        ).hexdigest()
    )


def series_forecast(
    view: OriginFeatures, product: str, route: dict[str, Any], policy: UpstreamPolicy
) -> SeriesForecast:
    series = (product, route["selling_location_id"], route["channel"])
    history = view.history(series)
    inputs = {r.horizon_days: r for r in view.targets(history) if r.horizon_days <= 7}
    daily: list[float | None] = []
    reason = None
    baseline = BaselinePolicy(
        moving_average_calendar_days=28, moving_average_minimum_known_days=policy.minimum_known_days
    )
    for horizon in range(1, 8):
        row: InputRow | None = inputs.get(horizon)
        if row is None:
            daily.append(None)
            reason = reason or "inactive_or_unknown_target_assortment"
            continue
        location_open = next(v.value for v in row.values if v.name == "target_location_open")
        if location_open is None:
            daily.append(None)
            reason = reason or "target_calendar_unknown"
        elif location_open is False:
            daily.append(0.0)
        else:
            estimate = predict("moving_average", row, history, baseline)
            daily.append(estimate.predicted_units)
            reason = reason or estimate.reason
    availability = [
        route["curated_available_at"],
        *(r.available_at for p in history.points for r in p.references),
        *(
            r.available_at
            for point in inputs.values()
            for value in point.values
            for r in value.references
        ),
    ]
    return SeriesForecast(
        selling_location_id=series[1],
        channel=series[2],
        route_record_sha256=route["source_record_sha256"],
        history_context_sha256=history.content_sha256(),
        input_rows_sha256=hashlib.sha256(
            canonical_json([inputs[n].model_dump(mode="json") for n in sorted(inputs)])
        ).hexdigest(),
        source_available_at=max(availability),
        daily_units=tuple(daily),
        reason=reason,
    )


def upstream_point(
    records: Records,
    *,
    product: str,
    stock: str,
    as_of: datetime,
    policy: UpstreamPolicy = DEFAULT_UPSTREAM_POLICY,
    version: str | None = None,
    view: OriginFeatures | None = None,
) -> UpstreamPoint:
    as_of = utc_time(as_of)
    origin = end_of_day(as_of.date())
    if origin > as_of:
        raise SnapshotError("stockout_upstream_origin_would_use_future_information")
    if set(records) != set(UPSTREAM_TABLES):
        raise SnapshotError("stockout_upstream_tables_not_allowlisted")
    known = {
        name: [
            r
            for r in rows
            if r["curated_available_at"] is not None and r["curated_available_at"] <= origin
        ]
        for name, rows in records.items()
    }
    routes = latest(effective(known["fulfillment_routes"], as_of.date()), ("route_key",))
    if len({(r["selling_location_id"], r["channel"]) for r in routes}) != len(routes):
        raise SnapshotError("stockout_upstream_ambiguous_physical_routing")
    if view is None:
        view = OriginFeatures({n: known[n] for n in TABLES}, make_origin(as_of.date()))
    elif view.origin.forecast_origin != origin:
        raise SnapshotError("stockout_upstream_frozen_view_origin_mismatch")
    chosen = [
        r
        for r in routes
        if r["stock_location_id"] == stock
        and view.active((product, r["selling_location_id"], r["channel"]), as_of.date()) is not None
    ]
    series = tuple(
        series_forecast(view, product, r, policy)
        for r in sorted(chosen, key=lambda r: (r["selling_location_id"], r["channel"]))
    )
    reason = (
        "inactive_or_unknown_origin_mapping"
        if not series
        else next((s.reason for s in series if s.reason), None)
    )
    return UpstreamPoint(
        product_id=product,
        stock_location_id=stock,
        as_of=as_of,
        forecast_origin=origin,
        training_cutoff=origin,
        selection_cutoff=origin,
        source_available_at=max(
            (s.source_available_at for s in series if s.source_available_at is not None),
            default=None,
        ),
        upstream_model_version=version or model_version(policy),
        status="available" if reason is None else "insufficient_data",
        reason=reason,
        forecast_units_7d=fsum(v for s in series for v in s.daily_units if v is not None)
        if reason is None
        else None,
        series=series,
    )
