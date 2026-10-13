"""Versioned exact questions; server policy owns intents, windows and source scope."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.agent.document_evidence import question_key
from retailops_ai.agent.evaluation import evaluator_checksum
from retailops_ai.agent.evidence import DENIED_QUESTION, required_calls
from retailops_ai.agent.execution import READ_CAPABILITIES
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import ResolvedGraphConfig
from retailops_ai.agent.graph_contracts import GraphRequest, Intent
from retailops_ai.agent.tools import MAX_QUALIFIED_SALES_POINTS, ToolName
from retailops_ai.assistant.contracts import AssistantQuery
from retailops_ai.assistant.planner import validate_source_scope
from retailops_ai.assistant.service import AssistantError, GraphAssistant, authorized
from retailops_ai.assistant.source_catalog import SourceCatalog
from retailops_ai.data_contracts.common import Contract, DateWindow, Versioned, end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.security.local import strict_json


class QuestionRoute(Contract):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    intent: Intent

    @model_validator(mode="after")
    def read_only_question(self) -> Self:
        if (
            not question_key(self.question)
            or "\0" in self.question
            or len(self.question.encode()) > 8000
            or DENIED_QUESTION.search(self.question)
            or self.intent == "refuse"
        ):
            raise ValueError("invalid_read_only_question_route")
        return self


class QuestionRoutes(Versioned):
    profile: Literal["assistant-question-routes-v1"]
    labels_state: Literal["proposed", "accepted"]
    graph_config_id: Annotated[str, Field(pattern=r"^agent-graph-config-sha256-[0-9a-f]{64}$")]
    routes: tuple[QuestionRoute, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unambiguous(self) -> Self:
        keys = [question_key(route.question) for route in self.routes]
        if len(keys) != len(set(keys)):
            raise ValueError("ambiguous_question_routes")
        return self

    def profile_id(self) -> str:
        return "assistant-routes-sha256-" + canonical_sha256(self.model_dump(mode="json"))


def load_question_routes(path: Path) -> QuestionRoutes:
    with path.open("rb") as source:
        raw = source.read(256_001)
    if len(raw) > 256_000:
        raise ValueError("question_routes_file_too_large")
    strict_json(raw)
    return QuestionRoutes.model_validate_json(raw)


class ReviewedPlanner:
    def __init__(
        self,
        profile: QuestionRoutes,
        graph: ResolvedGraphConfig,
        catalog: SourceCatalog,
        channel: Literal["store", "online"],
        available_tools: frozenset[ToolName],
        environment: Literal["local", "test"],
        *,
        allow_proposed: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        graph = graph.verified()
        profile = QuestionRoutes.model_validate_json(profile.model_dump_json())
        if profile.graph_config_id != graph.config_id:
            raise ValueError("question_routes_graph_mismatch")
        if profile.labels_state != "accepted" and not (environment == "test" and allow_proposed):
            raise ValueError("question_routes_require_review")
        if not available_tools <= set(READ_CAPABILITIES):
            raise ValueError("question_routes_unknown_tool")
        documents = {
            (question_key(rule.question), rule.intent)
            for rule in graph.config.policy.document_rules
        }
        if any(
            route.intent in {"documentation", "verified_state"}
            and (question_key(route.question), route.intent) not in documents
            for route in profile.routes
        ):
            raise ValueError("question_route_without_document_evidence")
        self.profile = profile
        self.routes = {question_key(route.question): route.intent for route in profile.routes}
        self.catalog = SourceCatalog.model_validate_json(catalog.model_dump_json())
        self.channel, self.available_tools, self.clock = channel, available_tools, clock
        self.max_forecast_rows = min(20, graph.config.chat.tool_policy.max_rows)

    async def prepare(self, query: AssistantQuery, principal: Principal) -> GraphRequest:
        if not authorized(principal, query) or self.channel not in principal.channels:
            raise AssistantError(403)
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise AssistantError(503)
        intent = self.routes.get(question_key(query.question))
        if DENIED_QUESTION.search(query.question):
            intent = "refuse"
        if intent is None:
            raise AssistantError(422)
        as_of = now
        if intent in {"forecast", "risk", "recommendations"}:
            # The existing forecast tool accepts a closed UTC day, never a
            # future end-of-day timestamp. This is a requested cutoff, not a
            # claim that an output has been published for that origin.
            as_of = end_of_day(now.date())
            if as_of > now:
                as_of = end_of_day(now.date() - timedelta(days=1))
        comparison = None
        if intent == "sales_comparison":
            span = query.scope.to - query.scope.from_ + timedelta(days=1)
            comparison = {
                "start": query.scope.from_ - span,
                "end": query.scope.from_ - timedelta(days=1),
            }
        limit = 5
        if intent in {"inventory", "risk", "operations", "model"}:
            series = len(query.scope.product_ids) * len(query.scope.store_ids)
            if series > self.max_forecast_rows:
                raise AssistantError(422)
            limit = min(max(limit, series), self.max_forecast_rows)
        if intent in {"sales", "sales_comparison", "investigation"}:
            series = len(query.scope.product_ids) * len(query.scope.store_ids)
            days = (query.scope.to - query.scope.from_).days + 1
            if series > self.max_forecast_rows or series * days > MAX_QUALIFIED_SALES_POINTS:
                raise AssistantError(422)
            limit = min(max(limit, series), self.max_forecast_rows)
        if intent in {"forecast", "recommendations", "anomalies", "investigation"}:
            # Native pages must contain the entire requested product/location/day
            # grid. Reject oversized requests before admission rather than taking
            # a truncated page or increasing the evaluated graph's row budget.
            cells = (
                len(query.scope.product_ids)
                * len(query.scope.store_ids)
                * ((query.scope.to - query.scope.from_).days + 1)
            )
            if cells > self.max_forecast_rows:
                raise AssistantError(422)
            limit = min(max(limit, cells), self.max_forecast_rows)
        try:
            request = GraphRequest.model_validate(
                {
                    "schema_version": "1.0",
                    "question": query.question,
                    "intent": intent,
                    "scope": {
                        "product_ids": [str(value) for value in query.scope.product_ids],
                        "selling_location_ids": [str(value) for value in query.scope.store_ids],
                        "channel": self.channel,
                    },
                    "as_of": as_of,
                    "window": {"start": query.scope.from_, "end": query.scope.to},
                    "comparison_window": comparison,
                    "limit": limit,
                }
            )
            calls = required_calls(request)
        except ValueError:
            raise AssistantError(422) from None
        if any(READ_CAPABILITIES[call.tool] not in principal.capabilities for call in calls):
            raise AssistantError(403)
        if any(call.tool in {"get_stockout_risk", "get_inventory_status"} for call in calls):
            physical = principal.stockout
            if physical is None or not set(request.scope.product_ids) <= physical.product_ids:
                raise AssistantError(403)
        if any(call.tool not in self.available_tools for call in calls):
            raise AssistantError(424)
        windows: tuple[DateWindow, ...] = (request.window,)
        if request.comparison_window is not None:
            windows += (request.comparison_window,)
        validate_source_scope(query, self.catalog, self.channel, as_of, windows)
        return request


def reviewed_backend(
    profile: QuestionRoutes,
    graph: ResolvedGraphConfig,
    catalog: SourceCatalog,
    channel: Literal["store", "online"],
    available_tools: frozenset[ToolName],
    environment: Literal["local", "test"],
    source_kind: Literal["runtime", "fixture"],
    runner: Callable[[], GraphRunner],
    *,
    allow_proposed: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    native_tools: frozenset[str] | None = None,
) -> GraphAssistant:
    """Bind a reviewed planner to existing server adapters, without creating SDK clients."""
    if source_kind == "fixture" and environment != "test":
        raise ValueError("fixture_assistant_requires_test_environment")
    if native_tools is not None and not native_tools <= available_tools:
        raise ValueError("native_tool_outside_assistant_catalog")
    planner = ReviewedPlanner(
        profile,
        graph,
        catalog,
        channel,
        available_tools,
        environment,
        allow_proposed=allow_proposed,
        clock=clock,
    )

    def checked_runner() -> GraphRunner:
        instance = runner()
        if (
            set(instance.executor.adapters) != available_tools
            or instance.executor.environment != environment
            or (
                native_tools is not None
                and native_tools
                != frozenset(
                    key
                    for key, adapter in instance.executor.adapters.items()
                    if adapter.source_kind == "runtime"
                )
            )
            or (
                source_kind == "runtime"
                and any(
                    adapter.source_kind != "runtime"
                    for adapter in instance.executor.adapters.values()
                )
            )
        ):
            raise AssistantError(503)
        return instance

    binding = {
        "question_routes_id": planner.profile.profile_id(),
        "source_catalog_sha256": planner.catalog.checksum(),
        "graph_config_id": graph.config_id,
        "code_sha256": evaluator_checksum(),
        "channel": channel,
        "available_tools": sorted(available_tools),
        "environment": environment,
        "source_kind": source_kind,
    }
    if native_tools is not None:
        binding["native_tools"] = sorted(native_tools)
    budget = graph.config.chat.budget
    return GraphAssistant(
        graph.config_id,
        graph.config.chat.knowledge_index_id,
        graph.config.chat.tool_policy.request_deadline_seconds,
        budget.max_input_tokens + budget.max_output_tokens,
        str(budget.pricing.max_run_cost),
        source_kind,
        planner.prepare,
        checked_runner,
        runtime_version="assistant-runtime-sha256-" + canonical_sha256(binding),
        native_tools=native_tools,
    )
