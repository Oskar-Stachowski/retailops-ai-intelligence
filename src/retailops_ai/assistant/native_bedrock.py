"""Opt-in native reads and qualified semantic retrieval with lazy, bounded Bedrock."""

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator
from sqlalchemy import Engine, text

from retailops_ai.adapters.agent_tools import PinnedKnowledgeTool
from retailops_ai.adapters.bedrock_embeddings import BedrockEmbeddingProvider
from retailops_ai.adapters.index_lifecycle import current_index
from retailops_ai.adapters.knowledge_search import PostgresKnowledge
from retailops_ai.adapters.native_anomaly_tool import PostgresNativeAnomalyReader
from retailops_ai.adapters.native_assistant_tools import native_assistant_tools
from retailops_ai.adapters.native_inventory_tool import NativeInventoryReader
from retailops_ai.adapters.native_model_status_tool import EnvironmentAnomalyCatalog
from retailops_ai.adapters.native_operations_tool import PostgresNativeOperationsReader
from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesReader
from retailops_ai.adapters.vector_store import _boundary
from retailops_ai.agent.chat import ChatProvider, ChatRequest, SmokeBudget
from retailops_ai.agent.chat_contracts import ProviderReply
from retailops_ai.agent.evaluation import evaluator_checksum
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolAdapter, ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import resolve_graph_config
from retailops_ai.agent.graph_contracts import GraphRequest, GraphResult
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.agent.tools import ToolName
from retailops_ai.assistant.routes import QuestionRoutes, reviewed_backend
from retailops_ai.assistant.runtime import (
    DocumentRuntimeConfig,
    LazyBedrock,
    document_model_request,
)
from retailops_ai.assistant.service import AssistantError, GraphAssistant
from retailops_ai.assistant.source_catalog import SourceCatalog, load_source_catalog
from retailops_ai.forecast_jobs.v12_reader import PostgresV12ForecastReader
from retailops_ai.knowledge.indexes import EmbeddingConfig
from retailops_ai.model_lifecycle.v12_catalog import PostgresV12Catalog
from retailops_ai.security.local import LocalAccess, strict_json
from retailops_ai.stockout_jobs.reader import PostgresStockoutReader


class NativeRuntimeConfig(DocumentRuntimeConfig):
    runtime_version: Literal["assistant-native-bedrock-v1"]  # type: ignore[assignment]
    curated_dataset_id: Annotated[str, Field(pattern=r"^curated-sha256-[0-9a-f]{64}$")]
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    day_coverage_id: Annotated[str, Field(pattern=r"^day-coverage-sha256-[0-9a-f]{64}$")]
    routes: QuestionRoutes
    embedding_input_per_million_usd: Decimal = Field(gt=0, le=1)
    embedding_max_cost_usd: Decimal = Field(gt=0, le=1)

    @model_validator(mode="after")
    def native_binding(self) -> Self:
        if (
            self.routes.graph_config_id != self.graph.config_id()
            or self.routes.labels_state != "accepted"
            or self.graph.chat.knowledge_mode != "semantic_retrieval"
            or self.graph.policy.suggestions.policy_version != "read-only-review-v1"
            or Decimal(self.embedding_max_input_bytes)
            * self.embedding_input_per_million_usd
            / Decimal(1_000_000)
            > self.embedding_max_cost_usd
        ):
            raise ValueError("native_runtime_routes_policy_or_budget_invalid")
        return self


def load_native_runtime(path: Path) -> NativeRuntimeConfig:
    with path.open("rb") as source:
        raw = source.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("native_runtime_file_too_large")
    strict_json(raw)
    config = NativeRuntimeConfig.model_validate_json(raw)
    if config.code_sha256 != evaluator_checksum():
        raise ValueError("native_runtime_code_mismatch")
    resolve_graph_config(config.graph)
    return config


def model_request(request: ChatRequest) -> ChatRequest:
    # The graph's existing validation retains the complete evidence. Only
    # document requests use the previously qualified compact wire projection.
    import json

    intent = json.loads(request.references_json)["server_evidence_policy"]["request"]["intent"]
    return (
        document_model_request(request)
        if intent in {"documentation", "verified_state"}
        else request
    )


class NativeLazyBedrock(LazyBedrock):
    def input_token_bound(self, request: ChatRequest) -> int:
        from retailops_ai.agent.chat import ProviderFailure

        if self.provider is None:
            raise ProviderFailure("auth")
        return self.provider.input_token_bound(model_request(request))

    async def count_input_tokens(self, request: ChatRequest) -> int:
        return await (await self.ready()).count_input_tokens(model_request(request))

    async def generate(self, request: ChatRequest) -> ProviderReply:
        return await (await self.ready()).generate(model_request(request))


class NativeBedrockAssistant(GraphAssistant):
    def __init__(
        self,
        config: NativeRuntimeConfig,
        catalog: SourceCatalog,
        sales: QualifiedSalesReader,
        inventory: NativeInventoryReader,
        engine: Engine,
        producer_engine: Engine,
        authority: LocalAccess,
    ) -> None:
        config = NativeRuntimeConfig.model_validate_json(config.model_dump_json())
        env = config.pin.environment
        if (
            config.code_sha256 != evaluator_checksum()
            or config.source_dataset_id != catalog.source_dataset_id
            or config.snapshot_id != catalog.snapshot_id
            or config.source_catalog_sha256 != catalog.checksum()
            or sales.environment != env
            or inventory.environment != env
            or sales.source_dataset_id != config.source_dataset_id
            or inventory.source_dataset_id != config.source_dataset_id
            or sales.curated_dataset_id != config.curated_dataset_id
            or inventory.curated_dataset_id != config.curated_dataset_id
            or sales.full_dq_replay_id != config.full_dq_replay_id
            or sales.day_coverage_id != config.day_coverage_id
        ):
            raise ValueError("native_runtime_source_binding_invalid")
        self.runtime_config, self.engine, self.producer_engine = config, engine, producer_engine
        graph = resolve_graph_config(config.graph)
        self.chat_provider = NativeLazyBedrock(config)
        self.smoke = SmokeBudget(graph.config.chat.budget.pricing)
        self.embedding_provider: BedrockEmbeddingProvider | None = None

        def embeddings(embedding: EmbeddingConfig) -> BedrockEmbeddingProvider:
            self.embedding_provider = BedrockEmbeddingProvider(
                embedding,
                max_requests=config.embedding_max_requests,
                max_input_bytes=config.embedding_max_input_bytes,
            )
            return self.embedding_provider

        self.knowledge = PostgresKnowledge(
            engine, env, graph.config.chat.retrieval, provider_factory=embeddings
        )
        self.adapters = native_assistant_tools(
            environment=env,
            sales=sales,
            inventory=inventory,
            forecast=PostgresV12ForecastReader(engine, env),
            stockout=PostgresStockoutReader(engine, env),
            anomaly=PostgresNativeAnomalyReader(engine, env),
            operations=PostgresNativeOperationsReader(producer_engine, env),
            forecast_catalog=PostgresV12Catalog(engine, env),
            anomaly_catalog=EnvironmentAnomalyCatalog(engine, env),
            knowledge=cast(ToolAdapter, PinnedKnowledgeTool(self.knowledge)),
        )
        native = frozenset(self.adapters)
        bound = reviewed_backend(
            config.routes,
            graph,
            catalog,
            config.channel,
            frozenset(cast(ToolName, name) for name in READ_CAPABILITIES),
            env,
            "runtime",
            lambda: GraphRunner(
                ToolExecutor(authority, self.adapters, graph.config.chat.tool_policy, env),
                graph,
                cast(ChatProvider, self.chat_provider),
                MemoryTraces(graph.config.policy),
                pin=config.pin,
                smoke=self.smoke,
            ),
            native_tools=native,
        )
        super().__init__(
            bound.graph_config_version,
            bound.index_id,
            bound.deadline_seconds,
            bound.reserved_tokens,
            bound.reserved_cost,
            "runtime",
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
        if current_index(self.engine, self.runtime_config.pin.environment, "retrieval") != (
            self.runtime_config.pin
        ):
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


def native_bedrock_backend(
    config_path: Path,
    source: Path,
    curated: Path,
    replay: Path,
    coverage: Path,
    engine: Engine,
    producer_engine: Engine,
    authority: LocalAccess,
    environment: Literal["local", "test"],
) -> NativeBedrockAssistant:
    config = load_native_runtime(config_path)
    if config.pin.environment != environment:
        raise ValueError("native_runtime_environment_mismatch")
    return NativeBedrockAssistant(
        config,
        load_source_catalog(source),
        QualifiedSalesReader(replay, coverage, curated, source, environment),
        NativeInventoryReader(curated, source, environment),
        engine,
        producer_engine,
        authority,
    )
