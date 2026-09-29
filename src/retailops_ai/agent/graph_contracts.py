"""Closed local graph inputs and bounded, safe metadata; not the Assistant HTTP API."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.agent.chat_contracts import AnswerDraft
from retailops_ai.agent.tools import DataScope
from retailops_ai.data_contracts.common import (
    Contract,
    DateWindow,
    Sha256,
    Symbol,
    UtcTime,
    Versioned,
)

Intent = Literal[
    "sales",
    "sales_comparison",
    "inventory",
    "forecast",
    "risk",
    "anomalies",
    "operations",
    "model",
    "documentation",
    "verified_state",
    "investigation",
    "refuse",
]
GraphCode = Literal[
    "invalid_scope",
    "unauthorized",
    "dependency_unavailable",
    "budget_exceeded",
    "deadline_exceeded",
    "provider_unavailable",
    "invalid_output",
    "invalid_evidence",
    "unauthorized_tool",
    "invalid_repair",
    "trace_unavailable",
    "cancelled",
]


class GraphRequest(Versioned):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    intent: Intent
    scope: DataScope
    as_of: UtcTime
    window: DateWindow
    comparison_window: DateWindow | None
    limit: Annotated[int, Field(ge=1, le=20)] = 5

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not self.question.strip() or "\0" in self.question or len(self.question.encode()) > 8000:
            raise ValueError("invalid_graph_question")
        if (self.window.end - self.window.start).days >= 90:
            raise ValueError("graph_period_outside_budget")
        if self.intent in {"forecast", "risk"}:
            if (
                not self.as_of.date()
                < self.window.start
                <= self.window.end
                <= self.as_of.date() + timedelta(days=14)
            ):
                raise ValueError("graph_horizon_outside_budget")
        elif self.window.end > self.as_of.date():
            raise ValueError("graph_observation_window_in_future")
        if self.intent == "sales_comparison":
            previous = self.comparison_window
            if (
                previous is None
                or previous.end >= self.window.start
                or previous.end - previous.start != self.window.end - self.window.start
            ):
                raise ValueError("comparison_requires_distinct_equal_length_periods")
        elif self.comparison_window is not None:
            raise ValueError("unexpected_comparison_window")
        return self


class GraphPolicy(Versioned):
    profile: Literal["bounded-evidence-graph-v1"]
    max_steps: Annotated[int, Field(ge=15, le=20)] = 20
    max_extra_evidence_rounds: Literal[1] = 1
    max_selected_facts: Annotated[int, Field(ge=1, le=5)] = 5
    max_catalogue_facts: Annotated[int, Field(ge=5, le=40)] = 40
    trace_retention_seconds: Annotated[int, Field(ge=1, le=900)] = 900
    trace_capacity: Annotated[int, Field(ge=1, le=100)] = 100


class NodeAudit(Contract):
    node: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
    status: Literal["ok", "error"]
    error_code: GraphCode | None
    duration_ms: Annotated[float, Field(ge=0)]


class SafeTrace(Versioned):
    trace_id: Annotated[str, Field(pattern=r"^trace-[0-9a-f]{32}$")]
    correlation_id: Annotated[str, Field(pattern=r"^correlation-[0-9a-f]{32}$")]
    owner_id: Symbol
    scope: DataScope
    config_id: Annotated[str, Field(pattern=r"^agent-graph-config-sha256-[0-9a-f]{64}$")]
    chat_config_id: Annotated[str, Field(pattern=r"^agent-chat-config-sha256-[0-9a-f]{64}$")]
    request_sha256: Sha256
    index_id: str | None
    release_refs: list[str] = Field(max_length=100)
    source_refs: list[str] = Field(max_length=100)
    release_refs_total: Annotated[int, Field(ge=0)]
    source_refs_total: Annotated[int, Field(ge=0)]
    status: Literal["succeeded", "failed"]
    error_code: GraphCode | None
    nodes: list[NodeAudit] = Field(max_length=20)
    tool_calls: Annotated[int, Field(ge=0, le=6)]
    model_calls: Annotated[int, Field(ge=0, le=6)]
    extra_evidence_rounds: Annotated[int, Field(ge=0, le=1)]
    repairs: Annotated[int, Field(ge=0, le=1)]
    input_tokens: Annotated[int, Field(ge=0, le=12000)]
    output_tokens: Annotated[int, Field(ge=0, le=1500)]
    estimated_cost: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?(?:E-[0-9]+)?$")]
    fixture_only: bool
    created_at: UtcTime


class GraphResult(Contract):
    status: Literal["succeeded", "failed"]
    error_code: GraphCode | None
    answer: AnswerDraft | None
    trace: SafeTrace | None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if (self.status == "succeeded") != (self.answer is not None and self.error_code is None):
            raise ValueError("graph_result_outcome_mismatch")
        if self.status == "failed" and (self.answer is not None or self.error_code is None):
            raise ValueError("failed_graph_requires_canonical_error")
        return self
