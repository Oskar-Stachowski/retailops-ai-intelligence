"""Bounded native reader adapter with server-owned identity and exact requested scope."""

import asyncio
from dataclasses import replace
from typing import Literal, Protocol

from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.native_forecast import NativeForecastRead
from retailops_ai.agent.tools import ForecastResult, ToolInput
from retailops_ai.data_contracts.tool import ToolRequest
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.v12_reader import V12ForecastReader
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL


class NativeForecastReader(V12ForecastReader, Protocol):
    environment: Literal["local", "test"]
    model: str


class NativeForecastTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, reader: NativeForecastReader, environment: Literal["local", "test"]) -> None:
        if (
            environment not in {"local", "test"}
            or reader.environment != environment
            or reader.model != MODEL
        ):
            raise ValueError("native_forecast_environment_invalid")
        self.reader, self.environment = reader, environment

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> ForecastResult:
        if not isinstance(request, ToolRequest):
            raise ToolFailure("invalid_scope")
        if self.reader.environment != self.environment or self.reader.model != MODEL:
            raise ToolFailure("unavailable")
        request = ToolRequest.model_validate_json(request.model_dump_json())
        scope = request.scope
        if not (
            "operator" in principal.roles
            and {"assistant:query", "forecast:read"} <= principal.capabilities
            and set(scope.product_ids) <= principal.product_ids
            and set(scope.selling_location_ids) <= principal.selling_location_ids
            and scope.channel in principal.channels
        ):
            raise ToolFailure("unauthorized")
        if (
            len(scope.product_ids)
            * len(scope.selling_location_ids)
            * ((scope.target_to - scope.target_from).days + 1)
            > request.limit
        ):
            raise ToolFailure("budget_exceeded")
        actor = replace(
            principal,
            product_ids=frozenset(scope.product_ids),
            selling_location_ids=frozenset(scope.selling_location_ids),
            channels=frozenset({scope.channel}),
        )
        query = ForecastQuery(
            channel=scope.channel,
            target_from=scope.target_from,
            target_to=scope.target_to,
            as_of=request.as_of,
            limit=request.limit,
        )
        try:
            page = await asyncio.to_thread(self.reader.read, query, actor)
            result = NativeForecastRead(
                schema_version="1.0", request=request, environment=self.environment, page=page
            )
        except ForecastReadError as exc:
            code = {
                "forecast-read-denied": "unauthorized",
                "forecast-scope-invalid": "invalid_scope",
                "forecast-scope-limit": "budget_exceeded",
                "forecast-read-budget": "budget_exceeded",
                "forecast-output-not-found": "not_found",
            }.get(exc.code, "unavailable")
            raise ToolFailure(code) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
        return ForecastResult(
            schema_version="1.0",
            contract_type="agent_tool_result",
            tool="get_demand_forecast",
            result=result,
            source_kind="runtime",
        )
