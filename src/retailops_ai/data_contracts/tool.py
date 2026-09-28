"""One bounded read-only forecast tool wire contract; no executor or principal."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    PredictionDatasetID,
    Symbol,
    UtcTime,
    Versioned,
    end_of_day,
)
from retailops_ai.data_contracts.prediction import PredictionRecord


class ForecastScope(Contract):
    product_ids: list[Symbol] = Field(min_length=1, max_length=20)
    selling_location_ids: list[Symbol] = Field(min_length=1, max_length=5)
    channel: Literal["store", "online"]
    target_from: date
    target_to: date

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not 0 <= (self.target_to - self.target_from).days < 90:
            raise ValueError("tool_period_outside_budget")
        if len(set(self.product_ids)) != len(self.product_ids) or len(
            set(self.selling_location_ids)
        ) != len(self.selling_location_ids):
            raise ValueError("duplicate_tool_scope")
        return self


class ToolRequest(Versioned):
    contract_type: Literal["tool_request"]
    tool: Literal["get_demand_forecast"]
    as_of: UtcTime
    scope: ForecastScope
    limit: Annotated[int, Field(ge=1, le=50)]

    @model_validator(mode="after")
    def supported_origin(self) -> Self:
        if self.as_of != end_of_day(self.as_of.date()):
            raise ValueError("tool_origin_requires_utc_end_of_day")
        if not (
            self.as_of.date() + timedelta(days=1)
            <= self.scope.target_from
            <= self.scope.target_to
            <= self.as_of.date() + timedelta(days=14)
        ):
            raise ValueError("tool_targets_outside_supported_horizon")
        return self


ERRORS = {
    "invalid_scope": ("The requested scope is invalid.", False),
    "unauthorized": ("The requested scope is not authorized.", False),
    "unavailable": ("The required source is unavailable.", True),
    "stale": ("The requested source is stale.", False),
    "not_found": ("The requested output was not found.", False),
    "unsupported_grain": ("The requested grain is unsupported.", False),
    "budget_exceeded": ("The tool budget was exceeded.", False),
}


class ToolError(Contract):
    code: Literal[
        "invalid_scope",
        "unauthorized",
        "unavailable",
        "stale",
        "not_found",
        "unsupported_grain",
        "budget_exceeded",
    ]
    description: Literal[
        "The requested scope is invalid.",
        "The requested scope is not authorized.",
        "The required source is unavailable.",
        "The requested source is stale.",
        "The requested output was not found.",
        "The requested grain is unsupported.",
        "The tool budget was exceeded.",
    ]
    retryable: bool

    @model_validator(mode="after")
    def safe_message(self) -> Self:
        if (self.description, self.retryable) != ERRORS[self.code]:
            raise ValueError("tool_error_is_not_canonical")
        return self


class ToolResult(Versioned):
    contract_type: Literal["tool_result"]
    tool: Literal["get_demand_forecast"]
    request: ToolRequest
    status: Literal["ok", "no_data", "error"]
    as_of: UtcTime | None
    freshness_status: Literal["current", "stale", "unknown", "missing", "unavailable"]
    source_ref: PredictionDatasetID | None
    items: list[PredictionRecord] = Field(max_length=50)
    error: ToolError | None

    @model_validator(mode="after")
    def result_scope(self) -> Self:
        if len(self.items) > self.request.limit:
            raise ValueError("tool_result_exceeds_request_limit")
        if self.status == "ok":
            if (
                not self.items
                or self.error is not None
                or self.as_of != self.request.as_of
                or self.source_ref is None
            ):
                raise ValueError("tool_success_incomplete")
            if self.freshness_status not in {"current", "stale", "unknown"}:
                raise ValueError("tool_success_freshness_mismatch")
        elif self.status == "no_data":
            if (
                self.items
                or self.error is not None
                or self.freshness_status != "missing"
                or self.as_of is not None
            ):
                raise ValueError("tool_no_data_must_not_fabricate_values")
        elif (
            self.items
            or self.error is None
            or self.as_of is not None
            or self.source_ref is not None
            or self.freshness_status != "unavailable"
        ):
            raise ValueError("tool_error_result_mismatch")
        if len({item.prediction_id for item in self.items}) != len(self.items):
            raise ValueError("duplicate_tool_prediction")
        for item in self.items:
            scope = self.request.scope
            key = item.key
            if (
                key.product_id not in scope.product_ids
                or key.selling_location_id not in scope.selling_location_ids
                or key.channel != scope.channel
                or not scope.target_from <= key.target_date <= scope.target_to
            ):
                raise ValueError("tool_item_outside_requested_scope")
            if (
                key.forecast_origin != self.as_of
                or item.prediction_dataset_id != self.source_ref
                or item.freshness_status != self.freshness_status
            ):
                raise ValueError("tool_result_lineage_or_freshness_mismatch")
        return self
