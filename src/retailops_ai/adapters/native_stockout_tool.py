"""Verified physical risks, projected through verified causal fulfillment routes."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Literal, Protocol

from retailops_ai.adapters.native_inventory_tool import InventoryReader
from retailops_ai.adapters.native_read_tools import environment, scoped_actor
from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    InventoryRequest,
    InventoryResult,
    NativeRiskEvidence,
    RiskRequest,
    RiskResult,
    ToolInput,
)
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.stockout_jobs.input_store import StockoutError
from retailops_ai.stockout_jobs.ports import StockoutReader
from retailops_ai.stockout_jobs.read_contracts import StockoutQuery, StockoutRiskPage
from retailops_ai.stockout_lifecycle.contract import MODEL


class NativeStockoutReader(StockoutReader, Protocol):
    environment: Literal["local", "test"]
    model: str


class NativeStockoutTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(
        self,
        reader: NativeStockoutReader,
        inventory: InventoryReader,
        env: Literal["local", "test"],
    ) -> None:
        self.environment = environment(env)
        if reader.environment != env or inventory.environment != env or reader.model != MODEL:
            raise ValueError("native_stockout_environment_or_namespace_invalid")
        self.reader, self.inventory = reader, inventory

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> RiskResult:
        if not isinstance(request, RiskRequest):
            raise ToolFailure("invalid_scope")
        request = RiskRequest.model_validate_json(request.model_dump_json())
        actor = scoped_actor(request, principal, {"stockout:read", "inventory:read"})
        if (
            self.reader.environment != self.environment
            or self.inventory.environment != self.environment
            or self.reader.model != MODEL
        ):
            raise ToolFailure("unavailable")
        if request.window.start != request.as_of.date() + timedelta(
            days=1
        ) or request.window.end != request.as_of.date() + timedelta(days=7):
            raise ToolFailure("invalid_scope")
        physical = actor.stockout
        if (
            physical is None
            or request.scope is None
            or not set(request.scope.product_ids) <= physical.product_ids
        ):
            raise ToolFailure("unauthorized")

        def read() -> NativeRiskEvidence:
            inv_request = InventoryRequest(
                schema_version="1.0",
                contract_type="tool_request",
                tool="get_inventory_status",
                scope=request.scope,
                as_of=request.as_of,
                limit=request.limit,
            )
            inventory = InventoryResult.model_validate_json(
                self.inventory.read(inv_request, actor).model_dump_json()
            )
            proof = inventory.native_view
            if (
                inventory.source_kind != "runtime"
                or proof is None
                or proof.environment != self.environment
                or proof.request != inv_request
            ):
                raise ToolFailure("unavailable")
            stocks = {p.route.stock_location_id for p in proof.points if p.route}
            if not stocks <= physical.stock_location_ids:
                raise ToolFailure("unauthorized")
            page = None
            if all(p.route is not None for p in proof.points):
                narrowed = replace(
                    actor,
                    stockout=replace(
                        physical,
                        product_ids=actor.product_ids,
                        stock_location_ids=frozenset(stocks),
                    ),
                )
                page = self.reader.list(
                    StockoutQuery(as_of=request.as_of, limit=request.limit), narrowed
                )
                page = StockoutRiskPage.model_validate_json(page.model_dump_json())
            return NativeRiskEvidence(
                environment=self.environment, request=request, inventory=proof, page=page
            )

        try:
            proof = await asyncio.to_thread(read)
            return RiskResult(
                schema_version="1.0",
                contract_type="agent_tool_result",
                tool=request.tool,
                status="ok" if proof.complete else "no_data",
                as_of=request.as_of,
                freshness_status="current" if proof.complete else "missing",
                source_ref=proof.view_ref,
                items=[*proof.result_items()],
                error=None,
                source_kind="runtime",
                native_view=proof,
            )
        except ToolFailure:
            raise
        except StockoutError as exc:
            raise ToolFailure(
                "unauthorized"
                if exc.status == 403
                else "budget_exceeded"
                if exc.status == 429
                else "unavailable"
            ) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
