"""Explicit test-only native read runtime with offline chat and fake-vector SQL retrieval."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator
from sqlalchemy import Engine, text

from retailops_ai.adapters.agent_tools import OfflineKnowledgeTool
from retailops_ai.adapters.index_lifecycle import current_index
from retailops_ai.adapters.knowledge_search import PostgresKnowledge
from retailops_ai.adapters.native_anomaly_tool import PostgresNativeAnomalyReader
from retailops_ai.adapters.native_assistant_tools import native_assistant_tools
from retailops_ai.adapters.native_inventory_tool import NativeInventoryReader
from retailops_ai.adapters.native_model_status_tool import EnvironmentAnomalyCatalog
from retailops_ai.adapters.native_operations_tool import PostgresNativeOperationsReader
from retailops_ai.adapters.offline_policy_chat import OfflinePolicyChat
from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesReader
from retailops_ai.adapters.vector_store import _boundary
from retailops_ai.agent.evaluation import evaluator_checksum
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import AgentGraphConfig, resolve_graph_config
from retailops_ai.agent.graph_contracts import GraphRequest, GraphResult
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.agent.tools import ToolName
from retailops_ai.assistant.routes import QuestionRoutes, reviewed_backend
from retailops_ai.assistant.service import AssistantError, GraphAssistant
from retailops_ai.assistant.source_catalog import (
    SnapshotID,
    SourceCatalog,
    SourceID,
    load_source_catalog,
)
from retailops_ai.data_contracts.common import Sha256, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.model_lifecycle.v12_catalog import PostgresV12Catalog
from retailops_ai.security.local import LocalAccess, strict_json
from retailops_ai.stockout_jobs.reader import PostgresStockoutReader


class NativeOfflineConfig(Versioned):
    runtime_version: Literal["assistant-native-offline-v1"]
    environment: Literal["test"]
    code_sha256: Sha256
    source_dataset_id: SourceID
    snapshot_id: SnapshotID
    source_catalog_sha256: Sha256
    curated_dataset_id: Annotated[str, Field(pattern=r"^curated-sha256-[0-9a-f]{64}$")]
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    day_coverage_id: Annotated[str, Field(pattern=r"^day-coverage-sha256-[0-9a-f]{64}$")]
    channel: Literal["store", "online"]
    graph: AgentGraphConfig
    routes: QuestionRoutes
    pin: IndexPin

    @model_validator(mode="after")
    def offline_binding(self) -> Self:
        if (
            self.graph.chat.model.provider != "fake"
            or self.graph.chat.knowledge_mode != "offline_test"
            or self.graph.chat.embeddings.provider != "fake"
            or self.pin.environment != "test"
            or self.pin.lane != "offline_test"
            or self.pin.manifest.embedding_config != self.graph.chat.embeddings
            or self.graph.chat.knowledge_index_id != self.pin.manifest.index_id
        ):
            raise ValueError("native_offline_provider_or_pin_invalid")
        return self

    def config_id(self) -> str:
        return "assistant-native-offline-sha256-" + canonical_sha256(self.model_dump(mode="json"))


def load_native_offline_config(path: Path) -> NativeOfflineConfig:
    with path.open("rb") as source:
        raw = source.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("native_offline_config_too_large")
    strict_json(raw)
    config = NativeOfflineConfig.model_validate_json(raw)
    if config.code_sha256 != evaluator_checksum():
        raise ValueError("native_offline_code_mismatch")
    resolve_graph_config(config.graph)
    return config


class NativeOfflineAssistant(GraphAssistant):
    def __init__(
        self,
        config: NativeOfflineConfig,
        catalog: SourceCatalog,
        sales: QualifiedSalesReader,
        inventory: NativeInventoryReader,
        engine: Engine,
        producer_engine: Engine,
        authority: LocalAccess,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        config = NativeOfflineConfig.model_validate_json(config.model_dump_json())
        if (
            config.code_sha256 != evaluator_checksum()
            or config.source_dataset_id != catalog.source_dataset_id
            or config.snapshot_id != catalog.snapshot_id
            or config.source_catalog_sha256 != catalog.checksum()
            or sales.environment != "test"
            or inventory.environment != "test"
            or sales.source_dataset_id != config.source_dataset_id
            or inventory.source_dataset_id != config.source_dataset_id
            or sales.curated_dataset_id != config.curated_dataset_id
            or inventory.curated_dataset_id != config.curated_dataset_id
            or sales.full_dq_replay_id != config.full_dq_replay_id
            or sales.day_coverage_id != config.day_coverage_id
        ):
            raise ValueError("native_offline_source_binding_invalid")
        self.runtime_config, self.engine, self.producer_engine = config, engine, producer_engine
        graph = resolve_graph_config(config.graph)
        self.knowledge = PostgresKnowledge(
            engine,
            "test",
            graph.config.chat.retrieval,
            allow_bedrock=False,
        )
        self.adapters = native_assistant_tools(
            environment="test",
            sales=sales,
            inventory=inventory,
            forecast=PostgresV12ForecastReader(engine, "test"),
            stockout=PostgresStockoutReader(engine, "test"),
            anomaly=PostgresNativeAnomalyReader(engine, "test"),
            operations=PostgresNativeOperationsReader(producer_engine, "test"),
            forecast_catalog=PostgresV12Catalog(engine, "test"),
            anomaly_catalog=EnvironmentAnomalyCatalog(engine, "test"),
            knowledge=OfflineKnowledgeTool(self.knowledge),
        )
        native = frozenset(
            k for k, adapter in self.adapters.items() if adapter.source_kind == "runtime"
        )
        bound = reviewed_backend(
            config.routes,
            graph,
            catalog,
            config.channel,
            frozenset(cast(ToolName, k) for k in READ_CAPABILITIES),
            "test",
            "fixture",
            lambda: GraphRunner(
                ToolExecutor(
                    authority,
                    self.adapters,
                    graph.config.chat.tool_policy,
                    "test",
                    allow_fixtures=True,
                    clock=clock,
                ),
                graph,
                OfflinePolicyChat(graph.config.chat.model),
                MemoryTraces(graph.config.policy),
                pin=config.pin,
            ),
            allow_proposed=True,
            clock=clock,
            native_tools=native,
        )
        super().__init__(
            bound.graph_config_version,
            bound.index_id,
            bound.deadline_seconds,
            bound.reserved_tokens,
            bound.reserved_cost,
            "fixture",
            bound.planner,
            bound.runner,
            runtime_version=config.config_id(),
            native_tools=native,
        )

    def dependencies_ready(self) -> bool:
        with self.engine.connect() as connection:
            _boundary(connection)
            if connection.scalar(text("SHOW TimeZone")) not in {"UTC", "Etc/UTC"}:
                return False
        if current_index(self.engine, "test", "offline_test") != self.runtime_config.pin:
            return False
        with self.producer_engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout='3s'"))
                if connection.scalar(text("SHOW TimeZone")) not in {"UTC", "Etc/UTC"}:
                    return False
                connection.execute(text("SELECT event_id FROM realtime_event_log LIMIT 0"))
        return True

    async def check(self) -> bool:
        try:
            return await asyncio.to_thread(self.dependencies_ready)
        except Exception:
            return False

    async def run(self, request: GraphRequest, authorization: str | None) -> GraphResult:
        if not await self.check():
            raise AssistantError(424)
        return await super().run(request, authorization)


def native_offline_backend(
    config_path: Path,
    source: Path,
    curated: Path,
    replay: Path,
    coverage: Path,
    engine: Engine,
    producer_engine: Engine,
    authority: LocalAccess,
    environment: Literal["local", "test"],
) -> NativeOfflineAssistant:
    if environment != "test":
        raise ValueError("native_offline_requires_test_environment")
    config = load_native_offline_config(config_path)
    catalog = load_source_catalog(source)
    sales = QualifiedSalesReader(replay, coverage, curated, source, "test")
    inventory = NativeInventoryReader(curated, source, "test")
    return NativeOfflineAssistant(
        config, catalog, sales, inventory, engine, producer_engine, authority
    )
