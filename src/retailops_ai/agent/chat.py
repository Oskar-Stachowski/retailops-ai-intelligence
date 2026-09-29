"""Finite provider boundary with shared deadline, cost reservation, retries and one repair."""

import asyncio
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from threading import Lock
from typing import Literal, Protocol, cast

from retailops_ai.agent.chat_config import ChatModelConfig, ChatPricing, ResolvedChatConfig
from retailops_ai.agent.chat_context import EvidenceSnapshot, InvalidEvidence
from retailops_ai.agent.chat_contracts import (
    DRAFT,
    AnswerDraft,
    ChatDraft,
    PlanDraft,
    ProviderReply,
)
from retailops_ai.agent.execution import READ_CAPABILITIES, ToolFailure, ToolSession
from retailops_ai.agent.tools import ToolName
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.security.local import strict_json

Phase = Literal["plan", "synthesize", "repair"]
ERRORS = {
    "budget_exceeded": "The model budget was exceeded.",
    "deadline_exceeded": "The request deadline was exceeded.",
    "provider_unavailable": "The model provider is unavailable.",
    "invalid_output": "The model response does not match the requested schema.",
    "invalid_evidence": "The model response refers to unsupported evidence.",
    "unauthorized_tool": "The proposed tool call is not authorized.",
    "invalid_repair": "The response repair is not authorized.",
}


class ChatFailure(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(ERRORS[code])


class ProviderFailure(ValueError):
    def __init__(self, code: Literal["throttled", "transient", "auth", "schema"]) -> None:
        self.code = code
        super().__init__("Model provider request failed.")


@dataclass(frozen=True)
class ChatRequest:
    config_id: str
    phase: Phase
    expected_kind: Literal["tool_plan", "answer"]
    system_prompts: tuple[str, ...]
    question: str
    allowed_tools: tuple[ToolName, ...]
    references_json: str
    max_output_tokens: int
    repair_code: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "config_id": self.config_id,
            "phase": self.phase,
            "expected_kind": self.expected_kind,
            "system": list(self.system_prompts),
            "user": {"question": self.question},
            "allowed_tools": list(self.allowed_tools),
            "references": json.loads(self.references_json),
            "max_output_tokens": self.max_output_tokens,
            "repair_code": self.repair_code,
        }

    def request_hash(self) -> str:
        return canonical_sha256(self.payload())


class ChatProvider(Protocol):
    model: ChatModelConfig
    source_kind: Literal["fixture", "runtime"]

    def input_token_bound(self, request: ChatRequest) -> int: ...
    async def generate(self, request: ChatRequest) -> ProviderReply: ...


def estimated_cost(pricing: ChatPricing, inputs: int, outputs: int) -> Decimal:
    return (pricing.input_per_million * inputs + pricing.output_per_million * outputs) / Decimal(
        1000000
    )


class SmokeBudget:
    """Shared cost cap across all sessions of one controlled smoke, including in-flight calls."""

    def __init__(self, pricing: ChatPricing) -> None:
        self.pricing_id = canonical_sha256(pricing.model_dump(mode="json"))
        self.limit = pricing.max_smoke_cost
        self.charged = Decimal(0)
        self._lock = Lock()

    def reserve(self, amount: Decimal) -> None:
        with self._lock:
            if self.charged + amount > self.limit:
                raise ChatFailure("budget_exceeded")
            self.charged += amount

    def settle(self, reserved: Decimal, actual: Decimal) -> None:
        with self._lock:
            self.charged += actual - reserved


@dataclass(frozen=True)
class ChatAudit:
    config_id: str
    phase: Phase
    status: Literal["ok", "error"]
    error_code: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost: str
    duration_ms: float


class ChatSession:
    def __init__(
        self,
        tools: ToolSession,
        config: ResolvedChatConfig,
        provider: ChatProvider,
        *,
        smoke: SmokeBudget | None = None,
        jitter: Callable[[], float] = random.random,
        reference_context: Callable[[EvidenceSnapshot], dict[str, object]] | None = None,
        answer_validator: Callable[[EvidenceSnapshot, AnswerDraft], None] | None = None,
    ) -> None:
        config = config.verified()
        resolved = config.config
        if provider.model != resolved.model or tools.executor.policy != resolved.tool_policy:
            raise ValueError("chat_provider_or_tool_policy_binding_mismatch")
        if provider.source_kind == "fixture":
            if (
                resolved.model.provider != "fake"
                or tools.executor.environment != "test"
                or not tools.executor.allow_fixtures
            ):
                raise ValueError("fixture_chat_requires_explicit_test_mode")
        elif resolved.model.provider != "bedrock":
            raise ValueError("runtime_chat_requires_real_model_configuration")
        if tools.pin is not None and (
            tools.pin.manifest.index_id != resolved.knowledge_index_id
            or tools.pin.manifest.embedding_config != resolved.embeddings
        ):
            raise ValueError("chat_knowledge_pin_binding_mismatch")
        self.tools = tools
        self.config = config
        self.provider = provider
        self.smoke = smoke or SmokeBudget(resolved.budget.pricing)
        if self.smoke.pricing_id != canonical_sha256(
            resolved.budget.pricing.model_dump(mode="json")
        ):
            raise ValueError("smoke_pricing_binding_mismatch")
        self.jitter = jitter
        self.reference_context = reference_context
        self.answer_validator = answer_validator
        self.calls = 0
        self.retries = 0
        self.repairs = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = Decimal(0)
        self.audit: list[ChatAudit] = []
        self._repair: tuple[str, Literal["tool_plan", "answer"], str] | None = None
        self._lock = asyncio.Lock()
        tools.claim_chat_session()

    def preview(self, phase: Phase, question: str) -> ChatRequest:
        return self._prepare(phase, question)[0]

    def _prepare(self, phase: Phase, question: str) -> tuple[ChatRequest, EvidenceSnapshot]:
        if phase not in {"plan", "synthesize", "repair"}:
            raise ChatFailure("invalid_output")
        if (
            not question.strip()
            or "\0" in question
            or len(question) > 2000
            or len(question.encode()) > 8000
        ):
            raise ChatFailure("invalid_output")
        config = self.config.config
        snapshot = EvidenceSnapshot.build(self.tools.accepted_outputs(), self.config)
        payload = snapshot.payload()
        if self.reference_context is not None:
            payload["server_evidence_policy"] = self.reference_context(snapshot)
        refs = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        repair_code = None
        if phase == "repair":
            if self._repair is None or self.repairs >= config.budget.max_repairs:
                raise ChatFailure("invalid_repair")
            context, kind, repair_code = self._repair
            if context != canonical_sha256({"question": question, "references": refs}):
                raise ChatFailure("invalid_repair")
        else:
            kind = "tool_plan" if phase == "plan" else "answer"
        request = ChatRequest(
            self.config.config_id,
            phase,
            kind,
            tuple(text for _, text in self.config.prompt_texts),
            question,
            tuple(
                cast(ToolName, name)
                for name, cap in READ_CAPABILITIES.items()
                if cap in self.tools.principal.capabilities
            ),
            refs,
            config.model.max_output_tokens,
            repair_code,
        )
        return request, snapshot

    def _reserve(self, request: ChatRequest) -> tuple[int, int, Decimal]:
        budget = self.config.config.budget
        if self.tools.executor.timer() >= self.tools.deadline:
            raise ChatFailure("deadline_exceeded")
        try:
            inputs = self.provider.input_token_bound(request)
        except Exception:
            raise ChatFailure("provider_unavailable") from None
        outputs = request.max_output_tokens
        if type(inputs) is not int or inputs < 1:
            raise ChatFailure("provider_unavailable")
        charge = estimated_cost(budget.pricing, inputs, outputs)
        if (
            self.calls >= budget.max_calls
            or self.input_tokens + inputs > budget.max_input_tokens
            or self.output_tokens + outputs > budget.max_output_tokens
            or self.cost + charge > budget.pricing.max_run_cost
        ):
            raise ChatFailure("budget_exceeded")
        self.smoke.reserve(charge)
        self.calls += 1
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.cost += charge
        return inputs, outputs, charge

    def _settle(self, reserved: tuple[int, int, Decimal], reply: ProviderReply) -> None:
        inputs, outputs, charge = reserved
        if reply.usage.input_tokens > inputs or reply.usage.output_tokens > outputs:
            # Untrustworthy usage cannot make any remaining budget available.
            raise ChatFailure("provider_unavailable")
        actual = estimated_cost(
            self.config.config.budget.pricing, reply.usage.input_tokens, reply.usage.output_tokens
        )
        self.input_tokens += reply.usage.input_tokens - inputs
        self.output_tokens += reply.usage.output_tokens - outputs
        self.cost += actual - charge
        self.smoke.settle(charge, actual)

    async def call(self, phase: Phase, question: str) -> ChatDraft:
        async with self._lock:
            try:
                request, snapshot = self._prepare(phase, question)
            except InvalidEvidence:
                raise ChatFailure("invalid_evidence") from None
            if phase == "repair":
                self.repairs += 1
                self._repair = None
            while True:
                reserved = self._reserve(request)
                started = self.tools.executor.timer()
                accounted = (reserved[0], reserved[1])
                try:
                    reply = await asyncio.wait_for(
                        self.provider.generate(request),
                        timeout=min(
                            self.config.config.budget.provider_timeout_seconds,
                            self.tools.deadline - started,
                        ),
                    )
                    reply = ProviderReply.model_validate_json(reply.model_dump_json())
                    self._settle(reserved, reply)
                    accounted = (reply.usage.input_tokens, reply.usage.output_tokens)
                    if self.tools.executor.timer() >= self.tools.deadline:
                        raise ChatFailure("deadline_exceeded")
                    try:
                        if len(reply.body.encode()) > 131072:
                            raise ValueError("model_response_too_large")
                        strict_json(reply.body.encode())
                        draft = DRAFT.validate_json(reply.body)
                    except (ValueError, RecursionError):
                        raise ChatFailure("invalid_output") from None
                    if draft.kind != request.expected_kind:
                        raise ChatFailure("invalid_output")
                    if isinstance(draft, PlanDraft):
                        if (
                            len(draft.tools) + self.tools.calls
                            > self.config.config.tool_policy.max_calls
                        ):
                            raise ChatFailure("budget_exceeded")
                        try:
                            normalized = [
                                self.tools.validate_call(tool.model_dump_json())
                                for tool in draft.tools
                            ]
                        except ToolFailure:
                            raise ChatFailure("unauthorized_tool") from None
                        draft = PlanDraft(kind="tool_plan", tools=normalized)
                    elif isinstance(draft, AnswerDraft):
                        try:
                            if self.answer_validator is None:
                                snapshot.validate_answer(draft)
                            else:
                                self.answer_validator(snapshot, draft)
                        except InvalidEvidence:
                            raise ChatFailure("invalid_evidence") from None
                    if self.tools.executor.timer() >= self.tools.deadline:
                        raise ChatFailure("deadline_exceeded")
                    self._record(
                        request, started, reply.usage.input_tokens, reply.usage.output_tokens, None
                    )
                    self._repair = None
                    return draft
                except asyncio.CancelledError:
                    self._record(request, started, *accounted, "cancelled")
                    raise
                except ProviderFailure as exc:
                    if self.tools.executor.timer() >= self.tools.deadline:
                        self._record(request, started, *accounted, "deadline_exceeded")
                        raise ChatFailure("deadline_exceeded") from None
                    self._record(request, started, *accounted, "provider_unavailable")
                    if (
                        exc.code not in {"throttled", "transient"}
                        or self.retries >= self.config.config.budget.max_retries
                    ):
                        raise ChatFailure("provider_unavailable") from None
                    remaining = self.tools.deadline - self.tools.executor.timer()
                    base = min(
                        self.config.config.budget.retry_max_seconds,
                        self.config.config.budget.retry_base_seconds * 2**self.retries,
                    )
                    delay = base * (0.5 + 0.5 * max(0.0, min(1.0, self.jitter())))
                    if remaining <= delay:
                        raise ChatFailure("deadline_exceeded") from None
                    self.retries += 1
                    await asyncio.sleep(delay)
                except TimeoutError:
                    code = (
                        "deadline_exceeded"
                        if self.tools.executor.timer() >= self.tools.deadline
                        else "provider_unavailable"
                    )
                    self._record(request, started, *accounted, code)
                    raise ChatFailure(code) from None
                except ChatFailure as exc:
                    self._record(request, started, *accounted, exc.code)
                    if exc.code in {"invalid_output", "invalid_evidence"} and phase != "repair":
                        self._repair = (
                            canonical_sha256(
                                {"question": question, "references": request.references_json}
                            ),
                            request.expected_kind,
                            exc.code,
                        )
                    raise
                except Exception:
                    self._record(request, started, *accounted, "provider_unavailable")
                    raise ChatFailure("provider_unavailable") from None

    def _record(
        self, request: ChatRequest, started: float, inputs: int, outputs: int, error: str | None
    ) -> None:
        self.audit.append(
            ChatAudit(
                self.config.config_id,
                request.phase,
                "error" if error else "ok",
                error,
                inputs,
                outputs,
                str(estimated_cost(self.config.config.budget.pricing, inputs, outputs)),
                max(0.0, (self.tools.executor.timer() - started) * 1000),
            )
        )
