"""Versioned section labels and frozen release thresholds, never self-labelled by ranking."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Role
from retailops_ai.knowledge.contracts import DocumentStatus, Repository, document_path
from retailops_ai.knowledge.indexes import IndexID
from retailops_ai.knowledge.retrieval import RetrievalConfigID, RetrievalRequest
from retailops_ai.security.models import KnowledgeResourceScope

GoldenID = Annotated[str, Field(pattern=r"^golden-set-sha256-[0-9a-f]{64}$")]


class ExpectedSection(Contract):
    repository: Repository
    path: str
    heading_path: Annotated[tuple[str, ...], Field(min_length=1, max_length=6)]
    document_status: DocumentStatus

    @field_validator("path")
    @classmethod
    def path_safe(cls, value: str) -> str:
        return document_path(value)


class GoldenCase(Contract):
    case_id: Symbol
    category: Literal[
        "documentation", "models", "operations", "missing", "conflict", "injection", "authorization"
    ]
    request: RetrievalRequest
    role: Role
    scope: KnowledgeResourceScope | None
    expected_sections: Annotated[tuple[ExpectedSection, ...], Field(max_length=8)]
    forbidden_sources: Annotated[tuple[ExpectedSection, ...], Field(max_length=8)]
    answerability: Literal["answerable", "not_answerable", "conflict", "refused"]
    acceptable_outcomes: Annotated[
        tuple[Literal["ok", "insufficient_evidence", "forbidden"], ...],
        Field(min_length=1, max_length=3),
    ]
    required_tools: Annotated[tuple[Symbol, ...], Field(max_length=8)]
    forbidden_tools: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=8)]
    critical: bool

    @model_validator(mode="after")
    def labels(self) -> Self:
        if self.answerability in {"answerable", "conflict"} and not self.expected_sections:
            raise ValueError("golden_positive_labels_required")
        if set(self.required_tools) & set(self.forbidden_tools):
            raise ValueError("golden_tool_labels_conflict")
        expected = {s.model_dump_json() for s in self.expected_sections}
        forbidden = {s.model_dump_json() for s in self.forbidden_sources}
        if len(expected) != len(self.expected_sections) or len(forbidden) != len(
            self.forbidden_sources
        ):
            raise ValueError("golden_source_labels_duplicate")
        if expected & forbidden:
            raise ValueError("golden_source_labels_conflict")
        return self


class GoldenThresholds(Contract):
    recall_at_5_min: Annotated[float, Field(ge=0, le=1)]
    mrr_min: Annotated[float, Field(ge=0, le=1)]
    citation_correctness_min: Annotated[float, Field(ge=1, le=1)]
    critical_pass_rate_min: Annotated[float, Field(ge=1, le=1)]
    groundedness_min: Annotated[float, Field(ge=0, le=1)]
    latency_p95_ms_max: Annotated[float, Field(gt=0, le=5000)]
    offline_cost_usd_max: Annotated[float, Field(ge=0, le=0)]


class GoldenSet(Contract):
    schema_version: Literal["1.0"]
    set_version: Literal["retailops-rag-golden-v1"]
    golden_set_id: GoldenID
    index_id: IndexID
    retrieval_config_id: RetrievalConfigID
    review_state: Literal["proposed"]
    review_owner: Symbol
    labels_origin: Literal["manually_authored_source_sections_not_ranker_output"]
    thresholds: GoldenThresholds
    cases: Annotated[tuple[GoldenCase, ...], Field(min_length=30, max_length=50)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.golden_set_id != "golden-set-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"golden_set_id"})
        ):
            raise ValueError("golden_set_identity_mismatch")
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("golden_case_identity_duplicate")
        if {c.category for c in self.cases} != {
            "documentation",
            "models",
            "operations",
            "missing",
            "conflict",
            "injection",
            "authorization",
        }:
            raise ValueError("golden_category_coverage_missing")
        return self


class CaseEvaluation(Contract):
    case_id: Symbol
    outcome: Literal["ok", "insufficient_evidence", "forbidden"]
    recall_at_5: Annotated[float, Field(ge=0, le=1)] | None
    reciprocal_rank: Annotated[float, Field(ge=0, le=1)] | None
    forbidden_sources_absent: bool
    expected_outcome: bool
    citations_bound: bool
    critical: bool
    passed: bool
    retrieved_chunk_ids: tuple[str, ...]
    elapsed_ms: Annotated[float, Field(ge=0)]


class GoldenReport(Contract):
    schema_version: Literal["1.0"] = "1.0"
    golden_set_id: GoldenID
    index_id: IndexID
    retrieval_config_id: RetrievalConfigID
    provider: Literal["fake", "bedrock"] = "fake"
    report_kind: Literal[
        "offline_mechanics_with_draft_semantic_labels", "semantic_retrieval_evaluation"
    ] = "offline_mechanics_with_draft_semantic_labels"
    semantic_quality: Literal["not_evaluated_fake_vectors", "measured_real_vectors"] = (
        "not_evaluated_fake_vectors"
    )
    corpus_approved: FalseFlag = False
    labels_approved: FalseFlag = False
    activation_allowed: FalseFlag = False
    answer_groundedness: None = None
    agent_tool_evaluation: Literal["labels_only_no_agent_runtime"] = "labels_only_no_agent_runtime"
    thresholds: GoldenThresholds
    cases: tuple[CaseEvaluation, ...]
    recall_at_5: float
    mrr: float
    citation_correctness: float
    critical_pass_rate: float
    latency_p95_ms: float
    model_cost_usd: Annotated[float, Field(ge=0, le=0)] = 0.0
    measured_thresholds_passed: bool

    @model_validator(mode="after")
    def provider_binding(self) -> Self:
        expected = (
            ("offline_mechanics_with_draft_semantic_labels", "not_evaluated_fake_vectors")
            if self.provider == "fake"
            else ("semantic_retrieval_evaluation", "measured_real_vectors")
        )
        if (self.report_kind, self.semantic_quality) != expected:
            raise ValueError("golden_report_provider_mismatch")
        return self
