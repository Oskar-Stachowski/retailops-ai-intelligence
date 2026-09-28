"""Evaluate against frozen source labels; fake reports never enable activation."""

import math
import time
from pathlib import Path
from typing import Literal

from retailops_ai.adapters.embeddings import EmbeddingProvider
from retailops_ai.domain.access import KnowledgeAccess, Principal
from retailops_ai.knowledge.chunks import MarkdownChunk
from retailops_ai.knowledge.golden import (
    CaseEvaluation,
    ExpectedSection,
    GoldenCase,
    GoldenReport,
    GoldenSet,
)
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.retrieval import RetrievalConfig
from retailops_ai.pipelines.releases import load_release_document
from retailops_ai.pipelines.retrieval import (
    KnowledgeDenied,
    allowed,
    resolve_scope,
    search_candidate,
)


def case_principal(case: GoldenCase) -> Principal:
    scope = case.scope
    return Principal(
        case.case_id,
        frozenset({case.role}),
        frozenset({"knowledge:read"}) if scope else frozenset(),
        frozenset(),
        frozenset(),
        frozenset(),
        KnowledgeAccess(
            scope.environment,
            frozenset(scope.repositories),
            frozenset(scope.access_classes),
            frozenset(scope.document_statuses),
        )
        if scope
        else None,
    )


def matches(chunk: MarkdownChunk, section: ExpectedSection) -> bool:
    titles = tuple(h.title for h in chunk.heading_path)
    return (chunk.repository, chunk.path, chunk.document_status) == (
        section.repository,
        section.path,
        section.document_status,
    ) and titles[: len(section.heading_path)] == section.heading_path


def validate_labels(candidate: IndexCandidate, golden: GoldenSet, config: RetrievalConfig) -> None:
    if (
        golden.index_id != candidate.manifest.index_id
        or golden.retrieval_config_id != config.config_id()
    ):
        raise ValueError("golden_candidate_configuration_mismatch")
    for case in golden.cases:
        for forbidden in case.forbidden_sources:
            if not any(matches(c, forbidden) for c in candidate.chunks.chunks):
                raise ValueError("golden_forbidden_section_missing")
        for expected in case.expected_sections:
            if not any(matches(c, expected) for c in candidate.chunks.chunks):
                raise ValueError("golden_label_section_missing")
            scope = case.scope
            if (
                scope is None
                or scope.environment != candidate.manifest.environment
                or expected.repository not in scope.repositories
                or expected.document_status not in scope.document_statuses
            ):
                raise ValueError("golden_positive_scope_mismatch")
            resolved = resolve_scope(
                case_principal(case), case.request, candidate.manifest.environment
            )
            if not any(
                matches(c, expected) and allowed(c, resolved, frozenset())
                for c in candidate.chunks.chunks
            ):
                raise ValueError("golden_positive_filter_mismatch")


def evaluate(
    candidate: IndexCandidate,
    golden: GoldenSet,
    config: RetrievalConfig,
    *,
    provider: EmbeddingProvider | None = None,
) -> GoldenReport:
    validate_labels(candidate, golden, config)
    original = {c.chunk_id: c for c in candidate.chunks.chunks}
    cases = []
    bound_citations = 0
    total_citations = 0
    for case in golden.cases:
        principal = case_principal(case)
        start = time.monotonic()
        outcome: Literal["ok", "insufficient_evidence", "forbidden"]
        try:
            result = search_candidate(candidate, case.request, principal, config, provider=provider)
            hits = list(result.items)
            outcome = result.status
        except KnowledgeDenied:
            hits = []
            outcome = "forbidden"
        elapsed = (time.monotonic() - start) * 1000
        relevant = [any(matches(h.chunk, s) for s in case.expected_sections) for h in hits]
        recall = (
            sum(any(matches(h.chunk, s) for h in hits) for s in case.expected_sections)
            / len(case.expected_sections)
            if case.expected_sections
            else None
        )
        reciprocal = (
            next((1.0 / (i + 1) for i, yes in enumerate(relevant) if yes), 0.0)
            if case.expected_sections
            else None
        )
        forbidden = not any(matches(h.chunk, s) for h in hits for s in case.forbidden_sources)
        citations = all(original.get(h.chunk.chunk_id) == h.chunk for h in hits)
        bound_citations += sum(original.get(h.chunk.chunk_id) == h.chunk for h in hits)
        total_citations += len(hits)
        expected_outcome = outcome in case.acceptable_outcomes
        passed = forbidden and citations and expected_outcome and (recall is None or recall == 1.0)
        cases.append(
            CaseEvaluation(
                case_id=case.case_id,
                outcome=outcome,
                recall_at_5=recall,
                reciprocal_rank=reciprocal,
                forbidden_sources_absent=forbidden,
                expected_outcome=expected_outcome,
                citations_bound=citations,
                critical=case.critical,
                passed=passed,
                retrieved_chunk_ids=tuple(h.chunk.chunk_id for h in hits),
                elapsed_ms=elapsed,
            )
        )
    recalls = [c.recall_at_5 for c in cases if c.recall_at_5 is not None]
    ranks = [c.reciprocal_rank for c in cases if c.reciprocal_rank is not None]
    critical = [c for c in cases if c.critical]
    recall_avg = sum(recalls) / len(recalls) if recalls else 0.0
    mrr = sum(ranks) / len(ranks) if ranks else 0.0
    citation_rate = bound_citations / total_citations if total_citations else 1.0
    critical_rate = sum(c.passed for c in critical) / len(critical) if critical else 0.0
    p95 = sorted(c.elapsed_ms for c in cases)[math.ceil(len(cases) * 0.95) - 1]
    t = golden.thresholds
    measured = (
        recall_avg >= t.recall_at_5_min
        and mrr >= t.mrr_min
        and citation_rate >= t.citation_correctness_min
        and critical_rate >= t.critical_pass_rate_min
        and p95 <= t.latency_p95_ms_max
    )
    return GoldenReport(
        provider=candidate.manifest.embedding_config.provider,
        report_kind="offline_mechanics_with_draft_semantic_labels"
        if candidate.manifest.embedding_config.provider == "fake"
        else "semantic_retrieval_evaluation",
        semantic_quality="not_evaluated_fake_vectors"
        if candidate.manifest.embedding_config.provider == "fake"
        else "measured_real_vectors",
        golden_set_id=golden.golden_set_id,
        index_id=candidate.manifest.index_id,
        retrieval_config_id=config.config_id(),
        thresholds=t,
        cases=tuple(cases),
        recall_at_5=recall_avg,
        mrr=mrr,
        citation_correctness=citation_rate,
        critical_pass_rate=critical_rate,
        latency_p95_ms=p95,
        measured_thresholds_passed=measured,
    )


def load_golden_set(path: Path) -> GoldenSet:
    return load_release_document(path, GoldenSet)
