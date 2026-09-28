"""Approved offline build snapshots and safe administrative run references."""

import json
from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    FalseFlag,
    Sha256,
    Symbol,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.chunks import ChunkManifest, ChunkManifestID
from retailops_ai.knowledge.contracts import REPOSITORIES, ConfigID, CorpusID, Repository
from retailops_ai.knowledge.golden import GoldenID, GoldenReport, GoldenSet
from retailops_ai.knowledge.indexes import (
    Dimension,
    EmbeddingConfig,
    EmbeddingRecord,
    IndexID,
    SpaceID,
)
from retailops_ai.knowledge.qualification import GoldenLabelsApproval, SimilarityReview
from retailops_ai.knowledge.releases import CorpusApproval, IndexValidation, approval_matches
from retailops_ai.knowledge.retrieval import RetrievalConfig
from retailops_ai.knowledge.review import SimilarityPolicy

BuildProfileID = Annotated[str, Field(pattern=r"^index-build-profile-sha256-[0-9a-f]{64}$")]
IndexConfigID = Annotated[str, Field(pattern=r"^index-config-sha256-[0-9a-f]{64}$")]
ReportID = Annotated[str, Field(pattern=r"^index-run-report-sha256-[0-9a-f]{64}$")]
ManifestRef = Annotated[str, Field(pattern=r"^db:ai.rag_indexes:index-sha256-[0-9a-f]{64}$")]
ReportRef = Annotated[
    str, Field(pattern=r"^db:ai.rag_index_reports:index-run-report-sha256-[0-9a-f]{64}$")
]
EvaluationID = Literal["offline-index-mechanics-v1"]
IndexErrorCode = Literal[
    "configuration-not-approved",
    "idempotency-conflict",
    "index-run-not-found",
    "index-not-configured",
    "queue-full",
    "worker-busy",
    "claim-lost",
    "index-report-not-found",
]


class IndexSource(Contract):
    repository: Repository
    commit_sha: CommitSha

    @field_validator("commit_sha")
    @classmethod
    def real_revision(cls, value: str) -> str:
        if value == "0" * 40:
            raise ValueError("placeholder_revision")
        return value


class KnowledgeIndexRequest(Contract):
    corpus_config_id: ConfigID
    sources: Annotated[tuple[IndexSource, ...], Field(min_length=2, max_length=2)]
    index_config_id: IndexConfigID
    evaluation_set_id: Symbol

    @field_validator("sources", mode="before")
    @classmethod
    def json_arrays(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def both_sources(self) -> Self:
        if {s.repository for s in self.sources} != set(REPOSITORIES):
            raise ValueError("both_registered_repositories_required")
        return self

    def request_hash(self) -> str:
        body = self.model_dump(mode="json")
        body["sources"] = [
            s.model_dump(mode="json") for s in sorted(self.sources, key=lambda s: s.repository)
        ]
        return canonical_sha256(body)


class IndexBuildProfile(Contract):
    """Test-only acceptance, never an approval of the real proposed corpus or labels."""

    schema_version: Literal["1.0"]
    profile_id: BuildProfileID
    environment: Literal["test"]
    approval: CorpusApproval
    chunks: ChunkManifest
    embedding_config: EmbeddingConfig
    evaluation_set_id: EvaluationID
    purpose: Literal["offline_build_mechanics_only"]

    @model_validator(mode="after")
    def binding(self) -> Self:
        corpus = self.chunks.corpus
        if self.embedding_config.provider != "fake":
            raise ValueError("offline_profile_requires_fake_embeddings")
        if (
            not approval_matches(
                self.approval,
                corpus.corpus_id,
                corpus.corpus_config_id,
                corpus.review_owner,
                self.environment,
            )
            or corpus.environment != self.environment
        ):
            raise ValueError("build_profile_review_binding_mismatch")
        if self.profile_id != "index-build-profile-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_id"})
        ):
            raise ValueError("build_profile_identity_mismatch")
        return self

    def index_config_id(self) -> str:
        return index_config_id(self.chunks, self.embedding_config)

    def request(self) -> KnowledgeIndexRequest:
        return KnowledgeIndexRequest.model_validate(
            {
                "corpus_config_id": self.chunks.corpus.corpus_config_id,
                "sources": tuple(
                    IndexSource(repository=s.repository, commit_sha=s.commit_sha)
                    for s in sorted(self.chunks.corpus.sources, key=lambda s: s.repository)
                ),
                "index_config_id": self.index_config_id(),
                "evaluation_set_id": self.evaluation_set_id,
            }
        )


class KnowledgeRunInput(Contract):
    request: KnowledgeIndexRequest
    request_hash: Sha256
    profile_id: BuildProfileID
    environment: Literal["local", "test"]

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.request_hash != self.request.request_hash():
            raise ValueError("index_run_request_hash_mismatch")
        return self


class KnowledgeRunOutput(Contract):
    kind: Literal["knowledge_index"]
    complete: TrueFlag
    index_id: IndexID
    manifest_ref: ManifestRef
    evaluation_report_ref: ReportRef
    activation_status: Literal["candidate"]

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.manifest_ref != "db:ai.rag_indexes:" + self.index_id:
            raise ValueError("index_run_manifest_reference_mismatch")
        return self


class IndexRunReport(Contract):
    schema_version: Literal["1.0"]
    report_id: ReportID
    profile_id: BuildProfileID
    validation: IndexValidation
    purpose: Literal["offline_build_mechanics_only"]
    activation_allowed: FalseFlag

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.validation.environment != "test":
            raise ValueError("index_run_report_requires_test_environment")
        if self.report_id != "index-run-report-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"report_id"})
        ):
            raise ValueError("index_run_report_identity_mismatch")
        return self


class CurrentKnowledgeIndex(Contract):
    schema_version: Literal["1.0"] = "1.0"
    index_id: IndexID
    status: Literal["active"] = "active"
    environment: Literal["local", "test"]
    lane: Literal["offline_test", "retrieval"]
    purpose: Literal["lifecycle_validation_only", "qualified_semantic_retrieval"]
    manifest_ref: ManifestRef
    corpus_manifest_id: CorpusID
    chunk_manifest_id: ChunkManifestID
    index_config_id: IndexConfigID
    embedding_config_id: SpaceID
    dimension: Dimension
    document_count: Annotated[int, Field(ge=2, le=128)]
    chunk_count: Annotated[int, Field(ge=1, le=32768)]
    activated_at: UtcTime
    evaluation_report_ref: Annotated[
        str,
        Field(
            pattern=r"^db:ai\.(rag_qualifications:index-validation|rag_index_reports:index-run-report)-sha256-[0-9a-f]{64}$"
        ),
    ]

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.lane == "offline_test":
            if (
                self.environment != "test"
                or self.purpose != "lifecycle_validation_only"
                or self.dimension not in {8, 16, 32, 64}
            ):
                raise ValueError("current_offline_binding_mismatch")
        elif self.purpose != "qualified_semantic_retrieval" or self.dimension not in {
            256,
            512,
            1024,
        }:
            raise ValueError("current_semantic_binding_mismatch")
        expected_prefix = (
            "db:ai.rag_qualifications:"
            if self.lane == "offline_test"
            else "db:ai.rag_index_reports:"
        )
        if not self.evaluation_report_ref.startswith(expected_prefix):
            raise ValueError("current_evaluation_reference_mismatch")
        if self.manifest_ref != "db:ai.rag_indexes:" + self.index_id:
            raise ValueError("current_index_manifest_reference_mismatch")
        return self


def index_config_id(chunks: ChunkManifest, embedding: EmbeddingConfig) -> str:
    return "index-config-sha256-" + canonical_sha256(
        {
            "chunker_config_id": chunks.chunker_config_id,
            "embedding_config": embedding.model_dump(mode="json"),
            "storage_version": "pgvector-checked-dimension-v1",
        }
    )


class GoldenProfileBase(Contract):
    """Explicitly approved corpus and labels; fake evaluation can only produce a candidate."""

    schema_version: Literal["1.0"]
    profile_id: BuildProfileID
    environment: Literal["local", "test"]
    approval: CorpusApproval
    chunks: ChunkManifest
    embedding_config: EmbeddingConfig
    evaluation_set_id: GoldenID
    golden_set: GoldenSet
    golden_approval: GoldenLabelsApproval
    retrieval_config: RetrievalConfig
    similarity_policy: SimilarityPolicy
    similarity_review: SimilarityReview
    purpose: Literal[
        "approved_corpus_fake_golden_validation", "approved_corpus_semantic_validation"
    ]

    @model_validator(mode="after")
    def binding(self) -> Self:
        corpus, golden, labels = self.chunks.corpus, self.golden_set, self.golden_approval
        expected_provider = (
            "fake" if self.purpose == "approved_corpus_fake_golden_validation" else "bedrock"
        )
        if self.embedding_config.provider != expected_provider:
            raise ValueError("golden_profile_provider_mismatch")
        if (
            not approval_matches(
                self.approval,
                corpus.corpus_id,
                corpus.corpus_config_id,
                corpus.review_owner,
                self.environment,
            )
            or corpus.environment != self.environment
        ):
            raise ValueError("golden_profile_corpus_approval_mismatch")
        if (
            labels.golden_set_id,
            labels.index_id,
            labels.retrieval_config_id,
            labels.environment,
            labels.review_owner,
        ) != (
            golden.golden_set_id,
            golden.index_id,
            self.retrieval_config.config_id(),
            self.environment,
            golden.review_owner,
        ) or golden.retrieval_config_id != self.retrieval_config.config_id():
            raise ValueError("golden_profile_labels_approval_mismatch")
        if self.evaluation_set_id != golden.golden_set_id:
            raise ValueError("golden_profile_evaluation_set_mismatch")
        if self.similarity_review.review_owner != corpus.review_owner:
            raise ValueError("golden_profile_similarity_owner_mismatch")
        if self.profile_id != "index-build-profile-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"profile_id"})
        ):
            raise ValueError("build_profile_identity_mismatch")
        return self

    def index_config_id(self) -> str:
        return "index-config-sha256-" + canonical_sha256(
            {
                "base_index_config_id": index_config_id(self.chunks, self.embedding_config),
                "retrieval_config_id": self.retrieval_config.config_id(),
                "similarity_policy_id": self.similarity_policy.config_id(),
                "similarity_review_id": self.similarity_review.review_id,
                "corpus_approval_id": self.approval.review_id,
                "golden_approval_id": self.golden_approval.approval_id,
            }
        )

    def request(self) -> KnowledgeIndexRequest:
        return KnowledgeIndexRequest.model_validate_json(
            json.dumps(
                {
                    "corpus_config_id": self.chunks.corpus.corpus_config_id,
                    "sources": [
                        {"repository": s.repository, "commit_sha": s.commit_sha}
                        for s in sorted(self.chunks.corpus.sources, key=lambda s: s.repository)
                    ],
                    "index_config_id": self.index_config_id(),
                    "evaluation_set_id": self.evaluation_set_id,
                }
            )
        )


class GoldenIndexBuildProfile(GoldenProfileBase):
    purpose: Literal["approved_corpus_fake_golden_validation"]


class SemanticIndexBuildProfile(GoldenProfileBase):
    purpose: Literal["approved_corpus_semantic_validation"]
    document_embeddings: Annotated[
        tuple[EmbeddingRecord, ...], Field(min_length=1, max_length=32768)
    ]
    query_embeddings: Annotated[tuple[EmbeddingRecord, ...], Field(min_length=1, max_length=50)]


class GoldenIndexRunReport(Contract):
    schema_version: Literal["1.0"]
    report_id: ReportID
    profile_id: BuildProfileID
    validation: IndexValidation
    approval: CorpusApproval
    golden_approval: GoldenLabelsApproval
    golden: GoldenReport
    similarity_report_id: Annotated[str, Field(pattern=r"^similarity-report-sha256-[0-9a-f]{64}$")]
    similarity_review_id: Annotated[str, Field(pattern=r"^similarity-review-sha256-[0-9a-f]{64}$")]
    quality_gate_passed: bool
    purpose: Literal[
        "approved_corpus_fake_golden_validation", "approved_corpus_semantic_validation"
    ]
    activation_allowed: FalseFlag

    @model_validator(mode="after")
    def binding(self) -> Self:
        validation, labels, golden = self.validation, self.golden_approval, self.golden
        expected_provider = (
            "fake" if self.purpose == "approved_corpus_fake_golden_validation" else "bedrock"
        )
        if validation.provider != expected_provider or golden.provider != expected_provider:
            raise ValueError("golden_report_provider_mismatch")
        if (
            validation.index_id != golden.index_id
            or labels.index_id != golden.index_id
            or labels.golden_set_id != golden.golden_set_id
            or labels.retrieval_config_id != golden.retrieval_config_id
            or validation.environment != labels.environment
            or validation.environment != self.approval.environment
            or validation.corpus_id != self.approval.corpus_id
        ):
            raise ValueError("golden_run_report_binding_mismatch")
        if self.quality_gate_passed != (
            validation.result == "passed" and golden.measured_thresholds_passed
        ):
            raise ValueError("golden_run_report_gate_mismatch")
        if self.report_id != "index-run-report-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"report_id"})
        ):
            raise ValueError("index_run_report_identity_mismatch")
        return self


BuildProfile = Annotated[
    IndexBuildProfile | GoldenIndexBuildProfile | SemanticIndexBuildProfile,
    Field(discriminator="purpose"),
]
RunReport = Annotated[IndexRunReport | GoldenIndexRunReport, Field(discriminator="purpose")]
BUILD_PROFILE_ADAPTER: TypeAdapter[BuildProfile] = TypeAdapter(BuildProfile)
RUN_REPORT_ADAPTER: TypeAdapter[RunReport] = TypeAdapter(RunReport)
