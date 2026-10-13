"""Reviewed offline fixture labels are separate from runtime evidence and model scripts."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.agent.chat_contracts import AnswerDraft
from retailops_ai.agent.graph_contracts import GraphCode
from retailops_ai.agent.tools import ToolInput, ToolOutput
from retailops_ai.data_contracts.common import Contract, Sha256, Symbol, Versioned
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.releases import IndexPin


class ToolFixture(Contract):
    request: ToolInput
    result: ToolOutput


class ChatFixture(Contract):
    expected_kind: Literal["tool_plan", "answer"]
    event: Literal["reply", "throttled", "transient", "auth", "schema"] = "reply"
    body: Annotated[str, Field(max_length=131072)]


class ExpectedAnswer(Contract):
    status: Literal["succeeded", "failed"]
    error_code: GraphCode | None
    answer: AnswerDraft | None
    candidate_types: list[
        Literal["review_replenishment", "investigate_anomaly", "refresh_source_data"]
    ] = Field(max_length=5)
    tool_calls: list[ToolInput] = Field(max_length=6)
    model_calls: Annotated[int, Field(ge=0, le=6)]
    tool_attempts: Annotated[int, Field(ge=0, le=6)]
    extra_evidence_rounds: Annotated[int, Field(ge=0, le=1)] = 0
    repairs: Annotated[int, Field(ge=0, le=1)] = 0

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.status == "succeeded") != (self.answer is not None and self.error_code is None):
            raise ValueError("invalid_expected_result")
        if self.status == "failed" and (self.answer is not None or self.error_code is None):
            raise ValueError("invalid_expected_error")
        return self


class GoldenCase(Contract):
    case_id: Symbol
    category: Literal["business", "suggestions", "documentation", "safety", "degradation"]
    critical: bool
    rag_case_id: Symbol | None
    request_json: Annotated[str, Field(min_length=1, max_length=16384)]
    access: Literal[
        "operator", "missing_credentials", "viewer", "foreign_scope", "no_source_capability"
    ]
    decision_time: Annotated[str, Field(pattern=r"^2026-08-23T00:00:00Z$")]
    tools: list[ToolFixture] = Field(max_length=6)
    script: list[ChatFixture] = Field(max_length=6)
    expected: ExpectedAnswer


class EvaluationThresholds(Contract):
    deterministic_rate_min: Annotated[float, Field(ge=1, le=1)] = 1.0
    critical_rate_min: Annotated[float, Field(ge=1, le=1)] = 1.0
    unnecessary_calls_max: Literal[0] = 0
    latency_p95_ms_max: Annotated[float, Field(gt=0, le=45000)] = 5000.0
    estimated_cost_usd_max: Literal["0"] = "0"


class AgentGoldenSet(Versioned):
    set_version: Literal["agent-canonical-golden-v2"]
    provider: Literal["fake"]
    fixture_only: Literal[True]
    labels_state: Literal["proposed"]
    labels_origin: Literal["authored_oracles_not_runtime_policy_or_ranker_output"]
    rag_set_sha256: Sha256
    pin: IndexPin
    thresholds: EvaluationThresholds
    cases: list[GoldenCase] = Field(min_length=30, max_length=50)

    @model_validator(mode="after")
    def closed(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate_agent_golden_case")
        if not any(case.critical for case in self.cases):
            raise ValueError("golden_requires_critical_cases")
        if {case.category for case in self.cases} != {
            "business",
            "suggestions",
            "documentation",
            "safety",
            "degradation",
        }:
            raise ValueError("golden_requires_all_categories")
        return self


class AgentEvaluationRelease(Versioned):
    release_id: Annotated[str, Field(pattern=r"^agent-evaluation-release-sha256-[0-9a-f]{64}$")]
    graph_config_id: Annotated[str, Field(pattern=r"^agent-graph-config-sha256-[0-9a-f]{64}$")]
    golden_sha256: Sha256
    evaluator_sha256: Sha256
    dependency_lock_sha256: Sha256
    set_version: Literal["agent-canonical-golden-v2"]
    provider: Literal["fake"]
    fixture_only: Literal[True]
    acceptance_scope: Literal["canonical_fixture_invariants_only"]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.release_id != "agent-evaluation-release-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"release_id"})
        ):
            raise ValueError("evaluation_release_identity_mismatch")
        return self


class Rate(Contract):
    passed: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    value: Annotated[float, Field(ge=0, le=1)] | None

    @model_validator(mode="after")
    def ratio(self) -> Self:
        expected = self.passed / self.total if self.total else None
        if self.passed > self.total or self.value != expected:
            raise ValueError("evaluation_rate_mismatch")
        return self


class CaseEvaluation(Contract):
    case_id: Symbol
    critical: bool
    passed: bool
    failed_checks: list[Symbol]
    duration_ms: Annotated[float, Field(ge=0)]
    tool_calls: Annotated[int, Field(ge=0, le=6)]
    model_calls: Annotated[int, Field(ge=0, le=6)]
    input_tokens: Annotated[int, Field(ge=0, le=16000)]
    output_tokens: Annotated[int, Field(ge=0, le=3000)]
    estimated_cost: str


class AgentEvaluationReport(Versioned):
    status: Literal["passed", "failed"]
    release_id: str
    graph_config_id: str
    golden_sha256: Sha256
    evaluator_sha256: Sha256
    provider: Literal["fake"]
    fixture_only: Literal[True]
    aws_executed: Literal[False]
    rag_quality_remeasured: Literal[False]
    labels_state: Literal["proposed"]
    acceptance_scope: Literal["canonical_fixture_invariants_only"]
    metrics: dict[Symbol, Rate]
    unnecessary_tool_calls: Annotated[int, Field(ge=0)]
    latency_p95_ms: Annotated[float, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    estimated_cost_usd: str
    failed_gates: list[Symbol]
    cases: list[CaseEvaluation] = Field(min_length=30, max_length=50)
