"""Generated from pinned source OpenAPI; regenerate, do not hand edit."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class ReadModel(BaseModel):
    model_config = ConfigDict(
        extra="allow", frozen=True, strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    @model_validator(mode="after")
    def period(self) -> Self:
        for left, right in (
            ("sold_from", "sold_to"),
            ("recorded_from", "recorded_to"),
            ("date_from", "date_to"),
        ):
            start = getattr(self, left, None)
            end = getattr(self, right, None)
            if (
                start is not None
                and end is not None
                and (end < start or end - start > timedelta(days=90))
            ):
                raise ValueError("period_outside_90_day_bound")
        return self


Channel = Literal["online", "store", "marketplace", "wholesale"]

Currency = Literal["PLN", "EUR", "USD"]

ForecastMethod = Literal[
    "moving_average",
    "naive_baseline",
    "seeded_demo",
    "retailops-baseline-demand-model",
    "retailops-realism-baseline-demand-model",
    "retailops-random-forest-demand-model",
]

ForecastSortBy = Literal[
    "forecast_period_start",
    "forecast_period_end",
    "generated_at",
    "predicted_quantity",
    "confidence_level",
]

ForecastStatus = Literal["generated", "evaluated", "deprecated"]

InventorySortBy = Literal[
    "recorded_at", "ingested_at", "created_at", "stock_quantity", "warehouse_code"
]

ProductSortBy = Literal["sku", "name", "category", "status", "created_at", "updated_at"]

ProductStatus = Literal["draft", "active", "discontinued"]

RiskStatus = Literal["normal", "stockout_risk", "overstock_risk", "unknown"]

SaleSortBy = Literal["sold_at", "created_at", "quantity", "unit_price", "total_amount", "channel"]

SortOrder = Literal["asc", "desc"]

StockRiskSortBy = Literal[
    "risk_status", "sku", "current_stock", "forecast_quantity", "inventory_updated_at"
]

UnitOfMeasure = Literal["pcs", "kg", "l", "m", "m2"]


class ForecastResponse(ReadModel):
    confidence_level: float = Field(..., ge=0.0, le=1.0)
    forecast_period_end: date = Field(...)
    forecast_period_start: date = Field(...)
    generated_at: AwareDatetime = Field(...)
    id: UUID = Field(...)
    method: str = Field(...)
    predicted_quantity: float = Field(..., ge=0.0)
    product_category: str | None = Field(None)
    product_id: UUID = Field(...)
    product_name: str | None = Field(None)
    product_sku: str | None = Field(None)
    status: str = Field(...)
    unit_of_measure: str = Field(...)


class InventorySnapshotResponse(ReadModel):
    created_at: AwareDatetime = Field(...)
    id: UUID = Field(...)
    ingested_at: AwareDatetime = Field(...)
    product_id: UUID = Field(...)
    recorded_at: AwareDatetime = Field(...)
    stock_quantity: int = Field(..., ge=0.0)
    unit_of_measure: str = Field(...)
    warehouse_code: str = Field(...)


class PaginationMetadata(ReadModel):
    limit: int = Field(..., ge=1.0)
    offset: int = Field(..., ge=0.0)
    total: int = Field(..., ge=0.0)


class ProductResponse(ReadModel):
    brand: str | None = Field(None)
    category: str | None = Field(None)
    created_at: AwareDatetime = Field(...)
    id: UUID = Field(...)
    name: str = Field(...)
    sku: str = Field(...)
    status: str = Field(...)
    updated_at: AwareDatetime = Field(...)


class SaleResponse(ReadModel):
    channel: str | None = Field(None)
    created_at: AwareDatetime = Field(...)
    currency: str = Field(...)
    id: UUID = Field(...)
    product_id: UUID = Field(...)
    quantity: int = Field(..., gt=0.0)
    sold_at: AwareDatetime = Field(...)
    total_amount: float = Field(..., ge=0.0)
    unit_price: float = Field(..., ge=0.0)


class SourceCapabilities(ReadModel):
    contract_version: Literal["retailops-source-reads-2.0"] = Field("retailops-source-reads-2.0")
    freshness: Literal["unknown_without_completeness_watermark"] = Field(
        "unknown_without_completeness_watermark"
    )
    immutable_snapshot: Literal[False] = Field(False)
    legacy_forecast_semantics: Literal["product_period_quantity"] = Field("product_period_quantity")
    legacy_risk_semantics: Literal["product_heuristic_not_ml_probability"] = Field(
        "product_heuristic_not_ml_probability"
    )
    max_limit: Literal[100] = Field(100)
    max_period_days: Literal[90] = Field(90)
    missing_sales_fields: list[Literal["store_id", "order_id", "ingested_at", "record_version"]] = (
        Field(["store_id", "order_id", "ingested_at", "record_version"])
    )
    read_mode: Literal["bounded_live"] = Field("bounded_live")
    sales_full_ml_grain: Literal[False] = Field(False)
    snapshot_replay_handoff: Literal[False] = Field(False)
    warehouse_location_mapping: Literal["not_available"] = Field("not_available")


class StockRiskResponse(ReadModel):
    category: str | None = Field(None)
    current_stock: float | None = Field(None)
    forecast_quantity: float | None = Field(None)
    inventory_updated_at: AwareDatetime | None = Field(None)
    name: str | None = Field(None)
    product_id: UUID = Field(...)
    risk_status: str = Field(...)
    sku: str | None = Field(None)


class ForecastListResponse(ReadModel):
    items: list[ForecastResponse] = Field(...)
    pagination: PaginationMetadata = Field(...)


class InventorySnapshotListResponse(ReadModel):
    items: list[InventorySnapshotResponse] = Field(...)
    pagination: PaginationMetadata = Field(...)


class ProductListResponse(ReadModel):
    items: list[ProductResponse] = Field(...)
    pagination: PaginationMetadata = Field(...)


class SaleListResponse(ReadModel):
    items: list[SaleResponse] = Field(...)
    pagination: PaginationMetadata = Field(...)


class StockRiskListResponse(ReadModel):
    items: list[StockRiskResponse] = Field(...)
    pagination: PaginationMetadata = Field(...)


class ProductsQuery(QueryModel):
    product_id: UUID = Field(...)
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)
    sort_order: SortOrder = Field("asc")
    category: str | None = Field(None, max_length=120)
    status: ProductStatus | None = Field(None)
    search: str | None = Field(None, min_length=1, max_length=200)
    sort_by: ProductSortBy = Field("sku")


class SalesQuery(QueryModel):
    product_id: UUID = Field(...)
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)
    sort_order: SortOrder = Field("desc")
    channel: Channel = Field(...)
    currency: Currency | None = Field(None)
    sold_from: AwareDatetime = Field(...)
    sold_to: AwareDatetime = Field(...)
    sort_by: SaleSortBy = Field("sold_at")


class InventoryQuery(QueryModel):
    product_id: UUID = Field(...)
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)
    sort_order: SortOrder = Field("desc")
    warehouse_code: str = Field(..., min_length=1, max_length=20)
    unit_of_measure: UnitOfMeasure | None = Field(None)
    recorded_from: AwareDatetime = Field(...)
    recorded_to: AwareDatetime = Field(...)
    sort_by: InventorySortBy = Field("recorded_at")


class ForecastsQuery(QueryModel):
    product_id: UUID = Field(...)
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)
    sort_order: SortOrder = Field("asc")
    status: ForecastStatus | None = Field(None)
    method: ForecastMethod | None = Field(None)
    date_from: date = Field(...)
    date_to: date = Field(...)
    sort_by: ForecastSortBy = Field("forecast_period_start")


class RisksQuery(QueryModel):
    product_id: UUID = Field(...)
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0, le=10000)
    sort_order: SortOrder = Field("asc")
    risk_status: RiskStatus | None = Field(None)
    category: str | None = Field(None, min_length=1, max_length=120)
    sort_by: StockRiskSortBy = Field("risk_status")
