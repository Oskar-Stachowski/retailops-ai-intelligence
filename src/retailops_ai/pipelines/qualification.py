"""Recheck pinned fake artifacts and enumerate release blockers without activating."""

import json
import math

from retailops_ai.adapters.embeddings import EmbeddingProvider
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenReport, GoldenSet
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.qualification import (
    GoldenLabelsApproval,
    IndexReleaseManifest,
    SimilarityReview,
    release_blockers,
)
from retailops_ai.knowledge.releases import CorpusApproval
from retailops_ai.knowledge.retrieval import RetrievalConfig
from retailops_ai.knowledge.review import SimilarityPolicy, SimilarityReport
from retailops_ai.pipelines.golden import evaluate
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.pipelines.review import review_similarity


def similarity_findings(report: SimilarityReport) -> set[tuple[str, str, str, str]]:
    findings: set[tuple[str, str, str, str]] = set()
    for analysis in (report.documents, report.chunks):
        members = {m.unit_id: m for m in analysis.members}
        for group in analysis.content_groups:
            if group.utf8_bytes > 0 and len(group.member_ids) > 1:
                findings.add((analysis.level, "exact", group.text_checksum, ""))
            if analysis.level == "chunk" and any(
                len(members[uid].source_refs) > 1 for uid in group.member_ids
            ):
                findings.add(("chunk", "repeated_occurrence", group.text_checksum, ""))
        for pair in analysis.near_pairs:
            findings.add((analysis.level, "near", pair.left_checksum, pair.right_checksum))
    if len(findings) > 10000:
        raise ValueError("release_similarity_decision_budget_exceeded")
    return findings


def check_golden_report(
    candidate: IndexCandidate,
    golden: GoldenSet,
    config: RetrievalConfig,
    report: GoldenReport,
    *,
    provider: EmbeddingProvider | None = None,
) -> None:
    actual = evaluate(candidate, golden, config, provider=provider)

    def semantic_fields(value: GoldenReport) -> dict[str, object]:
        body = value.model_dump(
            mode="json", exclude={"latency_p95_ms", "measured_thresholds_passed"}
        )
        body["cases"] = [c.model_dump(mode="json", exclude={"elapsed_ms"}) for c in value.cases]
        return body

    if semantic_fields(actual) != semantic_fields(report):
        raise ValueError("release_golden_report_reproduction_mismatch")
    p95 = sorted(c.elapsed_ms for c in report.cases)[math.ceil(len(report.cases) * 0.95) - 1]
    t = golden.thresholds
    passed = (
        report.recall_at_5 >= t.recall_at_5_min
        and report.mrr >= t.mrr_min
        and report.citation_correctness >= t.citation_correctness_min
        and report.critical_pass_rate >= t.critical_pass_rate_min
        and p95 <= t.latency_p95_ms_max
    )
    if report.latency_p95_ms != p95 or report.measured_thresholds_passed != passed:
        raise ValueError("release_golden_timing_or_gate_mismatch")


def prepare_release(
    candidate: IndexCandidate,
    golden: GoldenSet,
    config: RetrievalConfig,
    report: GoldenReport,
    policy: SimilarityPolicy,
    *,
    corpus_approval: CorpusApproval | None = None,
    golden_approval: GoldenLabelsApproval | None = None,
    similarity_review: SimilarityReview | None = None,
    provider: EmbeddingProvider | None = None,
) -> IndexReleaseManifest:
    candidate = IndexCandidate.model_validate_json(candidate.model_dump_json())
    check_golden_report(candidate, golden, config, report, provider=provider)
    validation = validate_candidate(candidate)
    similarity = review_similarity(candidate.chunks, policy)
    findings = similarity_findings(similarity)
    reviewed: set[tuple[str, str, str, str]] = set()
    if similarity_review is not None:
        if (
            similarity_review.report_id != similarity.report_id
            or similarity_review.review_owner != candidate.chunks.corpus.review_owner
        ):
            raise ValueError("release_similarity_review_mismatch")
        reviewed = {d.key() for d in similarity_review.decisions}
        if reviewed - findings:
            raise ValueError("release_similarity_decision_not_found")
    unreviewed = len(findings - reviewed)
    blockers = release_blockers(corpus_approval, golden_approval, validation, report, unreviewed)
    value = {
        "schema_version": "1.0",
        "policy_version": "fake-release-preflight-v1"
        if validation.provider == "fake"
        else "semantic-release-v1",
        "purpose": "release_readiness_only",
        "status": "blocked" if blockers else "ready",
        "activation_allowed": not blockers,
        "index_manifest": candidate.manifest.model_dump(mode="json"),
        "corpus_manifest": candidate.chunks.corpus.model_dump(mode="json"),
        "retrieval_config": config.model_dump(mode="json"),
        "golden_set": golden.model_dump(mode="json"),
        "golden_report": report.model_dump(mode="json"),
        "mechanical_validation": validation.model_dump(mode="json"),
        "corpus_approval": corpus_approval.model_dump(mode="json") if corpus_approval else None,
        "golden_approval": golden_approval.model_dump(mode="json") if golden_approval else None,
        "similarity_report_id": similarity.report_id,
        "similarity_review": similarity_review.model_dump(mode="json")
        if similarity_review
        else None,
        "similarity_findings": len(findings),
        "unreviewed_similarity_findings": unreviewed,
        "blockers": release_blockers(
            corpus_approval, golden_approval, validation, report, unreviewed
        ),
    }
    value["release_id"] = "rag-release-manifest-sha256-" + canonical_sha256(value)
    return IndexReleaseManifest.model_validate_json(json.dumps(value))
