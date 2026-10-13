"""Read scoped enrollment and release metadata without claiming live deployment."""

import asyncio
from typing import Literal, Protocol

from sqlalchemy import Engine

from retailops_ai.adapters.native_read_tools import environment, scoped_actor
from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    ModelStatusRequest,
    ModelStatusResult,
    NativeModelStatusEvidence,
    ToolInput,
    catalog_scope_verified,
)
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.model_lifecycle.anomaly_catalog import PostgresAnomalyCatalog
from retailops_ai.model_lifecycle.read_contracts import CatalogQuery, ModelPage
from retailops_ai.model_lifecycle.reader import CatalogError
from retailops_ai.model_lifecycle.v12_catalog import V12ModelCatalog
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import MODEL
from retailops_ai.model_lifecycle.v12_metadata_contracts import V12ModelPage


class NativeForecastCatalog(V12ModelCatalog, Protocol):
    environment: Literal["local", "test"]
    name: str


class NativeAnomalyCatalog(Protocol):
    environment: Literal["local", "test"]

    def models(self, query: CatalogQuery, actor: Principal) -> ModelPage: ...


class EnvironmentAnomalyCatalog(PostgresAnomalyCatalog):
    def __init__(self, engine: Engine, env: Literal["local", "test"]) -> None:
        super().__init__(engine)
        self.environment = environment(env)


class NativeModelStatusTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(
        self,
        forecast: NativeForecastCatalog,
        anomaly: NativeAnomalyCatalog,
        env: Literal["local", "test"],
    ) -> None:
        self.environment = environment(env)
        if forecast.environment != env or anomaly.environment != env or forecast.name != MODEL:
            raise ValueError("native_model_status_environment_or_namespace_invalid")
        self.forecast, self.anomaly = forecast, anomaly

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> ModelStatusResult:
        if not isinstance(request, ModelStatusRequest):
            raise ToolFailure("invalid_scope")
        request = ModelStatusRequest.model_validate_json(request.model_dump_json())
        actor = scoped_actor(request, principal, {"model:read", "forecast:read", "anomaly:read"})
        if request.limit < 2:
            raise ToolFailure("budget_exceeded")
        if (
            self.forecast.environment != self.environment
            or self.anomaly.environment != self.environment
            or self.forecast.name != MODEL
        ):
            raise ToolFailure("unavailable")
        query = CatalogQuery(
            channel=request.scope.channel if request.scope else None, limit=request.limit
        )

        def read() -> NativeModelStatusEvidence:
            # Both native catalogs receive the same narrowed verified identity.
            # A summary means a visible publication in this scope, not coverage
            # of every product/location or a deployment observation.
            forecast = V12ModelPage.model_validate_json(
                self.forecast.models(query, actor).model_dump_json()
            )
            anomaly = ModelPage.model_validate_json(
                self.anomaly.models(query, actor).model_dump_json()
            )
            return NativeModelStatusEvidence(
                environment=self.environment, request=request, forecast=forecast, anomaly=anomaly
            )

        try:
            proof = await asyncio.to_thread(read)
            if not catalog_scope_verified(proof, actor.principal_id):
                raise ToolFailure("unavailable")
            return ModelStatusResult(
                schema_version="1.0",
                contract_type="agent_tool_result",
                tool=request.tool,
                status="ok" if proof.complete else "no_data",
                as_of=proof.observed_at,
                freshness_status="current" if proof.complete else "missing",
                source_ref=proof.view_ref,
                items=[*proof.result_items()],
                error=None,
                source_kind="runtime",
                native_view=proof,
            )
        except CatalogError as exc:
            raise ToolFailure(
                "unauthorized"
                if exc.status == 403
                else "budget_exceeded"
                if exc.status == 429
                else "unavailable"
            ) from None
        except ToolFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
