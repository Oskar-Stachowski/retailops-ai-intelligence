"""Lossless v12 read evidence; never synthesize the legacy forecast/model contract."""

from datetime import timedelta
from typing import Literal, Self

from pydantic import model_validator

from retailops_ai.data_contracts.common import UtcTime, Versioned
from retailops_ai.data_contracts.tool import ToolRequest
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem, V12ForecastPage
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL


class NativeForecastRead(Versioned):
    contract_type: Literal["forecast_v12_tool_result"] = "forecast_v12_tool_result"
    request: ToolRequest
    environment: Literal["local", "test"]
    page: V12ForecastPage

    @model_validator(mode="after")
    def binding(self) -> Self:
        request, page = self.request, self.page
        if (
            page.selection != "origin"
            or page.pagination.offset != 0
            or page.pagination.limit != request.limit
            or page.pagination.next_offset is not None
            or page.pagination.total != len(page.items)
            or len(page.items) > request.limit
            or (page.data_status == "available") != bool(page.items)
            or page.generated_at < request.as_of
            or len({row.prediction_id for row in page.items}) != len(page.items)
        ):
            raise ValueError("native_forecast_incomplete_or_unbound_page")
        keys = set()
        for row in page.items:
            key = (row.product_id, row.selling_location_id, row.channel, row.target_date)
            if (
                key in keys
                or row.product_id not in request.scope.product_ids
                or row.selling_location_id not in request.scope.selling_location_ids
                or row.channel != request.scope.channel
                or not request.scope.target_from <= row.target_date <= request.scope.target_to
                or row.forecast_origin != request.as_of
                or row.target_date != request.as_of.date() + timedelta(days=row.horizon_days)
                or row.model_name != MODEL
                or row.generated_at > page.generated_at
                or row.approval_valid_until <= page.generated_at
                or row.freshness.evaluated_at != page.generated_at
                or abs(
                    row.freshness.origin_age_seconds
                    - (page.generated_at - row.forecast_origin).total_seconds()
                )
                > 1e-6
            ):
                raise ValueError("native_forecast_row_binding_invalid")
            keys.add(key)
        if page.items and keys != {
            (
                product,
                location,
                request.scope.channel,
                request.scope.target_from + timedelta(days=d),
            )
            for product in request.scope.product_ids
            for location in request.scope.selling_location_ids
            for d in range((request.scope.target_to - request.scope.target_from).days + 1)
        }:
            raise ValueError("native_forecast_partial_requested_coverage")
        return self

    @property
    def status(self) -> Literal["ok", "no_data"]:
        return "ok" if self.page.items else "no_data"

    @property
    def items(self) -> tuple[V12ForecastItem, ...]:
        return self.page.items

    @property
    def as_of(self) -> UtcTime | None:
        return self.request.as_of if self.page.items else None

    @property
    def freshness_status(self) -> Literal["current", "stale", "unknown", "missing"]:
        if not self.page.items:
            return "missing"
        statuses = {row.freshness.status for row in self.page.items}
        return "stale" if "stale" in statuses else "unknown" if "unknown" in statuses else "current"

    @property
    def source_ref(self) -> str:
        return "forecast-view-sha256-" + self.page.view_sha256

    @property
    def error(self) -> None:
        return None
