"""Bounded physical risk views; read freshness is distinct from inventory at origin."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, RunID, Sha256, Symbol, UtcTime
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.stockout_jobs.public_contracts import ProfileID, StockoutJobRun
from retailops_ai.stockout_runtime.public_contracts import RiskItem

RiskID = Annotated[str, Field(pattern=r"^risk-sha256-[0-9a-f]{64}$")]


class StockoutAttempts(Contract):
    version: Literal["stockout-job-attempts-1.0.0"] = "stockout-job-attempts-1.0.0"
    run_id: RunID
    items: tuple[StockoutJobRun, ...] = Field(max_length=5)
    generated_at: UtcTime

    @model_validator(mode="after")
    def complete_history(self) -> Self:
        if any(
            r.run_id != self.run_id or r.status in {"running", "queued"} for r in self.items
        ) or [r.attempt for r in self.items] != sorted({r.attempt for r in self.items}):
            raise ValueError("stockout_attempt_history")
        return self


class StockoutQuery(Contract):
    product_id: Symbol | None = None
    stock_location_id: Symbol | None = None
    as_of: UtcTime | None = None
    inference_run_id: RunID | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=3200)] = 0
    view_sha256: Sha256 | None = None
    view: Literal["all_risks", "attention_queue", "current_stockouts"] = "all_risks"

    @model_validator(mode="after")
    def pagination(self) -> Self:
        if self.offset and self.view_sha256 is None:
            raise ValueError("stockout_pagination_requires_frozen_view")
        if self.view == "attention_queue" and self.inference_run_id is None:
            raise ValueError("stockout_attention_requires_one_complete_inference_run")
        return self


class StockoutPriority(Contract):
    version: Literal["stockout-origin-priority-1.0.0"] = "stockout-origin-priority-1.0.0"
    universe_id: ProfileID
    universe: Literal["complete_registered_profile_at_origin"] = (
        "complete_registered_profile_at_origin"
    )
    selected_at_origin: bool | None
    rank_at_origin: Annotated[int, Field(ge=1, le=100)] | None
    eligible_in_universe: Annotated[int, Field(ge=0, le=100)] | None
    capacity_slots: Annotated[int, Field(ge=0, le=100)] | None
    other_scope_context: Literal["visible_authorized", "withheld"]

    @model_validator(mode="after")
    def context(self) -> Self:
        if (
            (self.eligible_in_universe is None) != (self.capacity_slots is None)
            or (self.other_scope_context == "withheld") != (self.eligible_in_universe is None)
            or (self.other_scope_context == "withheld" and self.rank_at_origin is not None)
            or (self.selected_at_origin is None and self.rank_at_origin is not None)
            or (
                self.eligible_in_universe is not None
                and self.capacity_slots is not None
                and self.capacity_slots > self.eligible_in_universe
            )
            or (
                self.rank_at_origin is not None
                and self.eligible_in_universe is not None
                and self.rank_at_origin > self.eligible_in_universe
            )
        ):
            raise ValueError("stockout_priority_scope_or_rank_context")
        return self


class StockoutRisk(RiskItem):
    read_at: UtcTime
    origin_age_seconds: Annotated[float, Field(ge=0)]
    output_age_seconds: Annotated[float, Field(ge=0)]
    freshness_reason: Literal[
        "source_watermark_unavailable",
        "origin_age_exceeded",
        "within_policy",
        "newer_run_unpublished",
    ]
    max_origin_age_seconds: Literal[86400] = 86400
    priority: StockoutPriority | None = None

    @model_validator(mode="after")
    def freshness(self) -> Self:
        expected = (
            "unknown"
            if self.freshness_reason == "source_watermark_unavailable"
            else "current"
            if self.freshness_reason == "within_policy"
            else "stale"
        )
        if (
            self.read_at < self.generated_at
            or self.freshness_status != expected
            or self.origin_age_seconds != (self.read_at - self.as_of).total_seconds()
            or self.output_age_seconds != (self.read_at - self.generated_at).total_seconds()
        ):
            raise ValueError("stockout_read_freshness")
        return self


class StockoutRiskPage(Contract):
    version: Literal["stockout-risk-page-1.0.0"] = "stockout-risk-page-1.0.0"
    items: tuple[StockoutRisk, ...] = Field(max_length=100)
    pagination: Pagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    selection: Literal["latest_per_product_stock", "origin", "inference_run"]
    view_sha256: Sha256
