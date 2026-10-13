"""Closed, typed read-only catalogue. Arguments never carry identity or provider URLs."""

from datetime import date, timedelta
from typing import Annotated, Any, Generic, Literal, Self, TypeVar

from pydantic import Field, TypeAdapter, model_validator

from retailops_ai.agent.native_forecast import NativeForecastRead
from retailops_ai.anomaly_portfolio.serving_contract import Item as NativeDetection
from retailops_ai.anomaly_portfolio.serving_contract import Page as AnomalyPage
from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    DateWindow,
    ModelID,
    SellingKey,
    Sha256,
    SourceID,
    Symbol,
    TrueFlag,
    Units,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.tool import ToolError, ToolRequest, ToolResult
from retailops_ai.day_qualification.contract import Point
from retailops_ai.knowledge.indexes import IndexID
from retailops_ai.knowledge.retrieval import KnowledgeHit, RetrievalConfigID, RetrievalRequest
from retailops_ai.model_lifecycle.read_contracts import CatalogModel, ModelPage
from retailops_ai.model_lifecycle.v12_metadata_contracts import V12CatalogModel, V12ModelPage
from retailops_ai.raw_dq.contract import stamp
from retailops_ai.stockout_jobs.read_contracts import StockoutRisk, StockoutRiskPage

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
MAX_QUALIFIED_SALES_POINTS = 200


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


class QualifiedSalesEvidence(Contract):
    """Exact causal days and verified parents, retained with the accepted tool output."""

    version: Literal["qualified-sales-days-v1"] = "qualified-sales-days-v1"
    environment: Literal["local", "test"]
    request: SalesRequest
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    full_dq_descriptor_sha256: Sha256
    day_coverage_id: Annotated[str, Field(pattern=r"^day-coverage-sha256-[0-9a-f]{64}$")]
    day_coverage_descriptor_sha256: Sha256
    qualification_runtime_sha256: Sha256
    points: tuple[Point, ...] = Field(min_length=1, max_length=MAX_QUALIFIED_SALES_POINTS)

    @model_validator(mode="after")
    def exact_days(self) -> Self:
        request = self.request
        scope = request.scope
        if (
            self.full_dq_replay_id != "full-dq-replay-sha256-" + self.full_dq_descriptor_sha256
            or self.day_coverage_id != "day-coverage-sha256-" + self.day_coverage_descriptor_sha256
        ):
            raise ValueError("qualified_sales_parent_identity_mismatch")
        if scope is None:
            raise ValueError("qualified_sales_scope_required")
        days = (request.window.end - request.window.start).days + 1
        expected = {
            (
                product,
                location,
                scope.channel,
                (request.window.start + timedelta(days=i)).isoformat(),
            )
            for product in scope.product_ids
            for location in scope.selling_location_ids
            for i in range(days)
        }
        actual = {
            (p.product_id, p.selling_location_id, p.channel, p.business_date) for p in self.points
        }
        if (
            len(actual) != len(self.points)
            or actual != expected
            or len(scope.product_ids) * len(scope.selling_location_ids) > request.limit
            or any(
                p.event_type != "sale_completed" or stamp(p.as_of) != request.as_of
                for p in self.points
            )
        ):
            raise ValueError("qualified_sales_day_grid_mismatch")
        currencies: dict[tuple[str, str, str], set[str]] = {}
        for point in self.points:
            key = (point.product_id, point.selling_location_id, point.channel)
            currencies.setdefault(key, set()).add(point.currency)
        if any(len(values) != 1 for values in currencies.values()):
            raise ValueError("qualified_sales_currency_changed")
        # The existing public unit type is float. Refuse an inexact integer conversion.
        for item in self.sales_items():
            total = sum(
                p.observed_units or 0
                for p in self.points
                if (p.product_id, p.selling_location_id, p.channel)
                == (item.product_id, item.selling_location_id, item.channel)
            )
            if item.observed_sales_units != total:
                raise ValueError("qualified_sales_units_precision")
        return self

    @property
    def complete(self) -> bool:
        return all(p.status == "qualified" for p in self.points)

    @property
    def view_ref(self) -> str:
        return "sales-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    def sales_items(self) -> list[SalesItem]:
        if not self.complete:
            return []
        scope = self.request.scope
        if scope is None:
            raise ValueError("qualified_sales_scope_required")
        totals: dict[tuple[str, str, str], int] = {}
        for point in self.points:
            if point.observed_units is None:
                raise ValueError("qualified_sales_units_missing")
            key = (point.product_id, point.selling_location_id, point.channel)
            totals[key] = totals.get(key, 0) + point.observed_units
        return [
            SalesItem(
                product_id=product,
                selling_location_id=location,
                channel=scope.channel,
                window=self.request.window,
                observed_sales_units=float(total),
                unit_of_measure="unit",
            )
            for (product, location, channel), total in sorted(totals.items())
        ]


class SalesResult(DataResult[SalesItem]):
    tool: Literal["get_sales_summary"]
    qualified_days: QualifiedSalesEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def qualified_outcome(self) -> Self:
        evidence = self.qualified_days
        if evidence is not None and (
            self.source_kind != "runtime"
            or self.as_of != evidence.request.as_of
            or self.source_ref != evidence.view_ref
            or self.items != evidence.sales_items()
            or self.status != ("ok" if evidence.complete else "no_data")
            or self.freshness_status != ("current" if evidence.complete else "missing")
            or self.error is not None
        ):
            raise ValueError("qualified_sales_result_binding_mismatch")
        return self


class NativeInventoryRoute(Contract):
    route_id: Symbol
    version: Annotated[int, Field(ge=1)]
    selling_location_id: Symbol
    stock_location_id: Symbol
    channel: Literal["store", "online"]
    effective_from: date
    effective_to: date
    available_at: UtcTime
    curated_available_at: UtcTime
    source_record_sha256: Sha256

    @model_validator(mode="after")
    def causal_route(self) -> Self:
        if (
            self.effective_from >= self.effective_to
            or self.available_at > self.curated_available_at
        ):
            raise ValueError("native_inventory_route_invalid")
        return self

    @property
    def mapping_ref(self) -> str:
        return "inventory-route-sha256-" + canonical_sha256(self.model_dump(mode="json"))


class NativeInventorySnapshot(Contract):
    snapshot_id: Symbol
    product_id: Symbol
    stock_location_id: Symbol
    business_date: date
    period_from_at: UtcTime
    period_to_at: UtcTime
    is_full_business_day: bool
    snapshot_at: UtcTime
    as_of_time: UtcTime
    unit_of_measure: Literal["pcs"]
    on_hand: Annotated[int, Field(ge=0)] | None
    reserved_qty: Annotated[int, Field(ge=0)] | None
    available_qty: Annotated[int, Field(ge=0)] | None
    status: Literal["known", "not_available"]
    source_available_at: UtcTime | None
    curated_available_at: UtcTime | None
    source_record_sha256: Sha256

    @model_validator(mode="after")
    def causal_snapshot(self) -> Self:
        if (
            self.snapshot_at != self.as_of_time
            or self.snapshot_at != self.period_to_at - timedelta(microseconds=1)
            or not self.period_from_at <= self.snapshot_at < self.period_to_at
            or self.business_date != self.period_from_at.date()
        ):
            raise ValueError("native_inventory_snapshot_time_mismatch")
        quantities = (self.on_hand, self.reserved_qty, self.available_qty)
        if self.status == "not_available":
            if any(
                v is not None
                for v in (*quantities, self.source_available_at, self.curated_available_at)
            ):
                raise ValueError("native_inventory_unknown_snapshot_has_values")
        elif (
            self.on_hand is None
            or self.reserved_qty is None
            or self.available_qty is None
            or self.reserved_qty > self.on_hand
            or self.available_qty != self.on_hand - self.reserved_qty
            or self.source_available_at is None
            or self.curated_available_at is None
            or not self.source_available_at <= self.snapshot_at <= self.curated_available_at
            or any(v is not None and int(float(v)) != v for v in quantities)
        ):
            raise ValueError("native_inventory_snapshot_quantity_or_availability_mismatch")
        return self


class NativeInventoryPoint(SellingKey):
    route: NativeInventoryRoute | None
    snapshot: NativeInventorySnapshot | None
    status: Literal["known", "missing_route", "missing_snapshot", "not_available", "stale"]


class NativeInventoryEvidence(Contract):
    evidence_version: Literal["verified-inventory-snapshots-v1"] = "verified-inventory-snapshots-v1"
    environment: Literal["local", "test"]
    request: InventoryRequest
    source_dataset_id: SourceID
    source_descriptor_sha256: Sha256
    curated_dataset_id: CuratedID
    curated_descriptor_sha256: Sha256
    qualification_runtime_sha256: Sha256
    max_snapshot_age_seconds: Literal[300] = 300
    points: tuple[NativeInventoryPoint, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def exact_scope(self) -> Self:
        scope = self.request.scope
        if (
            scope is None
            or len(scope.product_ids) * len(scope.selling_location_ids) > self.request.limit
            or self.source_dataset_id != "source-sha256-" + self.source_descriptor_sha256
            or self.curated_dataset_id != "curated-sha256-" + self.curated_descriptor_sha256
        ):
            raise ValueError("native_inventory_scope_or_parent_binding_mismatch")
        expected = {
            (p, s, scope.channel) for p in scope.product_ids for s in scope.selling_location_ids
        }
        actual = [(p.product_id, p.selling_location_id, p.channel) for p in self.points]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("native_inventory_partial_duplicate_or_extra_scope")
        for point in self.points:
            route, snapshot = point.route, point.snapshot
            if route is None:
                state = "missing_route"
                if snapshot is not None:
                    raise ValueError("native_inventory_snapshot_without_route")
            else:
                if (
                    route.selling_location_id != point.selling_location_id
                    or route.channel != point.channel
                    or route.curated_available_at > self.request.as_of
                    or not route.effective_from <= self.request.as_of.date() < route.effective_to
                ):
                    raise ValueError("native_inventory_noncausal_or_mismatched_route")
                state = "missing_snapshot"
                if snapshot is not None:
                    if (
                        snapshot.product_id != point.product_id
                        or snapshot.stock_location_id != route.stock_location_id
                        or snapshot.snapshot_at > self.request.as_of
                        or (
                            snapshot.curated_available_at is not None
                            and snapshot.curated_available_at > self.request.as_of
                        )
                    ):
                        raise ValueError("native_inventory_noncausal_or_mismatched_snapshot")
                    state = (
                        "not_available"
                        if snapshot.status != "known"
                        else "stale"
                        if (self.request.as_of - snapshot.snapshot_at).total_seconds()
                        > self.max_snapshot_age_seconds
                        else "known"
                    )
            if point.status != state:
                raise ValueError("native_inventory_point_status_mismatch")
        return self

    @property
    def complete(self) -> bool:
        return all(p.status == "known" for p in self.points)

    @property
    def view_ref(self) -> str:
        return "inventory-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    @property
    def as_of(self) -> UtcTime:
        return (
            min(p.snapshot.snapshot_at for p in self.points if p.snapshot is not None)
            if self.complete
            else self.request.as_of
        )

    def inventory_items(self) -> list[InventoryItem]:
        if not self.complete:
            return []
        items = []
        for point in self.points:
            route, snapshot = point.route, point.snapshot
            if (
                route is None
                or snapshot is None
                or snapshot.on_hand is None
                or snapshot.reserved_qty is None
                or snapshot.available_qty is None
            ):
                raise ValueError("native_inventory_complete_point_missing")
            items.append(
                InventoryItem(
                    product_id=point.product_id,
                    selling_location_id=point.selling_location_id,
                    channel=point.channel,
                    stock_location_id=route.stock_location_id,
                    mapping_ref=route.mapping_ref,
                    physical_units=float(snapshot.on_hand),
                    reserved_units=float(snapshot.reserved_qty),
                    available_units=float(snapshot.available_qty),
                    unit_of_measure="unit",
                )
            )
        return items


class InventoryResult(DataResult[InventoryItem]):
    tool: Literal["get_inventory_status"]
    native_view: NativeInventoryEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_outcome(self) -> Self:
        evidence = self.native_view
        if evidence is not None and (
            self.source_kind != "runtime"
            or self.as_of != evidence.as_of
            or self.source_ref != evidence.view_ref
            or self.items != evidence.inventory_items()
            or self.status != ("ok" if evidence.complete else "no_data")
            or self.freshness_status != ("current" if evidence.complete else "missing")
            or self.error is not None
        ):
            raise ValueError("native_inventory_result_binding_mismatch")
        return self


class NativeRiskItem(SellingKey):
    window: DateWindow
    mapping_ref: SourceRef
    risk: StockoutRisk


class NativeRiskEvidence(Contract):
    evidence_version: Literal["native-stockout-read-v1"] = "native-stockout-read-v1"
    environment: Literal["local", "test"]
    request: RiskRequest
    inventory: NativeInventoryEvidence
    page: StockoutRiskPage | None

    @model_validator(mode="after")
    def binding(self) -> Self:
        scope, page = self.request.scope, self.page
        if (
            scope is None
            or self.inventory.environment != self.environment
            or self.inventory.request.scope != scope
            or self.inventory.request.as_of != self.request.as_of
            or self.inventory.request.limit != self.request.limit
            or self.request.window.start != self.request.as_of.date() + timedelta(days=1)
            or self.request.window.end != self.request.as_of.date() + timedelta(days=7)
        ):
            raise ValueError("native_stockout_request_or_mapping_mismatch")
        routes = [p.route for p in self.inventory.points]
        expected = {
            (p.product_id, p.route.stock_location_id)
            for p in self.inventory.points
            if p.route is not None
        }
        if page is None:
            if all(r is not None for r in routes):
                raise ValueError("native_stockout_page_required")
            return self
        if any(r is None for r in routes):
            raise ValueError("native_stockout_page_without_complete_mapping")
        actual = [(r.product_id, r.stock_location_id) for r in page.items]
        if (
            page.selection != "origin"
            or page.pagination.offset != 0
            or page.pagination.next_offset is not None
            or page.pagination.total != len(page.items)
            or page.pagination.limit != self.request.limit
            or len(actual) != len(set(actual))
            or not set(actual) <= expected
            or (page.data_status == "available") != bool(page.items)
            or any(
                r.as_of != self.request.as_of
                or r.model_name != "retailops-stockout-risk"
                or r.quality_status != "passed_at_publication"
                or r.read_at != page.generated_at
                or r.lineage.source_dataset_id != self.inventory.source_dataset_id
                or r.lineage.curated_dataset_id != self.inventory.curated_dataset_id
                or (r.lineage.source_watermark is not None and r.lineage.source_watermark > r.as_of)
                for r in page.items
            )
        ):
            raise ValueError("native_stockout_page_scope_lineage_or_namespace_mismatch")
        return self

    @property
    def complete(self) -> bool:
        return (
            self.page is not None
            and len(self.page.items)
            == len(
                {
                    (p.product_id, p.route.stock_location_id)
                    for p in self.inventory.points
                    if p.route
                }
            )
            and all(r.freshness_status == "current" for r in self.page.items)
        )

    @property
    def view_ref(self) -> str:
        return "stockout-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    def result_items(self) -> list[NativeRiskItem]:
        if not self.complete or self.page is None:
            return []
        rows = {(r.product_id, r.stock_location_id): r for r in self.page.items}
        return [
            NativeRiskItem(
                product_id=p.product_id,
                selling_location_id=p.selling_location_id,
                channel=p.channel,
                window=self.request.window,
                mapping_ref=p.route.mapping_ref,
                risk=rows[(p.product_id, p.route.stock_location_id)],
            )
            for p in self.inventory.points
            if p.route is not None
        ]


class NativeAnomalyItem(SellingKey):
    window: DateWindow
    decisions: tuple[NativeDetection, ...] = Field(min_length=1, max_length=50)


class NativeAnomalyEvidence(Contract):
    evidence_version: Literal["native-anomaly-read-v1"] = "native-anomaly-read-v1"
    environment: Literal["local", "test"]
    request: AnomalyRequest
    page: AnomalyPage

    @model_validator(mode="after")
    def binding(self) -> Self:
        scope, page = self.request.scope, self.page
        if scope is None:
            raise ValueError("native_anomaly_scope_required")
        n = (self.request.window.end - self.request.window.start).days + 1
        expected = {
            (p, s, scope.channel, self.request.window.start + timedelta(days=i))
            for p in scope.product_ids
            for s in scope.selling_location_ids
            for i in range(n)
        }
        actual = [
            (r.product_id, r.selling_location_id, r.channel, r.business_date) for r in page.items
        ]
        if (
            len(expected) > self.request.limit
            or page.selection != "latest_complete_batch"
            or page.pagination.offset != 0
            or page.pagination.total != len(page.items)
            or page.pagination.limit != self.request.limit
            or len(actual) != len(set(actual))
            or not set(actual) <= expected
            or (page.data_status == "available") != bool(page.items)
            or len({r.batch_id for r in page.items}) > 1
            or len({r.release_id for r in page.items}) > 1
            or page.generated_at < self.request.as_of
            or any(
                r.event_type != "sale_completed"
                or r.role != "batch"
                or r.as_of > self.request.as_of
                or r.generated_at > page.generated_at
                or r.detector_name != "retailops-sales-anomaly"
                or r.freshness_status
                != (
                    "unknown"
                    if r.status == "insufficient_data"
                    else "stale"
                    if (page.generated_at - r.scoring_origin).total_seconds() > 7 * 86400
                    else "current"
                )
                for r in page.items
            )
        ):
            raise ValueError("native_anomaly_page_scope_time_or_budget_mismatch")
        currencies: dict[tuple[str, str, str], set[str]] = {}
        for r in page.items:
            currencies.setdefault((r.product_id, r.selling_location_id, r.channel), set()).add(
                r.currency
            )
        if any(len(v) != 1 for v in currencies.values()):
            raise ValueError("native_anomaly_currency_changed")
        return self

    @property
    def complete(self) -> bool:
        scope = self.request.scope
        return (
            scope is not None
            and len(self.page.items)
            == len(scope.product_ids)
            * len(scope.selling_location_ids)
            * ((self.request.window.end - self.request.window.start).days + 1)
            and all(
                r.status == "scored" and r.freshness_status == "current" for r in self.page.items
            )
        )

    @property
    def view_ref(self) -> str:
        return "anomaly-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    def result_items(self) -> list[NativeAnomalyItem]:
        if not self.complete or self.request.scope is None:
            return []
        return [
            NativeAnomalyItem(
                product_id=p,
                selling_location_id=s,
                channel=self.request.scope.channel,
                window=self.request.window,
                decisions=tuple(
                    r for r in self.page.items if r.product_id == p and r.selling_location_id == s
                ),
            )
            for p in sorted(self.request.scope.product_ids)
            for s in sorted(self.request.scope.selling_location_ids)
        ]


class NativeOperationsItem(SellingKey):
    measurement_kind: Literal["scoped_persisted_event_processing"] = (
        "scoped_persisted_event_processing"
    )
    stream_status: Literal["not_observed"] = "not_observed"
    consumer_lag_seconds: None = None
    window_minutes: Literal[15] = 15
    received: Annotated[int, Field(ge=0)]
    processed: Annotated[int, Field(ge=0)]
    failed_dead_lettered: Annotated[int, Field(ge=0)]
    ignored_duplicate: Annotated[int, Field(ge=0)]
    newer_state_not_evaluable: Annotated[int, Field(ge=0)]
    latest_ingested_at: UtcTime | None
    latest_processed_at: UtcTime | None
    max_processing_latency_seconds: Annotated[float, Field(ge=0)] | None

    @property
    def event_count(self) -> int:
        return self.received + self.processed + self.failed_dead_lettered + self.ignored_duplicate

    @model_validator(mode="after")
    def counts(self) -> Self:
        if (
            (self.latest_ingested_at is None) != (self.event_count == 0)
            or (self.latest_processed_at is None) != (self.processed == 0)
            or (self.max_processing_latency_seconds is None) != (self.processed == 0)
        ):
            raise ValueError("native_operations_unknown_or_count_mismatch")
        return self


class NativeOperationsEvidence(Contract):
    evidence_version: Literal["native-operations-read-v1"] = "native-operations-read-v1"
    environment: Literal["local", "test"]
    request: OperationsRequest
    observed_at: UtcTime
    source_view_sha256: Sha256
    points: tuple[NativeOperationsItem, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def binding(self) -> Self:
        scope = self.request.scope
        if scope is None:
            raise ValueError("native_operations_scope_required")
        expected = {
            (p, s, scope.channel) for p in scope.product_ids for s in scope.selling_location_ids
        }
        actual = [(r.product_id, r.selling_location_id, r.channel) for r in self.points]
        if (
            len(expected) > self.request.limit
            or len(set(actual)) != len(actual)
            or set(actual) != expected
        ):
            raise ValueError("native_operations_partial_duplicate_or_extra_scope")
        if not 0 <= (self.observed_at - self.request.as_of).total_seconds() <= 300 or any(
            t is not None
            and not self.request.as_of - timedelta(minutes=15) <= t <= self.request.as_of
            for p in self.points
            for t in (p.latest_ingested_at, p.latest_processed_at)
        ):
            raise ValueError("native_operations_noncausal_or_historical_observation")
        return self

    @property
    def complete(self) -> bool:
        return all(p.event_count > 0 and p.newer_state_not_evaluable == 0 for p in self.points)

    @property
    def view_ref(self) -> str:
        return "operations-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    def result_items(self) -> list[NativeOperationsItem]:
        return list(self.points) if self.complete else []


class NativeModelStatusItem(Contract):
    record_kind: Literal["scoped_catalog_metadata"] = "scoped_catalog_metadata"
    record: V12CatalogModel | CatalogModel


class NativeModelStatusEvidence(Contract):
    evidence_version: Literal["native-model-status-read-v1"] = "native-model-status-read-v1"
    environment: Literal["local", "test"]
    request: ModelStatusRequest
    forecast: V12ModelPage
    anomaly: ModelPage
    covered_models: tuple[
        Literal["retailops-demand-forecast-v12", "retailops-sales-anomaly"], ...
    ] = (
        "retailops-demand-forecast-v12",
        "retailops-sales-anomaly",
    )

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.request.scope is None or self.covered_models != (
            "retailops-demand-forecast-v12",
            "retailops-sales-anomaly",
        ):
            raise ValueError("native_model_status_scope_required")
        for page, name in (
            (self.forecast, "retailops-demand-forecast-v12"),
            (self.anomaly, "retailops-sales-anomaly"),
        ):
            if (
                page.pagination.offset != 0
                or page.pagination.next_offset is not None
                or page.pagination.total != len(page.items)
                or len(page.items) > 1
                or page.pagination.limit != self.request.limit
                or (page.data_status == "available") != bool(page.items)
                or not 0 <= (page.generated_at - self.request.as_of).total_seconds() <= 300
                or any(
                    r.model_name != name
                    or r.generated_at > page.generated_at
                    or r.freshness.evaluated_at > page.generated_at
                    for r in page.items
                )
            ):
                raise ValueError("native_model_status_page_or_namespace_mismatch")
        return self

    @property
    def complete(self) -> bool:
        return bool(self.forecast.items) and bool(self.anomaly.items)

    @property
    def observed_at(self) -> UtcTime:
        return max(self.forecast.generated_at, self.anomaly.generated_at)

    @property
    def view_ref(self) -> str:
        return "model-status-view-sha256-" + canonical_sha256(self.model_dump(mode="json"))

    def result_items(self) -> list[NativeModelStatusItem]:
        return (
            (
                [NativeModelStatusItem(record=r) for r in self.forecast.items]
                + [NativeModelStatusItem(record=r) for r in self.anomaly.items]
            )
            if self.complete
            else []
        )


def catalog_scope_verified(evidence: NativeModelStatusEvidence, principal_id: str) -> bool:
    scope = evidence.request.scope
    if scope is None:
        return False
    forecast = canonical_sha256(
        dict(
            projection="v12-catalog-v1",
            model="retailops-demand-forecast-v12",
            models=True,
            principal_id=principal_id,
            scope=dict(
                products=sorted(scope.product_ids),
                locations=sorted(scope.selling_location_ids),
                channels=[scope.channel],
            ),
            items=[
                r.model_dump(mode="json", exclude={"freshness", "generated_at"})
                for r in evidence.forecast.items
            ],
        )
    )
    anomaly = canonical_sha256(
        dict(
            principal=principal_id,
            scope=dict(product_id=None, selling_location_id=None, channel=scope.channel),
            items=[
                r.model_dump(mode="json", exclude={"freshness", "generated_at"})
                for r in evidence.anomaly.items
            ],
        )
    )
    return evidence.forecast.view_sha256 == forecast and evidence.anomaly.view_sha256 == anomaly


def native_result_matches(
    output: DataResult[Any],
    evidence: NativeRiskEvidence
    | NativeAnomalyEvidence
    | NativeOperationsEvidence
    | NativeModelStatusEvidence,
) -> bool:
    return (
        output.source_kind == "runtime"
        and output.as_of
        == (
            evidence.observed_at
            if isinstance(evidence, NativeModelStatusEvidence)
            else evidence.request.as_of
        )
        and output.source_ref == evidence.view_ref
        and output.items == evidence.result_items()
        and output.status == ("ok" if evidence.complete else "no_data")
        and output.freshness_status == ("current" if evidence.complete else "missing")
        and output.error is None
    )


class RiskResult(DataResult[RiskItem | NativeRiskItem]):
    tool: Literal["get_stockout_risk"]
    native_view: NativeRiskEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_outcome(self) -> Self:
        if self.native_view is not None and not native_result_matches(self, self.native_view):
            raise ValueError("native_stockout_result_binding_mismatch")
        if any(isinstance(r, NativeRiskItem) for r in self.items) and self.native_view is None:
            raise ValueError("native_stockout_evidence_required")
        return self


class AnomalyResult(DataResult[AnomalyItem | NativeAnomalyItem]):
    tool: Literal["get_detected_anomalies"]
    native_view: NativeAnomalyEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_outcome(self) -> Self:
        if self.native_view is not None and not native_result_matches(self, self.native_view):
            raise ValueError("native_anomaly_result_binding_mismatch")
        if any(isinstance(r, NativeAnomalyItem) for r in self.items) and self.native_view is None:
            raise ValueError("native_anomaly_evidence_required")
        return self


class OperationsResult(DataResult[OperationsItem | NativeOperationsItem]):
    tool: Literal["get_live_operations"]
    native_view: NativeOperationsEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_outcome(self) -> Self:
        if self.native_view is not None and not native_result_matches(self, self.native_view):
            raise ValueError("native_operations_result_binding_mismatch")
        if (
            any(isinstance(r, NativeOperationsItem) for r in self.items)
            and self.native_view is None
        ):
            raise ValueError("native_operations_evidence_required")
        return self


class ModelStatusResult(DataResult[ModelStatusItem | NativeModelStatusItem]):
    tool: Literal["get_model_status"]
    native_view: NativeModelStatusEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_outcome(self) -> Self:
        if self.native_view is not None and not native_result_matches(self, self.native_view):
            raise ValueError("native_model_status_result_binding_mismatch")
        if (
            any(isinstance(r, NativeModelStatusItem) for r in self.items)
            and self.native_view is None
        ):
            raise ValueError("native_model_status_evidence_required")
        return self


class ForecastResult(Versioned):
    contract_type: Literal["agent_tool_result"]
    tool: Literal["get_demand_forecast"]
    result: Annotated[ToolResult | NativeForecastRead, Field(discriminator="contract_type")]
    source_kind: Literal["fixture", "runtime"]


class KnowledgeResult(Versioned):
    contract_type: Literal["agent_tool_result"]
    tool: Literal["search_knowledge"]
    status: Literal["ok", "no_data"]
    index_id: IndexID
    pin_generation: Annotated[int, Field(ge=1)]
    provider: Literal["bedrock", "fake"]
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
        if self.provider == "fake" and self.source_kind != "fixture":
            raise ValueError("fake_knowledge_requires_fixture_source")
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
