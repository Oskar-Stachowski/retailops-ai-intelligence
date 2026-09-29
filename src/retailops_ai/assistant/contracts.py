"""Exact MVP wire shapes; no caller-selected principal, intent, model or tool."""

import json
import re
from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BeforeValidator, Field, model_validator

from retailops_ai.agent.chat_contracts import (
    AnswerDraft,
    AnswerFreshness,
    DraftAction,
    DraftCitation,
    EvidenceClaim,
    ShortText,
)
from retailops_ai.agent.graph_contracts import GraphCode, NodeAudit, SafeToolAudit
from retailops_ai.agent.suggestions import SuggestionCandidate
from retailops_ai.data_contracts.common import Contract, Symbol, UtcTime


def uuid_input(value: object) -> object:
    if isinstance(value, str):
        parsed = UUID(value)
        if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
            raise ValueError("canonical_uuid_required")
        return parsed
    return value


def date_input(value: object) -> object:
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("date_required")
        return date.fromisoformat(value)
    return value


WireUUID = Annotated[UUID, BeforeValidator(uuid_input)]
WireDate = Annotated[date, BeforeValidator(date_input)]


class QueryScope(Contract):
    product_ids: list[WireUUID] = Field(min_length=1, max_length=20)
    store_ids: list[WireUUID] = Field(min_length=1, max_length=5)
    from_: WireDate = Field(alias="from")
    to: WireDate

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if not 0 <= (self.to - self.from_).days < 90:
            raise ValueError("period_outside_budget")
        if len(set(self.product_ids)) != len(self.product_ids) or len(set(self.store_ids)) != len(
            self.store_ids
        ):
            raise ValueError("duplicate_scope_id")
        return self


class AssistantQuery(Contract):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    scope: QueryScope
    conversation_id: None = None

    @model_validator(mode="after")
    def nonblank(self) -> Self:
        if not self.question.strip() or "\0" in self.question or len(self.question.encode()) > 8000:
            raise ValueError("invalid_question")
        return self


class RecommendedAction(DraftAction):
    recommendation_id: WireUUID


class AssistantAnswer(Contract):
    outcome: Literal["answered", "insufficient_evidence", "refused"]
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[EvidenceClaim] = Field(max_length=20)
    recommended_actions: list[RecommendedAction] = Field(max_length=5)
    confidence: Literal["low", "medium", "high"]
    data_freshness: AnswerFreshness
    citations: list[DraftCitation] = Field(max_length=5)
    limitations: list[ShortText] = Field(max_length=10)
    answer_id: WireUUID
    trace_id: WireUUID
    agent_config_version: Symbol
    index_id: str
    created_at: UtcTime

    @model_validator(mode="after")
    def draft_shape(self) -> Self:
        draft = self.model_dump(
            mode="json",
            exclude={"answer_id", "trace_id", "agent_config_version", "index_id", "created_at"},
        )
        draft["kind"] = "answer"
        draft["recommended_actions"] = [
            action.model_dump(mode="json", exclude={"recommendation_id"})
            for action in self.recommended_actions
        ]
        AnswerDraft.model_validate_json(json.dumps(draft))
        if len({item.recommendation_id for item in self.recommended_actions}) != len(
            self.recommended_actions
        ):
            raise ValueError("duplicate_recommendation_id")
        return self


class TraceNode(Contract):
    name: str = Field(pattern=r"^[a-z_]{1,32}$")
    status: Literal["ok", "error"]
    duration_ms: float = Field(ge=0)
    error_code: GraphCode | None

    @classmethod
    def from_audit(cls, audit: NodeAudit) -> "TraceNode":
        return cls(
            name=audit.node,
            status=audit.status,
            duration_ms=audit.duration_ms,
            error_code=audit.error_code,
        )


class TraceUsage(Contract):
    input_tokens: int = Field(ge=0, le=12000)
    output_tokens: int = Field(ge=0, le=1500)
    duration_ms: float = Field(ge=0)
    estimated_cost: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?(?:E-[0-9]+)?$")
    currency: Literal["USD"] = "USD"


class AssistantRun(Contract):
    trace_id: WireUUID
    answer_id: WireUUID | None
    status: Literal["running", "succeeded", "failed"]
    outcome: Literal["answered", "insufficient_evidence", "refused"] | None
    requested_at: UtcTime
    completed_at: UtcTime | None
    agent_config_version: Symbol
    index_id: str
    nodes: list[TraceNode] = Field(max_length=20)
    tools: list[SafeToolAudit] = Field(max_length=6)
    usage: TraceUsage
    error_code: GraphCode | None

    @model_validator(mode="after")
    def lifecycle(self) -> Self:
        if (self.status == "running") != (self.completed_at is None):
            raise ValueError("run_completion_mismatch")
        if self.completed_at is not None and self.completed_at < self.requested_at:
            raise ValueError("run_time_order")
        if (self.status == "succeeded") != (
            self.answer_id is not None and self.outcome is not None and self.error_code is None
        ):
            raise ValueError("run_answer_mismatch")
        if self.status != "succeeded" and (self.answer_id is not None or self.outcome is not None):
            raise ValueError("failed_or_running_has_no_answer")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("run_error_mismatch")
        return self


class PersistedSuggestion(SuggestionCandidate):
    recommendation_id: WireUUID
    trace_id: WireUUID
    answer_id: WireUUID
    created_at: UtcTime
    origin: Literal["retailops-ai"] = "retailops-ai"
    store_id: WireUUID
    summary: str = Field(min_length=1, max_length=4000)
    agent_config_version: Symbol

    # The candidate's immutable identity is checked before this envelope is added.
    @model_validator(mode="after")
    def identity(self) -> Self:
        base = {name: getattr(self, name) for name in SuggestionCandidate.model_fields}
        SuggestionCandidate.model_validate(base)
        if str(self.store_id) != self.selling_location_id or self.created_at >= self.expires_at:
            raise ValueError("persisted_candidate_scope_or_expiry")
        return self
