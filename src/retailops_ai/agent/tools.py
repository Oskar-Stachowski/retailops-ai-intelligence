"""Closed, typed read-only catalogue. Arguments never carry identity or provider URLs."""

from datetime import timedelta
from typing import Annotated, Generic, Literal, Self, TypeVar

from pydantic import Field, TypeAdapter, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DateWindow,
    ModelID,
    SellingKey,
    Symbol,
    TrueFlag,
    Units,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.tool import ToolError, ToolRequest, ToolResult
from retailops_ai.knowledge.indexes import IndexID
from retailops_ai.knowledge.retrieval import KnowledgeHit, RetrievalConfigID, RetrievalRequest

ToolName = Literal[
    "get_sales_summary",
    "get_inventory_status",
    "get_demand_forecast",
    "get_stockout_risk",
    "get_detected_anomalies",
    "get_live_operations",
    "get_model_status",
    "search_knowledge",
]
SourceRef = Annotated[
    str,
    Field(pattern=r"^(?:[a-z][a-z0-9_-]{0,31}-sha256-[0-9a-f]{64}|fixture-[A-Za-z0-9_.-]{1,100})$"),
]
ReadLimit = Annotated[int, Field(ge=1, le=50)]


class DataScope(Contract):
    product_ids: list[Symbol] = Field(min_length=1, max_length=20)
    selling_location_ids: list[Symbol] = Field(min_length=1, max_length=5)
    channel: Literal["store", "online"]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.product_ids)) != len(self.product_ids) or len(
            set(self.selling_location_ids)
        ) != len(self.selling_location_ids):
            raise ValueError("duplicate_tool_scope")
        return self


class DataRequest(Versioned):
    contract_type: Literal["tool_request"]
    scope: DataScope | None
    as_of: UtcTime
    limit: ReadLimit = 20


class SalesRequest(DataRequest):
    tool: Literal["get_sales_summary"]
    window: DateWindow
    grain: Literal["product_selling_location_channel_period"]

    @model_validator(mode="after")
    def window_budget(self) -> Self:
        if (self.window.end - self.window.start).days >= 90 or self.window.end > self.as_of.date():
            raise ValueError("sales_window_outside_budget")
        return self


class InventoryRequest(DataRequest):
    tool: Literal["get_inventory_status"]


class RiskRequest(DataRequest):
    tool: Literal["get_stockout_risk"]
    window: DateWindow

    @model_validator(mode="after")
    def horizon(self) -> Self:
        if not (
            self.as_of.date()
            < self.window.start
            <= self.window.end
            <= self.as_of.date() + timedelta(days=14)
        ):
            raise ValueError("risk_horizon_outside_budget")
        return self


class AnomalyRequest(DataRequest):
    tool: Literal["get_detected_anomalies"]
    window: DateWindow

    @model_validator(mode="after")
    def window_budget(self) -> Self:
        if (self.window.end - self.window.start).days >= 90 or self.window.end > self.as_of.date():
            raise ValueError("anomaly_window_outside_budget")
        return self


class OperationsRequest(DataRequest):
    tool: Literal["get_live_operations"]


class ModelStatusRequest(DataRequest):
    tool: Literal["get_model_status"]


class KnowledgeRequest(Versioned):
    contract_type: Literal["tool_request"]
    tool: Literal["search_knowledge"]
    retrieval: RetrievalRequest


ToolInput = Annotated[
    SalesRequest
    | InventoryRequest
    | ToolRequest
    | RiskRequest
    | AnomalyRequest
    | OperationsRequest
    | ModelStatusRequest
    | KnowledgeRequest,
    Field(discriminator="tool"),
]
INPUT: TypeAdapter[ToolInput] = TypeAdapter(ToolInput)


class SalesItem(SellingKey):
    window: DateWindow
    observed_sales_units: Units
    unit_of_measure: Literal["unit"]


class InventoryItem(SellingKey):
    stock_location_id: Symbol
    mapping_ref: SourceRef
    physical_units: Units
    reserved_units: Units
    available_units: Units
    unit_of_measure: Literal["unit"]

    @model_validator(mode="after")
    def reconciliation(self) -> Self:
        if (
            self.reserved_units > self.physical_units
            or abs(self.available_units - (self.physical_units - self.reserved_units)) > 1e-9
        ):
            raise ValueError("inventory_units_do_not_reconcile")
        return self


class RiskItem(SellingKey):
    window: DateWindow
    probability: Annotated[float, Field(ge=0, le=1)]
    threshold: Annotated[float, Field(ge=0, le=1)]
    calibrated: TrueFlag
    model_id: ModelID
    model_release_ref: SourceRef
    inventory_source_ref: SourceRef
    inventory_as_of: UtcTime


class AnomalyItem(SellingKey):
    window: DateWindow
    expected_units: Units
    observed_units: Units
    detector_version: Symbol
    model_release_ref: SourceRef


class OperationsItem(SellingKey):
    stream_status: Literal["healthy", "degraded", "stopped"]
    lag_seconds: Annotated[float, Field(ge=0)]


class ModelStatusItem(SellingKey):
    model_id: ModelID
    deployed_release_ref: SourceRef
    deployment_environment: Literal["local", "test"]
    alias: Symbol | None
    evaluation_ref: SourceRef
    approved: TrueFlag


ItemT = TypeVar("ItemT", bound=Contract)


class DataResult(Versioned, Generic[ItemT]):
    contract_type: Literal["agent_tool_result"]
    status: Literal["ok", "no_data", "error"]
    as_of: UtcTime | None
    freshness_status: Literal["current", "stale", "missing", "unavailable"]
    source_ref: SourceRef | None
    items: list[ItemT] = Field(max_length=50)
    error: ToolError | None
    source_kind: Literal["fixture", "runtime"]

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.status == "ok":
            if (
                not self.items
                or self.error is not None
                or self.as_of is None
                or self.source_ref is None
            ):
                raise ValueError("tool_success_incomplete")
            if self.freshness_status not in {"current", "stale"}:
                raise ValueError("tool_success_freshness_mismatch")
        elif self.status == "no_data":
            if (
                self.items
                or self.error is not None
                or self.freshness_status != "missing"
                or self.as_of is None
                or self.source_ref is None
            ):
                raise ValueError("no_data_requires_checked_source")
        elif (
            self.items
            or self.error is None
            or self.as_of is not None
            or self.source_ref is not None
            or self.freshness_status != "unavailable"
        ):
            raise ValueError("tool_error_result_mismatch")
        if self.source_ref is not None and self.source_ref.startswith("fixture-") != (
            self.source_kind == "fixture"
        ):
            raise ValueError("tool_source_kind_mismatch")
        return self


class SalesResult(DataResult[SalesItem]):
    tool: Literal["get_sales_summary"]


class InventoryResult(DataResult[InventoryItem]):
    tool: Literal["get_inventory_status"]


class RiskResult(DataResult[RiskItem]):
    tool: Literal["get_stockout_risk"]


class AnomalyResult(DataResult[AnomalyItem]):
    tool: Literal["get_detected_anomalies"]


class OperationsResult(DataResult[OperationsItem]):
    tool: Literal["get_live_operations"]


class ModelStatusResult(DataResult[ModelStatusItem]):
    tool: Literal["get_model_status"]


class ForecastResult(Versioned):
    contract_type: Literal["agent_tool_result"]
    tool: Literal["get_demand_forecast"]
    result: ToolResult
    source_kind: Literal["fixture", "runtime"]


class KnowledgeResult(Versioned):
    contract_type: Literal["agent_tool_result"]
    tool: Literal["search_knowledge"]
    status: Literal["ok", "no_data"]
    index_id: IndexID
    pin_generation: Annotated[int, Field(ge=1)]
    provider: Literal["bedrock"]
    retrieval_config_id: RetrievalConfigID
    content_trust: Literal["untrusted_reference"]
    items: list[KnowledgeHit] = Field(max_length=5)
    context_tokens: Annotated[int, Field(ge=0, le=6000)]
    context_bytes: Annotated[int, Field(ge=0, le=24000)]
    source_kind: Literal["runtime", "fixture"]

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.status == "ok") != bool(self.items):
            raise ValueError("knowledge_outcome_mismatch")
        return self


ToolOutput = Annotated[
    SalesResult
    | InventoryResult
    | ForecastResult
    | RiskResult
    | AnomalyResult
    | OperationsResult
    | ModelStatusResult
    | KnowledgeResult,
    Field(discriminator="tool"),
]
OUTPUT: TypeAdapter[ToolOutput] = TypeAdapter(ToolOutput)


class ToolPolicy(Versioned):
    profile: Literal["agent-tools-bounded-v1"]
    max_calls: Annotated[int, Field(ge=1, le=6)] = 6
    request_deadline_seconds: Annotated[float, Field(gt=0, le=45)] = 45.0
    tool_timeout_seconds: Annotated[float, Field(gt=0, le=5)] = 5.0
    max_rows: Annotated[int, Field(ge=1, le=50)] = 50
    max_period_days: Annotated[int, Field(ge=1, le=90)] = 90
    max_products: Annotated[int, Field(ge=1, le=20)] = 20
    max_locations: Annotated[int, Field(ge=1, le=5)] = 5
    max_result_bytes: Annotated[int, Field(ge=1024, le=131072)] = 131072
    freshness_seconds: Annotated[int, Field(ge=1, le=86400)] = 300

    def config_id(self) -> str:
        return "agent-tool-policy-sha256-" + canonical_sha256(self.model_dump(mode="json"))


REQUEST_MODELS: dict[str, type[Contract]] = {
    "get_sales_summary": SalesRequest,
    "get_inventory_status": InventoryRequest,
    "get_demand_forecast": ToolRequest,
    "get_stockout_risk": RiskRequest,
    "get_detected_anomalies": AnomalyRequest,
    "get_live_operations": OperationsRequest,
    "get_model_status": ModelStatusRequest,
    "search_knowledge": KnowledgeRequest,
}
RESULT_MODELS: dict[str, type[Contract]] = {
    "get_sales_summary": SalesResult,
    "get_inventory_status": InventoryResult,
    "get_demand_forecast": ForecastResult,
    "get_stockout_risk": RiskResult,
    "get_detected_anomalies": AnomalyResult,
    "get_live_operations": OperationsResult,
    "get_model_status": ModelStatusResult,
    "search_knowledge": KnowledgeResult,
}
