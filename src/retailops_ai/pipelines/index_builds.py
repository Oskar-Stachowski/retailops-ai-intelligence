"""Approved golden snapshots validate before registration and evaluate without promotion."""

import json

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.golden import GoldenSet
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.jobs import (
    BUILD_PROFILE_ADAPTER,
    RUN_REPORT_ADAPTER,
    BuildProfile,
    GoldenIndexBuildProfile,
    RunReport,
)
from retailops_ai.knowledge.qualification import GoldenLabelsApproval, SimilarityReview
from retailops_ai.knowledge.releases import CorpusApproval, IndexValidation
from retailops_ai.knowledge.retrieval import RetrievalConfig
from retailops_ai.knowledge.review import SimilarityPolicy
from retailops_ai.pipelines.golden import evaluate, validate_labels
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.qualification import similarity_findings
from retailops_ai.pipelines.review import review_similarity


def check_build_profile(profile: BuildProfile, candidate: IndexCandidate | None = None) -> None:
    if not isinstance(profile, GoldenIndexBuildProfile):
        return
    candidate = candidate or build_index(profile.chunks, profile.embedding_config)
    validate_labels(candidate, profile.golden_set, profile.retrieval_config)
    report = review_similarity(profile.chunks, profile.similarity_policy)
    if report.report_id != profile.similarity_review.report_id:
        raise ValueError("golden_profile_similarity_snapshot_mismatch")
    if similarity_findings(report) != {d.key() for d in profile.similarity_review.decisions}:
        raise ValueError("golden_profile_similarity_review_incomplete")


def prepare_build_profile(
    candidate: IndexCandidate,
    golden: GoldenSet,
    approval: CorpusApproval,
    labels: GoldenLabelsApproval,
    retrieval: RetrievalConfig,
    policy: SimilarityPolicy,
    review: SimilarityReview,
) -> GoldenIndexBuildProfile:
    value = {
        "schema_version": "1.0",
        "environment": candidate.manifest.environment,
        "approval": approval.model_dump(mode="json"),
        "chunks": candidate.chunks.model_dump(mode="json"),
        "embedding_config": candidate.manifest.embedding_config.model_dump(mode="json"),
        "evaluation_set_id": golden.golden_set_id,
        "golden_set": golden.model_dump(mode="json"),
        "golden_approval": labels.model_dump(mode="json"),
        "retrieval_config": retrieval.model_dump(mode="json"),
        "similarity_policy": policy.model_dump(mode="json"),
        "similarity_review": review.model_dump(mode="json"),
        "purpose": "approved_corpus_fake_golden_validation",
    }
    value["profile_id"] = "index-build-profile-sha256-" + canonical_sha256(value)
    profile = GoldenIndexBuildProfile.model_validate_json(json.dumps(value))
    # Rebuild rather than trust a supplied candidate with valid but different vectors.
    check_build_profile(profile)
    return profile


def build_run_report(
    profile: BuildProfile, candidate: IndexCandidate, validation: IndexValidation
) -> RunReport:
    profile = BUILD_PROFILE_ADAPTER.validate_json(profile.model_dump_json())
    check_build_profile(profile, candidate)
    value = {
        "schema_version": "1.0",
        "profile_id": profile.profile_id,
        "validation": validation.model_dump(mode="json"),
        "purpose": profile.purpose,
        "activation_allowed": False,
    }
    if isinstance(profile, GoldenIndexBuildProfile):
        report = evaluate(candidate, profile.golden_set, profile.retrieval_config)
        value.update(
            approval=profile.approval.model_dump(mode="json"),
            golden_approval=profile.golden_approval.model_dump(mode="json"),
            golden=report.model_dump(mode="json"),
            similarity_report_id=profile.similarity_review.report_id,
            similarity_review_id=profile.similarity_review.review_id,
            quality_gate_passed=validation.result == "passed" and report.measured_thresholds_passed,
        )
    value["report_id"] = "index-run-report-sha256-" + canonical_sha256(value)
    return RUN_REPORT_ADAPTER.validate_json(json.dumps(value))
