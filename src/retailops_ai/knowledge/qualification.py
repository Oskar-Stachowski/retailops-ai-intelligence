"""Bound decisions and a blocked fake release preflight, never a promotion token."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.contracts import CorpusManifest
from retailops_ai.knowledge.golden import GoldenID, GoldenReport, GoldenSet
from retailops_ai.knowledge.indexes import IndexID, IndexManifest
from retailops_ai.knowledge.releases import CorpusApproval, IndexValidation, approval_matches
from retailops_ai.knowledge.retrieval import RetrievalConfig, RetrievalConfigID

GoldenApprovalID = Annotated[str, Field(pattern=r"^golden-approval-sha256-[0-9a-f]{64}$")]
SimilarityReportID = Annotated[str, Field(pattern=r"^similarity-report-sha256-[0-9a-f]{64}$")]
SimilarityReviewID = Annotated[str, Field(pattern=r"^similarity-review-sha256-[0-9a-f]{64}$")]
ReleaseManifestID = Annotated[str, Field(pattern=r"^rag-release-manifest-sha256-[0-9a-f]{64}$")]
Blocker = Literal[
    "corpus_approval_missing",
    "golden_labels_approval_missing",
    "mechanical_validation_failed",
    "similarity_review_incomplete",
    "golden_thresholds_failed",
    "semantic_provider_required",
    "user_build_profile_required",
]


class GoldenLabelsApproval(Contract):
    schema_version: Literal["1.0"]
    approval_id: GoldenApprovalID
    golden_set_id: GoldenID
    index_id: IndexID
    retrieval_config_id: RetrievalConfigID
    environment: Literal["local", "test"]
    review_owner: Symbol
    reviewer: Symbol
    reviewer_kind: Literal["human", "approved_pipeline"]
    decision: Literal["approved"]
    scope: Literal["labels_tools_and_frozen_thresholds"]
    reviewed_at: UtcTime

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.approval_id != "golden-approval-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"approval_id"})
        ):
            raise ValueError("golden_approval_identity_mismatch")
        if self.reviewer_kind == "human" and self.reviewer != self.review_owner:
            raise ValueError("golden_owner_review_required")
        return self


class SimilarityDisposition(Contract):
    level: Literal["document", "chunk"]
    kind: Literal["exact", "near", "repeated_occurrence"]
    left_checksum: Sha256
    right_checksum: Sha256 | None
    decision: Literal["retain_separate_contexts"]
    reason: Annotated[str, Field(min_length=20, max_length=1000)]

    @model_validator(mode="after")
    def pair(self) -> Self:
        if self.kind == "near":
            if self.right_checksum is None or self.left_checksum >= self.right_checksum:
                raise ValueError("similarity_disposition_pair_invalid")
        elif self.right_checksum is not None:
            raise ValueError("similarity_disposition_pair_invalid")
        if self.kind == "repeated_occurrence" and self.level != "chunk":
            raise ValueError("similarity_disposition_kind_invalid")
        if not self.reason.strip():
            raise ValueError("similarity_disposition_reason_required")
        return self

    def key(self) -> tuple[str, str, str, str]:
        return self.level, self.kind, self.left_checksum, self.right_checksum or ""


class SimilarityReview(Contract):
    schema_version: Literal["1.0"]
    review_id: SimilarityReviewID
    report_id: SimilarityReportID
    review_owner: Symbol
    reviewer: Symbol
    review_kind: Literal["technical_lexical_review_only"]
    reviewed_at: UtcTime
    corpus_approval_created: FalseFlag
    decisions: Annotated[tuple[SimilarityDisposition, ...], Field(max_length=10000)]

    @model_validator(mode="after")
    def identity(self) -> Self:
        keys = [d.key() for d in self.decisions]
        if keys != sorted(set(keys)):
            raise ValueError("similarity_decisions_order_or_duplicate")
        if self.review_id != "similarity-review-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"review_id"})
        ):
            raise ValueError("similarity_review_identity_mismatch")
        return self


def release_blockers(
    corpus_approval: CorpusApproval | None,
    golden_approval: GoldenLabelsApproval | None,
    validation: IndexValidation,
    report: GoldenReport,
    unreviewed: int,
) -> tuple[Blocker, ...]:
    blockers: list[Blocker] = []
    if corpus_approval is None:
        blockers.append("corpus_approval_missing")
    if golden_approval is None:
        blockers.append("golden_labels_approval_missing")
    if validation.result != "passed":
        blockers.append("mechanical_validation_failed")
    if unreviewed:
        blockers.append("similarity_review_incomplete")
    if not report.measured_thresholds_passed:
        blockers.append("golden_thresholds_failed")
    if validation.provider == "fake":
        return tuple((*blockers, "semantic_provider_required", "user_build_profile_required"))
    return tuple(blockers)


class IndexReleaseManifest(Contract):
    schema_version: Literal["1.0"]
    release_id: ReleaseManifestID
    policy_version: Literal["fake-release-preflight-v1", "semantic-release-v1"]
    purpose: Literal["release_readiness_only"]
    status: Literal["blocked", "ready"]
    activation_allowed: bool
    index_manifest: IndexManifest
    corpus_manifest: CorpusManifest
    retrieval_config: RetrievalConfig
    golden_set: GoldenSet
    golden_report: GoldenReport
    mechanical_validation: IndexValidation
    corpus_approval: CorpusApproval | None
    golden_approval: GoldenLabelsApproval | None
    similarity_report_id: SimilarityReportID
    similarity_review: SimilarityReview | None
    similarity_findings: Annotated[int, Field(ge=0, le=10000)]
    unreviewed_similarity_findings: Annotated[int, Field(ge=0, le=10000)]
    blockers: Annotated[tuple[Blocker, ...], Field(max_length=7)]

    @model_validator(mode="after")
    def binding(self) -> Self:
        manifest, validation, report = (
            self.index_manifest,
            self.mechanical_validation,
            self.golden_report,
        )
        corpus, golden = self.corpus_manifest, self.golden_set
        fake = manifest.embedding_config.provider == "fake"
        if (
            self.policy_version != ("fake-release-preflight-v1" if fake else "semantic-release-v1")
            or validation.provider != manifest.embedding_config.provider
            or report.provider != validation.provider
        ):
            raise ValueError("release_provider_mismatch")
        ready = not self.blockers and not fake
        if self.activation_allowed != ready or self.status != ("ready" if ready else "blocked"):
            raise ValueError("release_status_mismatch")
        if (
            (validation.index_id, validation.corpus_id, validation.space_id, validation.environment)
            != (manifest.index_id, manifest.corpus_id, manifest.space_id, manifest.environment)
            or report.index_id != manifest.index_id
            or golden.index_id != manifest.index_id
            or corpus.corpus_id != manifest.corpus_id
            or corpus.environment != manifest.environment
            or golden.golden_set_id != report.golden_set_id
            or golden.retrieval_config_id != report.retrieval_config_id
            or golden.thresholds != report.thresholds
            or report.retrieval_config_id != self.retrieval_config.config_id()
        ):
            raise ValueError("release_artifact_binding_mismatch")
        if self.corpus_approval is not None and not approval_matches(
            self.corpus_approval,
            manifest.corpus_id,
            corpus.corpus_config_id,
            corpus.review_owner,
            manifest.environment,
        ):
            raise ValueError("release_corpus_approval_mismatch")
        if self.golden_approval is not None:
            approval = self.golden_approval
            if (
                approval.golden_set_id,
                approval.index_id,
                approval.retrieval_config_id,
                approval.environment,
                approval.review_owner,
            ) != (
                report.golden_set_id,
                manifest.index_id,
                self.retrieval_config.config_id(),
                manifest.environment,
                golden.review_owner,
            ):
                raise ValueError("release_golden_approval_mismatch")
        reviewed = 0
        if self.similarity_review is not None:
            if (
                self.similarity_review.report_id != self.similarity_report_id
                or self.similarity_review.review_owner != corpus.review_owner
            ):
                raise ValueError("release_similarity_review_mismatch")
            reviewed = len(self.similarity_review.decisions)
        if self.unreviewed_similarity_findings != self.similarity_findings - reviewed:
            raise ValueError("release_similarity_coverage_mismatch")
        if self.blockers != release_blockers(
            self.corpus_approval,
            self.golden_approval,
            validation,
            report,
            self.unreviewed_similarity_findings,
        ):
            raise ValueError("release_blockers_mismatch")
        if self.release_id != "rag-release-manifest-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"release_id"})
        ):
            raise ValueError("release_manifest_identity_mismatch")
        return self
