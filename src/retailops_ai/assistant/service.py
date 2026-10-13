"""Server-owned planning boundary, conservative admission reservations and durable completion."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID, uuid4, uuid5

from pydantic import Field

from retailops_ai.agent.evidence import required_calls
from retailops_ai.agent.execution import READ_CAPABILITIES, read_capabilities
from retailops_ai.agent.graph_contracts import GraphCode, GraphRequest, GraphResult
from retailops_ai.agent.tools import KnowledgeRequest
from retailops_ai.assistant.contracts import (
    AssistantAnswer,
    AssistantQuery,
    AssistantRun,
    PersistedSuggestion,
    RecommendationPage,
    TraceNode,
    TraceUsage,
)
from retailops_ai.data_contracts.common import Contract, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Capability, Principal
from retailops_ai.pipelines.retrieval import KnowledgeDenied, resolve_scope
from retailops_ai.security.models import KnowledgeResourceScope

if TYPE_CHECKING:
    from retailops_ai.agent.graph import GraphRunner


class AssistantError(ValueError):
    def __init__(self, status: int) -> None:
        super().__init__("assistant_request_failed")
        self.status = status


class AdmissionPolicy(Contract):
    policy_version: Literal["assistant-admission-v1"] = "assistant-admission-v1"
    parallel_per_principal: int = Field(default=2, ge=1, le=2)
    parallel_total: int = Field(default=8, ge=1, le=8)
    window_seconds: int = Field(default=60, ge=60, le=300)
    runs_per_principal_window: int = Field(default=6, ge=1, le=6)
    runs_total_window: int = Field(default=24, ge=1, le=24)
    tokens_per_principal_window: int = Field(default=81000, ge=1, le=81000)
    tokens_total_window: int = Field(default=324000, ge=1, le=324000)
    cost_per_principal_window: str = Field(default="1", pattern=r"^[0-9]+(?:\.[0-9]{1,9})?$")
    cost_total_window: str = Field(default="4", pattern=r"^[0-9]+(?:\.[0-9]{1,9})?$")
    retention_seconds: int = Field(default=900, ge=300, le=900)
    capacity: int = Field(default=100, ge=1, le=100)


class RunLease(Contract):
    run: AssistantRun
    claim: UUID
    correlation_id: UUID = Field(default_factory=uuid4)
    owner_id: Symbol
    scope_json: str
    request_sha256: str
    reserved_tokens: int = Field(ge=1, le=19000)
    reserved_cost: str
    required_capabilities: list[Capability] = Field(default_factory=list, max_length=12)
    knowledge_scope: KnowledgeResourceScope | None = None


class AssistantStore(Protocol):
    async def admit(
        self, lease: RunLease, policy: AdmissionPolicy, deadline_seconds: float
    ) -> RunLease: ...
    async def finish(
        self,
        lease: RunLease,
        run: AssistantRun,
        answer: AssistantAnswer | None,
        suggestions: list[PersistedSuggestion],
    ) -> None: ...
    async def get(self, trace_id: UUID, principal: Principal) -> AssistantRun | None: ...
    async def recommendations(
        self, principal: Principal, *, limit: int = 50, offset: int = 0
    ) -> RecommendationPage: ...
    async def recommendation(
        self, recommendation_id: UUID, principal: Principal
    ) -> PersistedSuggestion | None: ...


class AssistantBackend(Protocol):
    source_kind: Literal["runtime", "fixture"]
    config_version: str
    index_id: str
    deadline_seconds: float
    reserved_tokens: int
    reserved_cost: str

    async def prepare(self, query: AssistantQuery, principal: Principal) -> GraphRequest: ...
    async def run(self, request: GraphRequest, authorization: str | None) -> GraphResult: ...


class GraphAssistant:
    """Inject an authoritative source resolver/planner and a fresh bounded runner per request.

    No natural-language or store-ID mapping is fabricated by this HTTP boundary. A real
    source/planner is a separate dependency; test scripts are permitted only in APP_ENV=test.
    """

    def __init__(
        self,
        config_version: str,
        index_id: str,
        deadline_seconds: float,
        reserved_tokens: int,
        reserved_cost: str,
        source_kind: Literal["runtime", "fixture"],
        planner: Callable[[AssistantQuery, Principal], Awaitable[GraphRequest]],
        runner: Callable[[], GraphRunner],
        *,
        runtime_version: str | None = None,
        native_tools: frozenset[str] | None = None,
    ) -> None:
        self.graph_config_version = config_version
        self.config_version = runtime_version or config_version
        self.index_id = index_id
        self.deadline_seconds = deadline_seconds
        self.reserved_tokens = reserved_tokens
        self.reserved_cost = reserved_cost
        self.source_kind = source_kind
        self.native_tools = (
            native_tools
            if native_tools is not None
            else (frozenset(READ_CAPABILITIES) if source_kind == "runtime" else frozenset())
        )
        self.planner = planner
        self.runner = runner

    async def prepare(self, query: AssistantQuery, principal: Principal) -> GraphRequest:
        return await self.planner(query, principal)

    async def run(self, request: GraphRequest, authorization: str | None) -> GraphResult:
        runner = self.runner()
        budget = runner.config.config.chat.budget
        if (
            runner.config.config_id != self.graph_config_version
            or runner.config.config.chat.knowledge_index_id != self.index_id
            or runner.executor.policy.request_deadline_seconds > self.deadline_seconds
            or budget.max_input_tokens + budget.max_output_tokens > self.reserved_tokens
            or budget.pricing.max_run_cost > Decimal(self.reserved_cost)
            or (runner.provider.source_kind == "fixture") != (self.source_kind == "fixture")
        ):
            raise AssistantError(503)
        return await runner.run_json(authorization, request.model_dump_json())


ERROR_STATUS: dict[GraphCode, int] = {
    "invalid_scope": 422,
    "unauthorized": 403,
    "dependency_unavailable": 424,
    "budget_exceeded": 429,
    "deadline_exceeded": 504,
    "provider_unavailable": 503,
    "invalid_output": 502,
    "invalid_evidence": 502,
    "unauthorized_tool": 502,
    "invalid_repair": 502,
    "trace_unavailable": 503,
    "cancelled": 504,
}


def authorized(principal: Principal, query: AssistantQuery) -> bool:
    return (
        "operator" in principal.roles
        and "assistant:query" in principal.capabilities
        and {str(item) for item in query.scope.product_ids} <= principal.product_ids
        and {str(item) for item in query.scope.store_ids} <= principal.selling_location_ids
    )


def readable(
    principal: Principal,
    owner: str,
    scope_json: str,
    required_capabilities: list[Capability] | None = None,
    knowledge_scope: KnowledgeResourceScope | None = None,
) -> bool:
    from retailops_ai.agent.tools import DataScope

    if "admin" in principal.roles and "assistant:audit" in principal.capabilities:
        return True
    scope = DataScope.model_validate_json(scope_json)
    if not set(required_capabilities or []) <= principal.capabilities:
        return False
    if knowledge_scope is not None:
        knowledge = principal.knowledge
        if (
            knowledge is None
            or knowledge.environment != knowledge_scope.environment
            or not set(knowledge_scope.repositories) <= knowledge.repositories
            or not set(knowledge_scope.access_classes) <= knowledge.access_classes
            or not set(knowledge_scope.document_statuses) <= knowledge.document_statuses
        ):
            return False
    return (
        principal.principal_id == owner
        and "operator" in principal.roles
        and "assistant:query" in principal.capabilities
        and set(scope.product_ids) <= principal.product_ids
        and set(scope.selling_location_ids) <= principal.selling_location_ids
        and scope.channel in principal.channels
    )


def recommendation_reader(principal: Principal) -> bool:
    return ("operator" in principal.roles and "assistant:query" in principal.capabilities) or (
        "admin" in principal.roles and "assistant:audit" in principal.capabilities
    )


def recommendation_readable(
    principal: Principal,
    item: PersistedSuggestion,
    owner: str,
    scope_json: str,
    required_capabilities: list[Capability],
    knowledge_scope: KnowledgeResourceScope | None,
) -> bool:
    if not readable(principal, owner, scope_json, required_capabilities, knowledge_scope):
        return False
    if "admin" in principal.roles and "assistant:audit" in principal.capabilities:
        return True
    from retailops_ai.domain.access import can_read_stockout

    return (
        item.product_id in principal.product_ids
        and item.selling_location_id in principal.selling_location_ids
        and item.channel in principal.channels
        and (
            item.stock_location_id is None
            or can_read_stockout(
                principal, products={item.product_id}, stock_locations={item.stock_location_id}
            )
        )
    )


class AssistantService:
    def __init__(
        self,
        backend: AssistantBackend,
        store: AssistantStore,
        environment: Literal["local", "test"],
        policy: AdmissionPolicy | None = None,
    ) -> None:
        if backend.source_kind == "fixture" and environment != "test":
            raise ValueError("fixture_assistant_requires_test_environment")
        if not 0 < backend.deadline_seconds <= 45 or not 0 < backend.reserved_tokens <= 19000:
            raise ValueError("assistant_budget_outside_profile")
        if not Decimal(backend.reserved_cost).is_finite() or Decimal(backend.reserved_cost) <= 0:
            raise ValueError("assistant_cost_reservation_required")
        self.backend, self.store, self.environment = backend, store, environment
        self.policy = policy or AdmissionPolicy()

    async def query(
        self,
        query: AssistantQuery,
        principal: Principal,
        authorization: str | None,
        *,
        correlation_id: UUID | None = None,
    ) -> AssistantAnswer:
        if not authorized(principal, query):
            raise AssistantError(403)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.backend.deadline_seconds
        try:
            request = await asyncio.wait_for(
                self.backend.prepare(query, principal), timeout=max(0.001, deadline - loop.time())
            )
            request = GraphRequest.model_validate_json(request.model_dump_json())
            # Resolver must return the exact source IDs/window, with a server-selected channel.
            if (
                request.question != query.question
                or set(request.scope.product_ids) != {str(x) for x in query.scope.product_ids}
                or set(request.scope.selling_location_ids)
                != {str(x) for x in query.scope.store_ids}
                or request.window.start != query.scope.from_
                or request.window.end != query.scope.to
            ):
                raise AssistantError(422)
            if request.scope.channel not in principal.channels:
                raise AssistantError(403)
            for call in required_calls(request):
                capabilities = read_capabilities(
                    call.tool,
                    native=call.tool
                    in getattr(
                        self.backend,
                        "native_tools",
                        frozenset(READ_CAPABILITIES)
                        if self.backend.source_kind == "runtime"
                        else frozenset(),
                    ),
                )
                if not capabilities <= principal.capabilities:
                    raise AssistantError(403)
                if isinstance(call, KnowledgeRequest):
                    resolve_scope(principal, call.retrieval, self.environment)
        except TimeoutError:
            raise AssistantError(504) from None
        except AssistantError:
            raise
        except KnowledgeDenied:
            raise AssistantError(403) from None
        except ValueError:
            raise AssistantError(422) from None
        now = datetime.now(UTC)
        initial = AssistantRun(
            trace_id=uuid4(),
            answer_id=None,
            status="running",
            outcome=None,
            requested_at=now,
            completed_at=None,
            agent_config_version=self.backend.config_version,
            index_id=self.backend.index_id,
            nodes=[],
            tools=[],
            usage=TraceUsage(input_tokens=0, output_tokens=0, duration_ms=0.0, estimated_cost="0"),
            error_code=None,
        )
        lease = RunLease(
            run=initial,
            claim=uuid4(),
            correlation_id=correlation_id or uuid4(),
            owner_id=principal.principal_id,
            scope_json=request.scope.model_dump_json(),
            request_sha256=canonical_sha256(query.model_dump(mode="json", by_alias=True)),
            reserved_tokens=self.backend.reserved_tokens,
            reserved_cost=self.backend.reserved_cost,
            required_capabilities=sorted(
                set().union(
                    *(
                        read_capabilities(
                            call.tool,
                            native=call.tool
                            in getattr(
                                self.backend,
                                "native_tools",
                                frozenset(READ_CAPABILITIES)
                                if self.backend.source_kind == "runtime"
                                else frozenset(),
                            ),
                        )
                        for call in required_calls(request)
                    )
                )
                | {"assistant:query"}
            ),
            knowledge_scope=KnowledgeResourceScope.model_validate_json(
                json.dumps(
                    {
                        "environment": principal.knowledge.environment,
                        "repositories": sorted(principal.knowledge.repositories),
                        "access_classes": sorted(principal.knowledge.access_classes),
                        "document_statuses": sorted(principal.knowledge.document_statuses),
                    }
                )
            )
            if principal.knowledge is not None
            and any(isinstance(call, KnowledgeRequest) for call in required_calls(request))
            else None,
        )
        # DB owns the lease clock and quotas. No model work precedes durable admission.
        lease = await self.store.admit(lease, self.policy, max(0.001, deadline - loop.time()))
        initial = lease.run
        answer: AssistantAnswer | None = None
        suggestions: list[PersistedSuggestion] = []
        result: GraphResult | None = None
        error: GraphCode | None = None
        cancelled = False
        try:
            if loop.time() >= deadline:
                raise TimeoutError()
            result = await asyncio.wait_for(
                self.backend.run(request, authorization), timeout=max(0.001, deadline - loop.time())
            )
            error = result.error_code
            if result.trace is None and result.error_code != "unauthorized":
                raise AssistantError(502)
            if result.trace is not None and (
                result.trace.owner_id != principal.principal_id
                or result.trace.scope != request.scope
                or result.trace.config_id
                != getattr(self.backend, "graph_config_version", self.backend.config_version)
                or result.trace.fixture_only != (self.backend.source_kind == "fixture")
                or (
                    result.trace.index_id is not None
                    and result.trace.index_id != self.backend.index_id
                )
                or result.trace.input_tokens + result.trace.output_tokens
                > self.backend.reserved_tokens
                or Decimal(result.trace.estimated_cost) > Decimal(self.backend.reserved_cost)
            ):
                raise AssistantError(502)
            if result.answer is not None:
                completed = datetime.now(UTC)
                answer_id = uuid5(initial.trace_id, "answer")
                actions = []
                for candidate in result.suggestions:
                    if (
                        candidate.expires_at <= completed
                        or candidate.product_id not in request.scope.product_ids
                        or candidate.selling_location_id not in request.scope.selling_location_ids
                        or candidate.channel != request.scope.channel
                    ):
                        raise AssistantError(502)
                    recommendation_id = uuid5(initial.trace_id, candidate.candidate_id)
                    actions.append(
                        candidate.draft_action().model_dump(mode="json")
                        | {"recommendation_id": str(recommendation_id)}
                    )
                    suggestions.append(
                        PersistedSuggestion.model_validate_json(
                            json.dumps(
                                candidate.model_dump(mode="json")
                                | {
                                    "recommendation_id": str(recommendation_id),
                                    "trace_id": str(initial.trace_id),
                                    "answer_id": str(answer_id),
                                    "created_at": completed.isoformat(),
                                    "store_id": candidate.selling_location_id,
                                    "summary": result.answer.summary,
                                    "agent_config_version": initial.agent_config_version,
                                }
                            )
                        )
                    )
                answer = AssistantAnswer.model_validate_json(
                    json.dumps(
                        result.answer.model_dump(mode="json", exclude={"kind"})
                        | {
                            "recommended_actions": actions,
                            "answer_id": str(answer_id),
                            "trace_id": str(initial.trace_id),
                            "agent_config_version": initial.agent_config_version,
                            "index_id": initial.index_id,
                            "created_at": completed.isoformat(),
                        }
                    )
                )
        except asyncio.CancelledError:
            error, cancelled = "cancelled", True
        except TimeoutError:
            error = "deadline_exceeded"
        except AssistantError as exc:
            error = {
                403: "unauthorized",
                424: "dependency_unavailable",
                429: "budget_exceeded",
                503: "provider_unavailable",
                504: "deadline_exceeded",
            }.get(exc.status, "invalid_output")  # type: ignore[assignment]
            result = None
        except Exception:
            error = "invalid_output"
            result = None
        if error is not None:
            answer, suggestions = None, []
        trace = result.trace if result else None
        final = AssistantRun(
            **{
                **initial.model_dump(),
                "status": "succeeded" if answer else "failed",
                "answer_id": answer.answer_id if answer else None,
                "outcome": answer.outcome if answer else None,
                "completed_at": datetime.now(UTC),
                "error_code": error if error else (None if answer else "invalid_output"),
                "nodes": [TraceNode.from_audit(node) for node in trace.nodes] if trace else [],
                "tools": list(trace.tools) if trace else [],
                "usage": TraceUsage(
                    input_tokens=trace.input_tokens if trace else 0,
                    output_tokens=trace.output_tokens if trace else 0,
                    estimated_cost=trace.estimated_cost if trace else "0",
                    duration_ms=max(
                        0.0, (loop.time() - (deadline - self.backend.deadline_seconds)) * 1000
                    ),
                ),
            }
        )
        try:
            await asyncio.shield(
                asyncio.wait_for(
                    self.store.finish(lease, final, answer, suggestions),
                    timeout=max(0.001, deadline - loop.time()),
                )
            )
        except TimeoutError:
            raise AssistantError(504) from None
        except AssistantError:
            raise
        except Exception:
            raise AssistantError(503) from None
        if cancelled:
            raise asyncio.CancelledError()
        if answer is None:
            raise AssistantError(ERROR_STATUS[final.error_code or "invalid_output"])
        return answer
