"""Explicit SQL-only synthetic clock fixtures; never evidence of a qualified AI04 source."""

import json
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import JsonValue

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.inputs import InputContent, PreparedInputs, prepared
from retailops_ai.forecast_jobs.source_freshness import source_freshness
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import TABLES, HistoryContext, InputRow
from retailops_ai.forecasting.manifest_contract import FeatureManifest


def fixture(
    inputs: PreparedInputs,
    origin: datetime,
    *,
    complete_through: date | None,
    missing_origin: bool = False,
    missing_history_days: int = 0,
    origin_closed: bool = False,
    policy_version: str = "daily-demand-1.0.0",
    omit_watermark: bool = False,
    declaration_as_of: datetime | None = None,
) -> PreparedInputs:
    shift = origin - inputs.as_of_time

    def shifted(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: shifted(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [shifted(v) for v in value]
        if value is not None and key in {
            "forecast_origin",
            "available_at",
            "source_available_at",
        }:
            return (datetime.fromisoformat(value) + shift).isoformat()
        if value is not None and key in {
            "business_date",
            "target_date",
            "effective_date",
            "observed_through_date",
        }:
            return (date.fromisoformat(value) + shift).isoformat()
        return value

    histories = []
    for history in inputs.histories:
        raw = shifted(history.model_dump(mode="json"))
        for p in raw["points"]:
            point_day = date.fromisoformat(p["business_date"])
            if point_day > origin.date() - timedelta(
                days=max(int(missing_origin), missing_history_days)
            ):
                p.update(
                    status="missing",
                    observed_units=None,
                    source_data_complete=False,
                    source_available_at=None,
                )
            elif point_day == origin.date():
                p.update(
                    status="closed" if origin_closed else "observed_zero",
                    observed_units=0,
                    source_data_complete=True,
                    source_available_at=origin.isoformat(),
                    location_open=not origin_closed,
                    references=[],
                )
        histories.append(HistoryContext.model_validate_json(json.dumps(raw)))
    contexts = {(h.product_id, h.selling_location_id, h.channel): h for h in histories}
    view = OriginFeatures({t: [] for t in TABLES}, make_origin(origin.date()))
    rows = []
    for row in inputs.rows:
        raw = shifted(row.model_dump(mode="json"))
        h = contexts[(row.product_id, row.selling_location_id, row.channel)]
        observed = view.historical_values(h)
        values = {v["name"]: v for v in raw["values"]}
        target = date.fromisoformat(raw["target_date"])
        for name, val in {
            "target_weekday": target.weekday(),
            "target_week_of_year": target.isocalendar().week,
            "target_month": target.month,
            "target_quarter": (target.month - 1) // 3 + 1,
            "target_is_weekend": target.weekday() >= 5,
        }.items():
            values[name]["value"] = val
        for name, feature in observed.items():
            values[name] = feature.model_dump(mode="json")
        known = sum(p.status != "missing" for p in h.points)
        raw.update(
            history_context_sha256=h.content_sha256(),
            history_active_days=len(h.points),
            history_known_days=known,
            history_missing_days=len(h.points) - known,
            history_closed_days=sum(p.status == "closed" for p in h.points),
            insufficient_history=len(h.points) < 28 or known < 7,
            values=list(values.values()),
        )
        rows.append(InputRow.model_validate_json(json.dumps(raw)))
    watermark: dict[str, JsonValue] = dict(
        as_of_time=(declaration_as_of or origin + timedelta(seconds=1)).isoformat(),
        complete_through=complete_through.isoformat() if complete_through else None,
        completeness_status="complete" if complete_through else "not_ready",
        meaning="synthetic_sales_day_close_without_return_guarantee",
        policy_version=policy_version,
    )
    descriptor: dict[str, JsonValue] = dict(
        schema_version="1.0.0",
        purpose="sql_clock_fixture_only",
        parent_source_dataset_id=inputs.feature_manifest.descriptor.parent.source_dataset_id,
        watermarks={} if omit_watermark else {"daily_demand_observations": watermark},
    )
    manifest = inputs.feature_manifest.model_dump(mode="json")
    parent = manifest["descriptor"]["parent"]
    parent.update(
        curated_dataset_id="curated-sha256-" + canonical_sha256(descriptor),
        curated_descriptor_sha256=canonical_sha256(descriptor),
    )
    manifest["feature_set_id"] = "features-sha256-" + canonical_sha256(manifest["descriptor"])
    return prepared(
        InputContent(
            schema_version="1.1",
            feature_manifest=FeatureManifest.model_validate_json(json.dumps(manifest)),
            as_of_time=origin,
            scope=inputs.scope,
            horizon_days=inputs.horizon_days,
            rows=tuple(rows),
            histories=tuple(histories),
            source_freshness=source_freshness(descriptor, origin, tuple(histories)),
        )
    )
