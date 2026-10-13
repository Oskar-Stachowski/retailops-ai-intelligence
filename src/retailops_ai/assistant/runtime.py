"""Opt-in document Assistant using a reviewed graph, a verified source import and pinned RAG."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, Self, cast

from pydantic import Field, model_validator
from sqlalchemy import Engine

from retailops_ai.adapters.agent_tools import PinnedKnowledgeTool
from retailops_ai.adapters.bedrock_access import inspect_model_access
from retailops_ai.adapters.bedrock_chat import BedrockChatProvider, CircuitPolicy
from retailops_ai.adapters.bedrock_embeddings import BedrockEmbeddingProvider
from retailops_ai.adapters.index_lifecycle import current_index
from retailops_ai.adapters.knowledge_search import PostgresKnowledge
from retailops_ai.agent.chat import ChatProvider, ChatRequest, ProviderFailure, SmokeBudget
from retailops_ai.agent.chat_contracts import ProviderReply
from retailops_ai.agent.evaluation import evaluator_checksum
from retailops_ai.agent.execution import ToolAdapter, ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import AgentGraphConfig, resolve_graph_config
from retailops_ai.agent.graph_contracts import GraphRequest, GraphResult
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.assistant.planner import DocumentPlanner
from retailops_ai.assistant.service import AssistantError, GraphAssistant
from retailops_ai.assistant.source_catalog import (
    SnapshotID,
    SourceCatalog,
    SourceID,
    load_source_catalog,
)
from retailops_ai.data_contracts.common import Sha256, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import EmbeddingConfig
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.pipelines.retrieval import load_retrieval_config
from retailops_ai.security.local import LocalAccess, strict_json


class DocumentRuntimeConfig(Versioned):
    runtime_version: Literal["assistant-documents-v1"]
    code_sha256: Sha256
    source_dataset_id: SourceID
    snapshot_id: SnapshotID
    source_catalog_sha256: Sha256
    channel: Literal["store", "online"]
    graph: AgentGraphConfig
    pin: IndexPin
    circuit: CircuitPolicy
    embedding_max_requests: int = Field(ge=1, le=100)
    embedding_max_input_bytes: int = Field(ge=1, le=800000)

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.graph.chat.model.provider != "bedrock"
            or self.pin.lane != "retrieval"
            or self.pin.manifest.embedding_config.provider != "bedrock"
            or self.graph.chat.knowledge_index_id != self.pin.manifest.index_id
            or self.graph.chat.embeddings != self.pin.manifest.embedding_config
            or not self.graph.policy.document_rules
        ):
            raise ValueError("document_runtime_binding_invalid")
        entries = {row.chunk_id for row in self.pin.manifest.entries}
        if any(
            support.chunk_id not in entries
            for rule in self.graph.policy.document_rules
            for requirement in rule.requirements
            for support in requirement.supports
        ):
            raise ValueError("document_rule_outside_pin")
        return self

    def config_id(self) -> str:
        return "assistant-runtime-sha256-" + canonical_sha256(self.model_dump(mode="json"))


def load_document_runtime(path: Path) -> DocumentRuntimeConfig:
    with path.open("rb") as source:
        raw = source.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("document_runtime_file_too_large")
    strict_json(raw)
    config = DocumentRuntimeConfig.model_validate_json(raw)
    if config.code_sha256 != evaluator_checksum():
        raise ValueError("document_runtime_code_mismatch")
    resolve_graph_config(config.graph)
    return config


def document_model_request(request: ChatRequest) -> ChatRequest:
    """Send reviewed facts/citations; full retrieved chunks remain in server validation.

    Projection is the same before CountTokens and Converse. It does not select new
    facts, alter quotes, skip retrieval, or relax any evidence/authorization check.
    """
    references = json.loads(request.references_json)
    policy = references["server_evidence_policy"]
    if policy["request"]["intent"] not in {"documentation", "verified_state"}:
        raise ProviderFailure("schema")
    sources = {fact["evidence"]["source_ref"] for fact in policy["facts"]}
    projected = {
        "content_trust": references["content_trust"],
        "reference_projection": "question-bound-document-facts-v1",
        "citation_candidates": [
            row for row in references["citation_candidates"] if row["source_ref"] in sources
        ],
        "data_freshness": references["data_freshness"],
        "server_evidence_policy": policy,
    }
    return replace(
        request,
        references_json=json.dumps(
            projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
    )


class LazyBedrock:
    """Account/profile checks are single-flight and occur only after durable admission."""

    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, config: DocumentRuntimeConfig) -> None:
        self.config = config
        self.model = config.graph.chat.model
        self.provider: BedrockChatProvider | None = None
        self.pending: asyncio.Task[BedrockChatProvider] | None = None

    def create(self) -> BedrockChatProvider:
        chat = resolve_graph_config(self.config.graph).chat
        if inspect_model_access(chat)["status"] not in {"passed", "not_required"}:
            raise ProviderFailure("auth")
        return BedrockChatProvider(chat, self.config.circuit)

    async def ready(self) -> BedrockChatProvider:
        if self.provider is not None:
            return self.provider
        if self.pending is None:
            self.pending = asyncio.create_task(asyncio.to_thread(self.create))
        try:
            self.provider = await asyncio.shield(self.pending)
            return self.provider
        except asyncio.CancelledError:
            raise
        except Exception:
            # Retain the failed task: a broken initialization must not create an unbounded
            # sequence of background SDK checks. Correct configuration and restart explicitly.
            raise ProviderFailure("auth") from None

    def input_token_bound(self, request: ChatRequest) -> int:
        if self.provider is None:
            raise ProviderFailure("auth")
        return self.provider.input_token_bound(document_model_request(request))

    async def count_input_tokens(self, request: ChatRequest) -> int:
        return await (await self.ready()).count_input_tokens(document_model_request(request))

    async def generate(self, request: ChatRequest) -> ProviderReply:
        return await (await self.ready()).generate(document_model_request(request))


class DocumentAssistant(GraphAssistant):
    def __init__(
        self,
        config: DocumentRuntimeConfig,
        catalog: SourceCatalog,
        engine: Engine,
        authority: LocalAccess,
    ) -> None:
        if config.code_sha256 != evaluator_checksum() or (
            config.source_dataset_id != catalog.source_dataset_id
            or config.snapshot_id != catalog.snapshot_id
            or config.source_catalog_sha256 != catalog.checksum()
        ):
            raise ValueError("document_runtime_source_mismatch")
        self.runtime_config = DocumentRuntimeConfig.model_validate_json(config.model_dump_json())
        self.engine = engine
        graph = resolve_graph_config(config.graph)
        planner = DocumentPlanner(graph.config.policy.document_rules, catalog, config.channel)
        self.chat_provider = LazyBedrock(config)
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
            engine,
            config.pin.environment,
            load_retrieval_config(
                Path(__file__).resolve().parents[1] / "knowledge/retrieval.default.json"
            ),
            provider_factory=embeddings,
        )
        executor = ToolExecutor(
            authority,
            {"search_knowledge": cast(ToolAdapter, PinnedKnowledgeTool(self.knowledge))},
            graph.config.chat.tool_policy,
            config.pin.environment,
        )
        budget = graph.config.chat.budget
        super().__init__(
            graph.config_id,
            config.pin.manifest.index_id,
            graph.config.chat.tool_policy.request_deadline_seconds,
            budget.max_input_tokens + budget.max_output_tokens,
            str(budget.pricing.max_run_cost),
            "runtime",
            planner.prepare,
            lambda: GraphRunner(
                executor,
                graph,
                cast(ChatProvider, self.chat_provider),
                MemoryTraces(graph.config.policy),
                pin=config.pin,
                smoke=self.smoke,
            ),
            runtime_version=config.config_id(),
        )

    async def run(self, request: GraphRequest, authorization: str | None) -> GraphResult:
        try:
            pin = await asyncio.to_thread(
                current_index, self.engine, self.runtime_config.pin.environment, "retrieval"
            )
            if pin != self.runtime_config.pin:
                raise AssistantError(424)
        except AssistantError:
            raise
        except Exception:
            raise AssistantError(503) from None
        return await super().run(request, authorization)


def document_backend(
    config_path: Path,
    source_path: Path,
    engine: Engine,
    authority: LocalAccess,
    environment: Literal["local", "test"],
) -> DocumentAssistant:
    config = load_document_runtime(config_path)
    if config.pin.environment != environment:
        raise ValueError("document_runtime_environment_mismatch")
    return DocumentAssistant(config, load_source_catalog(source_path), engine, authority)
