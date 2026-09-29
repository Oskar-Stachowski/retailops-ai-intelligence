"""Pinned RAG integration and explicit deterministic fixtures for future data tools."""

import asyncio
from typing import Literal, Protocol

from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    OUTPUT,
    KnowledgeRequest,
    KnowledgeResult,
    ToolInput,
    ToolOutput,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.knowledge.retrieval import RetrievalRequest, RetrievalResult


class PinnedKnowledgeBackend(Protocol):
    def search_pinned(
        self, pin: IndexPin, request: RetrievalRequest, principal: Principal
    ) -> RetrievalResult: ...


class PinnedKnowledgeTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, backend: PinnedKnowledgeBackend) -> None:
        self.backend = backend

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> KnowledgeResult:
        if not isinstance(request, KnowledgeRequest) or pin is None or pin.lane != "retrieval":
            raise ToolFailure("unavailable")
        result = await asyncio.to_thread(
            self.backend.search_pinned, pin, request.retrieval, principal
        )
        if result.index_id != pin.manifest.index_id or result.corpus_id != pin.manifest.corpus_id:
            raise ToolFailure("unavailable")
        return KnowledgeResult(
            schema_version="1.0",
            contract_type="agent_tool_result",
            tool="search_knowledge",
            status="ok" if result.items else "no_data",
            index_id=result.index_id,
            pin_generation=pin.generation,
            provider="bedrock",
            retrieval_config_id=result.retrieval_config_id,
            content_trust="untrusted_reference",
            items=list(result.items),
            context_tokens=result.context_tokens,
            context_bytes=result.context_bytes,
            source_kind="runtime",
        )


class FixtureTools:
    """Exact request bindings, no generated zeroes or random fallback values."""

    source_kind: Literal["fixture"] = "fixture"

    def __init__(self, cases: list[tuple[ToolInput, ToolOutput]]) -> None:
        self._cases: dict[str, str] = {}
        for request, output in cases:
            if output.source_kind != "fixture" or output.tool != request.tool:
                raise ValueError("fixture_tool_binding_mismatch")
            key = canonical_sha256(request.model_dump(mode="json"))
            if key in self._cases:
                raise ValueError("duplicate_fixture_tool_request")
            self._cases[key] = OUTPUT.validate_json(output.model_dump_json()).model_dump_json()

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> ToolOutput:
        key = canonical_sha256(request.model_dump(mode="json"))
        value = self._cases.get(key)
        if value is None:
            raise ToolFailure("unavailable")
        return OUTPUT.validate_json(value)
