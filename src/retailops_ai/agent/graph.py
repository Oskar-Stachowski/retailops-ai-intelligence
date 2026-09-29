"""A finite LangGraph DAG: one missing-evidence pass and one total repair, without agents/tools that write."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TypedDict, cast
from uuid import uuid4

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from retailops_ai.agent.chat import ChatFailure, ChatProvider, ChatSession, Phase, SmokeBudget
from retailops_ai.agent.chat_context import EvidenceSnapshot, InvalidEvidence, tool_result_ref
from retailops_ai.agent.chat_contracts import AnswerDraft, PlanDraft
from retailops_ai.agent.evidence import Catalogue, EvidencePolicy, call_id
from retailops_ai.agent.execution import ToolExecutor, ToolFailure, ToolSession
from retailops_ai.agent.graph_config import ResolvedGraphConfig
from retailops_ai.agent.graph_contracts import (
    GraphCode,
    GraphRequest,
    GraphResult,
    NodeAudit,
    SafeToolAudit,
    SafeTrace,
)
from retailops_ai.agent.graph_traces import MemoryTraces, TraceUnavailable
from retailops_ai.agent.suggestions import SuggestionCandidate
from retailops_ai.agent.tools import (
    AnomalyResult,
    ForecastResult,
    KnowledgeResult,
    ModelStatusResult,
    RiskResult,
    ToolInput,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.security.local import strict_json


class Flow(TypedDict):
    route: str


class GraphRunner:
    def __init__(
        self,
        executor: ToolExecutor,
        config: ResolvedGraphConfig,
        provider: ChatProvider,
        traces: MemoryTraces,
        *,
        pin: IndexPin | None = None,
        smoke: SmokeBudget | None = None,
    ) -> None:
        self.config = config.verified()
        if (
            executor.policy != self.config.config.chat.tool_policy
            or traces.policy != self.config.config.policy
        ):
            raise ValueError("graph_executor_or_trace_policy_mismatch")
        self.executor = executor
        self.provider = provider
        self.traces = traces
        self.pin_json = pin.model_dump_json() if pin else None
        self.smoke = smoke

    async def run_json(self, authorization: str | None, raw: str) -> GraphResult:
        try:
            if len(raw.encode()) > 16384:
                raise ValueError("graph_request_too_large")
            strict_json(raw.encode())
            request = GraphRequest.model_validate_json(raw)
        except (ValueError, RecursionError):
            return GraphResult(status="failed", error_code="invalid_scope", answer=None, trace=None)
        run = _Execution(self, authorization, request)
        return await run.execute()


class _Execution:
    def __init__(
        self, runner: GraphRunner, authorization: str | None, request: GraphRequest
    ) -> None:
        self.runner = runner
        self.authorization = authorization
        self.request = request
        self.policy = EvidencePolicy(request, runner.config.config.policy)
        self.started = runner.executor.timer()
        self.deadline = self.started + runner.executor.policy.request_deadline_seconds
        self.trace_id = "trace-" + uuid4().hex
        self.correlation_id = "correlation-" + uuid4().hex
        self.tools: ToolSession | None = None
        self.chat: ChatSession | None = None
        self.plan: tuple[ToolInput, ...] = ()
        self.attempted: set[str] = set()
        self.extra_rounds = 0
        self.answer: AnswerDraft | None = None
        self.error: GraphCode | None = None
        self.nodes: list[NodeAudit] = []
        self.catalogue: Catalogue | None = None
        self.repair_phase: Phase = "plan"
        self.repair_destination = "tools"
        self.trace: SafeTrace | None = None
        self.persist_attempted = False
        self.suggestions: tuple[SuggestionCandidate, ...] = ()

    def guard(self) -> None:
        if self.runner.executor.timer() >= self.deadline:
            raise ChatFailure("deadline_exceeded")

    def snapshot(self) -> EvidenceSnapshot:
        if self.tools is None:
            raise InvalidEvidence("missing_authenticated_session")
        return EvidenceSnapshot.build(self.tools.accepted_outputs(), self.runner.config.chat)

    def reference_context(self, snapshot: EvidenceSnapshot) -> dict[str, object]:
        if self.tools is None:
            raise InvalidEvidence("missing_authenticated_session")
        self.catalogue = self.policy.build(self.tools, snapshot)
        return self.catalogue.payload() | {
            "request": self.request.model_dump(mode="json"),
            "permitted_calls": [
                call.model_dump(mode="json")
                for call in self.policy.calls
                if call_id(call) not in self.attempted
            ],
        }

    def validate_answer(self, snapshot: EvidenceSnapshot, answer: AnswerDraft) -> None:
        if self.tools is None:
            raise InvalidEvidence("missing_authenticated_session")
        self.policy.validate(self.tools, snapshot, answer)

    async def validate_auth(self) -> str:
        pin = IndexPin.model_validate_json(self.runner.pin_json) if self.runner.pin_json else None
        self.tools = self.runner.executor.open_session(
            self.authorization, default_scope=self.request.scope, pin=pin
        )
        self.authorization = None
        self.tools.deadline = min(self.tools.deadline, self.deadline)
        for call in self.policy.calls:
            self.tools.validate_call(call.model_dump_json())
        self.chat = ChatSession(
            self.tools,
            self.runner.config.chat,
            self.runner.provider,
            smoke=self.runner.smoke,
            reference_context=self.reference_context,
            answer_validator=self.validate_answer,
        )
        return "refuse" if self.policy.refused else "plan"

    def check_plan(self, draft: PlanDraft) -> None:
        permitted = {call_id(call) for call in self.policy.calls} - self.attempted
        ids = [call_id(call) for call in draft.tools]
        if not set(ids) <= permitted or len(set(ids)) != len(ids):
            raise ChatFailure("unauthorized_tool")
        self.plan = tuple(draft.tools)

    async def plan_evidence(self, *, extra: bool = False) -> str:
        if self.chat is None:
            raise ChatFailure("unauthorized_tool")
        if extra:
            self.extra_rounds += 1
        try:
            draft = await self.chat.call("plan", self.request.question)
        except ChatFailure as exc:
            if exc.code in {"invalid_output", "invalid_evidence"} and self.chat.repairs == 0:
                self.repair_phase = "plan"
                self.repair_destination = "tools_extra" if extra else "tools"
                return "repair_extra" if extra else "repair_plan"
            raise
        if not isinstance(draft, PlanDraft):
            raise ChatFailure("invalid_output")
        self.check_plan(draft)
        return "tools_extra" if extra else "tools"

    async def retrieve_tools(self, *, extra: bool = False) -> str:
        if self.tools is None:
            raise ToolFailure("unauthorized")
        for call in self.plan:
            self.guard()
            cid = call_id(call)
            if cid in self.attempted:
                raise ChatFailure("unauthorized_tool")
            self.attempted.add(cid)
            try:
                await self.tools.execute_json(call.model_dump_json())
            except ToolFailure as exc:
                self.policy.failures[cid] = exc.code
                if exc.code not in {"stale", "not_found", "unsupported_grain"}:
                    raise
        return "check_final" if extra else "check_evidence"

    async def check_evidence(self, *, final: bool = False) -> str:
        if self.tools is None:
            raise ToolFailure("unauthorized")
        self.catalogue = self.policy.build(self.tools, self.snapshot())
        missing = {call_id(call) for call in self.policy.calls} - self.attempted
        if missing and not final:
            return "plan_extra"
        return "synthesize"

    async def synthesize(self) -> str:
        if self.tools is None or self.chat is None:
            raise ToolFailure("unauthorized")
        self.catalogue = self.policy.build(self.tools, self.snapshot())
        if self.catalogue.expected_outcome != "answered":
            self.answer = self.catalogue.render(())
            return "validate_output"
        try:
            draft = await self.chat.call("synthesize", self.request.question)
        except ChatFailure as exc:
            if exc.code in {"invalid_output", "invalid_evidence"} and self.chat.repairs == 0:
                self.repair_phase = "synthesize"
                self.repair_destination = "validate_output"
                return "repair_answer"
            raise
        if not isinstance(draft, AnswerDraft):
            raise ChatFailure("invalid_output")
        self.answer = draft
        return "validate_output"

    async def repair(self) -> str:
        if self.chat is None:
            raise ToolFailure("unauthorized")
        draft = await self.chat.call("repair", self.request.question)
        if self.repair_phase == "plan" and isinstance(draft, PlanDraft):
            self.check_plan(draft)
        elif self.repair_phase == "synthesize" and isinstance(draft, AnswerDraft):
            self.answer = draft
        else:
            raise ChatFailure("invalid_output")
        return self.repair_destination

    async def validate_output(self) -> str:
        if self.answer is None:
            raise ChatFailure("invalid_output")
        self.validate_answer(self.snapshot(), self.answer)
        if self.tools is not None:
            self.catalogue = self.policy.build(self.tools, self.snapshot())
            selected = tuple(
                fact for fact in self.catalogue.facts if fact.claim in self.answer.evidence
            )
            self.suggestions = self.catalogue.suggestions(selected)
        return "persist_trace"

    def error_code(self, exc: Exception) -> GraphCode:
        if isinstance(exc, ChatFailure):
            return cast(GraphCode, exc.code)
        if isinstance(exc, ToolFailure):
            if exc.code == "budget_exceeded" and self.runner.executor.timer() >= self.deadline:
                return "deadline_exceeded"
            return (
                cast(GraphCode, exc.code)
                if exc.code in {"invalid_scope", "unauthorized", "budget_exceeded"}
                else "dependency_unavailable"
            )
        return (
            "invalid_evidence"
            if isinstance(exc, (InvalidEvidence, ValueError))
            else "provider_unavailable"
        )

    def node(
        self, name: str, operation: Callable[[], Awaitable[str]]
    ) -> Callable[[Flow], Awaitable[Flow]]:
        async def execute(state: Flow) -> Flow:
            started = self.runner.executor.timer()
            try:
                self.guard()
                route = await operation()
                self.guard()
                self.nodes.append(
                    NodeAudit(
                        node=name,
                        status="ok",
                        error_code=None,
                        duration_ms=max(0.0, (self.runner.executor.timer() - started) * 1000),
                    )
                )
                return {"route": route}
            except asyncio.CancelledError:
                self.error = "cancelled"
                self.nodes.append(
                    NodeAudit(
                        node=name,
                        status="error",
                        error_code=self.error,
                        duration_ms=max(0.0, (self.runner.executor.timer() - started) * 1000),
                    )
                )
                raise
            except Exception as exc:
                self.error = self.error_code(exc)
                self.answer = None
                self.nodes.append(
                    NodeAudit(
                        node=name,
                        status="error",
                        error_code=self.error,
                        duration_ms=max(0.0, (self.runner.executor.timer() - started) * 1000),
                    )
                )
                return {"route": "persist_trace"}

        return execute

    def make_trace(self) -> SafeTrace | None:
        if self.tools is None:
            return None
        releases: set[str] = set()
        sources: set[str] = set()
        for output in self.tools.accepted_outputs():
            sources.add(tool_result_ref(output))
            if isinstance(output, ForecastResult):
                releases.update(item.release_id for item in output.result.items)
            elif isinstance(output, (RiskResult, AnomalyResult)):
                releases.update(item.model_release_ref for item in output.items)
            elif isinstance(output, ModelStatusResult):
                releases.update(item.deployed_release_ref for item in output.items)
            elif isinstance(output, KnowledgeResult):
                sources.update(
                    chunk.source_ref for hit in output.items for chunk in hit.chunk.occurrences
                )
        chat = self.chat
        tool_audits = []
        outputs = self.tools.accepted_outputs()
        for audit in self.tools.audit[:6]:
            audited_output = next((item for item in outputs if item.tool == audit.tool), None)
            freshness = "unavailable" if audit.status == "error" else "missing"
            if audit.status == "ok" and audited_output is not None:
                if isinstance(audited_output, KnowledgeResult):
                    freshness = "not_requested"
                elif isinstance(audited_output, ForecastResult):
                    freshness = (
                        "unavailable"
                        if audited_output.result.freshness_status == "unknown"
                        else audited_output.result.freshness_status
                    )
                else:
                    freshness = audited_output.freshness_status
            tool_audits.append(
                SafeToolAudit.model_validate_json(
                    json.dumps(
                        {
                            "name": audit.tool,
                            "status": audit.status,
                            "source_refs": list(audit.source_refs)[:100],
                            "freshness_status": freshness,
                        }
                    )
                )
            )
        return SafeTrace.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "trace_id": self.trace_id,
                    "correlation_id": self.correlation_id,
                    "owner_id": self.tools.principal.principal_id,
                    "scope": self.request.scope.model_dump(mode="json"),
                    "config_id": self.runner.config.config_id,
                    "chat_config_id": self.runner.config.chat.config_id,
                    "request_sha256": canonical_sha256(self.request.model_dump(mode="json")),
                    "index_id": self.tools.pin.manifest.index_id if self.tools.pin else None,
                    "release_refs": sorted(releases)[:100],
                    "source_refs": sorted(sources)[:100],
                    "release_refs_total": len(releases),
                    "source_refs_total": len(sources),
                    "status": "failed" if self.error else "succeeded",
                    "error_code": self.error,
                    "nodes": [node.model_dump(mode="json") for node in self.nodes],
                    "tools": [audit.model_dump(mode="json") for audit in tool_audits],
                    "tool_calls": self.tools.calls,
                    "model_calls": chat.calls if chat else 0,
                    "extra_evidence_rounds": self.extra_rounds,
                    "repairs": chat.repairs if chat else 0,
                    "input_tokens": chat.input_tokens if chat else 0,
                    "output_tokens": chat.output_tokens if chat else 0,
                    "estimated_cost": format(chat.cost, "f") if chat else "0",
                    "fixture_only": self.runner.provider.source_kind == "fixture",
                    "created_at": self.runner.executor.clock().isoformat(),
                }
            )
        )

    def persist_trace(self) -> None:
        if self.persist_attempted:
            return
        self.persist_attempted = True
        self.nodes.append(
            NodeAudit(node="persist_trace", status="ok", error_code=None, duration_ms=0.0)
        )
        self.trace = self.make_trace()
        if self.trace is not None:
            try:
                self.runner.traces.save(self.trace)
            except TraceUnavailable:
                self.error, self.answer = "trace_unavailable", None
                self.nodes[-1] = NodeAudit(
                    node="persist_trace", status="error", error_code=self.error, duration_ms=0.0
                )
                self.trace = self.make_trace()

    async def execute(self) -> GraphResult:
        builder: StateGraph[Flow, None, Flow, Flow] = StateGraph(Flow)
        nodes = {
            "validate_auth": self.validate_auth,
            "plan": self.plan_evidence,
            "plan_extra": lambda: self.plan_evidence(extra=True),
            "tools": self.retrieve_tools,
            "tools_extra": lambda: self.retrieve_tools(extra=True),
            "check_evidence": self.check_evidence,
            "check_final": lambda: self.check_evidence(final=True),
            "synthesize": self.synthesize,
            "repair_plan": self.repair,
            "repair_extra": self.repair,
            "repair_answer": self.repair,
            "validate_output": self.validate_output,
        }
        routes = {
            "validate_auth": ["plan", "refuse"],
            "plan": ["tools", "repair_plan"],
            "repair_plan": ["tools"],
            "tools": ["check_evidence"],
            "check_evidence": ["plan_extra", "synthesize"],
            "plan_extra": ["tools_extra", "repair_extra"],
            "repair_extra": ["tools_extra"],
            "tools_extra": ["check_final"],
            "check_final": ["synthesize"],
            "synthesize": ["validate_output", "repair_answer"],
            "repair_answer": ["validate_output"],
            "validate_output": ["persist_trace"],
        }
        for name, operation in nodes.items():
            builder.add_node(name, RunnableLambda(self.node(name, operation)))
            builder.add_conditional_edges(
                name,
                lambda state: state["route"],
                ["synthesize" if target == "refuse" else target for target in routes[name]]
                + ["persist_trace"]
                if name != "validate_auth"
                else {"plan": "plan", "refuse": "synthesize", "persist_trace": "persist_trace"},
            )

        async def persist(state: Flow) -> Flow:
            self.persist_trace()
            return {"route": "end"}

        builder.add_node("persist_trace", persist)
        builder.add_edge(START, "validate_auth")
        builder.add_edge("persist_trace", END)
        graph = builder.compile(checkpointer=False, debug=False)
        try:
            # Never inherit remote tracing or checkpoint credentials from developer environment.
            with tracing_context(enabled=False):
                await asyncio.wait_for(
                    graph.ainvoke(
                        {"route": "validate_auth"},
                        config={
                            "recursion_limit": self.runner.config.config.policy.max_steps,
                            "callbacks": [],
                        },
                    ),
                    timeout=max(0.001, self.deadline - self.runner.executor.timer()),
                )
        except TimeoutError:
            self.error, self.answer = "deadline_exceeded", None
        except asyncio.CancelledError:
            self.error, self.answer = "cancelled", None
            self.persist_trace()
            raise
        except Exception as exc:
            self.error, self.answer = self.error_code(exc), None
        self.persist_trace()
        return GraphResult(
            status="failed" if self.error else "succeeded",
            error_code=self.error,
            answer=self.answer,
            trace=self.trace,
            suggestions=list(self.suggestions) if not self.error else [],
        )
