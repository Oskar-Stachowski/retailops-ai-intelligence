"""Explicit editorial metadata and reproducible candidate corpus contracts."""

import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, Sha256, Symbol, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256

Repository = Literal[
    "Oskar-Stachowski/retailops-cloud-native-platform",
    "Oskar-Stachowski/retailops-ai-intelligence",
]
REPOSITORIES = (
    "Oskar-Stachowski/retailops-cloud-native-platform",
    "Oskar-Stachowski/retailops-ai-intelligence",
)
DocumentStatus = Literal["specified", "implemented", "verified", "deprecated", "historical"]
AccessClass = Literal["public_project", "project_internal", "restricted"]
Scope = Annotated[str, Field(min_length=1, max_length=600)]
DocumentID = Annotated[str, Field(pattern=r"^document-sha256-[0-9a-f]{64}$")]
ContentID = Annotated[str, Field(pattern=r"^markdown-sha256-[0-9a-f]{64}$")]
CorpusID = Annotated[str, Field(pattern=r"^corpus-sha256-[0-9a-f]{64}$")]
ConfigID = Annotated[str, Field(pattern=r"^corpus-config-sha256-[0-9a-f]{64}$")]
MAX_MARKDOWN_BYTES = 500_000
DENIED_COMPONENTS = {
    "raw",
    "data",
    "simulation_truth",
    "simulation-truth",
    "truth",
    "uploads",
    "private",
    "artifacts",
    "models",
    "logs",
    "adversarial",
    "fixtures",
}
EXCLUSIONS = {
    "outside_document_roots": "Only explicitly registered docs paths are considered.",
    "credentials_or_private_path": "Dotfiles, credentials and private/runtime paths are forbidden.",
    "non_markdown": "Raw facts, logs, uploads, binaries and JSON evidence are not corpus text.",
    "not_registered": "Unregistered Markdown requires a new explicit editorial registration.",
    "non_regular_git_entry": "Symlinks, executable documents and submodules are forbidden.",
}


def safe_path(value: str) -> str:
    if (
        not value
        or len(value) > 240
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or "\\" in value
        or ":" in value
        or "%" in value
        or value.startswith("/")
        or str(PurePosixPath(value)) != value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("invalid_document_path")
    if value.split("/")[0] != "docs":
        raise ValueError("document_outside_docs")
    if any(part.startswith(".") or part.lower() in DENIED_COMPONENTS for part in value.split("/")):
        raise ValueError("private_or_truth_path")
    return value


def document_path(value: str) -> str:
    safe_path(value)
    if not value.endswith(".md") or value.lower().endswith((".key.md", ".pem.md", ".log.md")):
        raise ValueError("markdown_document_required")
    return value


class EvidenceReference(Contract):
    repository: Repository
    commit_sha: CommitSha
    path: str
    byte_sha256: Sha256
    verified_at: UtcTime
    verified_commit: CommitSha
    commit_pointer: Annotated[
        str, Field(pattern=r"^/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$", max_length=200)
    ]
    verified_at_pointer: Annotated[
        str, Field(pattern=r"^/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$", max_length=200)
    ]
    scope: Scope
    result_pointer: Annotated[
        str, Field(pattern=r"^/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+$", max_length=200)
    ]
    expected_result: Literal["success", "passed", "rejected"]

    @field_validator("path")
    @classmethod
    def evidence_path(cls, value: str) -> str:
        safe_path(value)
        if not value.startswith("docs/evidence/") or not value.endswith(".json"):
            raise ValueError("versioned_json_evidence_required")
        return value

    @field_validator("commit_sha", "verified_commit")
    @classmethod
    def real_revision(cls, value: str) -> str:
        if value == "0" * 40:
            raise ValueError("placeholder_revision")
        return value


class ImplementationReference(Contract):
    repository: Repository
    commit_sha: CommitSha
    path: Annotated[str, Field(max_length=240)]
    byte_sha256: Sha256

    @field_validator("commit_sha")
    @classmethod
    def real_revision(cls, value: str) -> str:
        return EvidenceReference.real_revision(value)

    @field_validator("path")
    @classmethod
    def code_path(cls, value: str) -> str:
        if (
            str(PurePosixPath(value)) != value
            or "\\" in value
            or ":" in value
            or "%" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(
                part.startswith(".") or part.lower() in DENIED_COMPONENTS - {"models"}
                for part in value.split("/")
            )
            or not re.match(r"^(src|scripts|services|ml|infra|k8s)/", value)
            or not value.endswith(".py")
        ):
            raise ValueError("implementation_path_invalid")
        return value


class DocumentRegistration(Contract):
    path: str
    title: Annotated[str, Field(min_length=1, max_length=200)]
    byte_sha256: Sha256
    document_type: Literal[
        "architecture", "contract", "guide", "runbook", "policy", "plan", "evidence", "model_card"
    ]
    document_status: DocumentStatus
    access_class: AccessClass
    fact_scope: Scope
    implementation_refs: Annotated[tuple[ImplementationReference, ...], Field(max_length=8)]
    verification: EvidenceReference | None

    @field_validator("path")
    @classmethod
    def markdown_path(cls, value: str) -> str:
        return document_path(value)

    @model_validator(mode="after")
    def status_evidence(self) -> Self:
        if (self.document_status == "verified") != (self.verification is not None):
            raise ValueError("verified_status_requires_evidence_only")
        if self.path.startswith("docs/plans/") and self.document_status != "specified":
            raise ValueError("plans_are_specified")
        if self.verification and self.fact_scope != self.verification.scope:
            raise ValueError("verification_scope_mismatch")
        if self.document_status == "implemented" and not self.implementation_refs:
            raise ValueError("implemented_status_requires_code")
        return self


class SourceRegistration(Contract):
    repository: Repository
    commit_sha: CommitSha
    allowed_roots: Annotated[tuple[str, ...], Field(min_length=1, max_length=12)]
    documents: Annotated[tuple[DocumentRegistration, ...], Field(min_length=1, max_length=64)]

    @field_validator("commit_sha")
    @classmethod
    def real_revision(cls, value: str) -> str:
        return EvidenceReference.real_revision(value)

    @field_validator("allowed_roots")
    @classmethod
    def roots(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            safe_path(value)
        if len(set(values)) != len(values):
            raise ValueError("duplicate_document_root")
        return values

    @model_validator(mode="after")
    def registered_paths(self) -> Self:
        paths = [d.path for d in self.documents]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate_document_registration")
        for path in paths:
            if not any(path.startswith(root + "/") for root in self.allowed_roots):
                raise ValueError("document_outside_allowlist")
        return self


class CorpusRegistry(Contract):
    schema_version: Literal["1.0"]
    policy_version: Literal["registered-markdown-v1"]
    environment: Literal["local", "test", "dev", "production"]
    review_state: Literal["proposed"]
    review_owner: Symbol
    sources: Annotated[tuple[SourceRegistration, ...], Field(min_length=2, max_length=2)]

    @model_validator(mode="after")
    def exact_sources(self) -> Self:
        if {s.repository for s in self.sources} != set(REPOSITORIES):
            raise ValueError("both_registered_repositories_required")
        revisions = {s.repository: s.commit_sha for s in self.sources}
        for source in self.sources:
            for document in source.documents:
                references: list[ImplementationReference | EvidenceReference] = list(
                    document.implementation_refs
                )
                if document.verification:
                    references.append(document.verification)
                if any(ref.commit_sha != revisions[ref.repository] for ref in references):
                    raise ValueError("reference_source_revision_mismatch")
        return self

    def semantic_data(self) -> dict[str, object]:
        value = self.model_dump(mode="json")
        value["sources"] = [
            {
                **s.model_dump(mode="json"),
                "allowed_roots": sorted(s.allowed_roots),
                "documents": [
                    d.model_dump(mode="json") for d in sorted(s.documents, key=lambda d: d.path)
                ],
            }
            for s in sorted(self.sources, key=lambda s: s.repository)
        ]
        return value

    def config_id(self) -> str:
        return "corpus-config-sha256-" + canonical_sha256(self.semantic_data())


class ManifestDocument(DocumentRegistration):
    repository: Repository
    commit_sha: CommitSha
    document_id: DocumentID
    content_id: ContentID
    source_ref: str

    @model_validator(mode="after")
    def binding(self) -> Self:
        if self.document_id != document_id(self.repository, self.path):
            raise ValueError("document_identity_mismatch")
        if self.source_ref != source_ref(self.repository, self.commit_sha, self.path):
            raise ValueError("citation_binding_mismatch")
        return self


def document_id(repository: str, path: str) -> str:
    return "document-sha256-" + canonical_sha256({"repository": repository, "path": path})


def source_ref(repository: str, commit_sha: str, path: str) -> str:
    return f"git:{repository}@{commit_sha}:{path}"


class ExcludedDocument(Contract):
    repository: Repository
    path: str
    reason: Literal["not_registered", "credentials_or_private_path", "non_regular_git_entry"]


class CorpusManifest(Contract):
    schema_version: Literal["1.0"]
    policy_version: Literal["registered-markdown-v1"]
    canonicalization_version: Literal["utf8-lf-v1"]
    environment: Literal["local", "test", "dev", "production"]
    lifecycle: Literal["candidate"]
    review_owner: Symbol
    content_trust: Literal["untrusted_reference"]
    corpus_config_id: ConfigID
    corpus_id: CorpusID
    sources: Annotated[tuple[SourceRegistration, ...], Field(min_length=2, max_length=2)]
    documents: Annotated[tuple[ManifestDocument, ...], Field(min_length=2, max_length=128)]
    excluded_documents: tuple[ExcludedDocument, ...]
    exclusion_policy: dict[str, str]
    duplicate_content_groups: tuple[tuple[DocumentID, ...], ...]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.corpus_id != "corpus-sha256-" + canonical_sha256(self.identity_data()):
            raise ValueError("corpus_identity_mismatch")
        registrations = {(s.repository, d.path) for s in self.sources for d in s.documents}
        bindings = {(d.repository, d.path) for d in self.documents}
        if registrations != bindings or len(bindings) != len(self.documents):
            raise ValueError("corpus_coverage_mismatch")
        registry = CorpusRegistry(
            schema_version=self.schema_version,
            policy_version=self.policy_version,
            environment=self.environment,
            review_state="proposed",
            review_owner=self.review_owner,
            sources=self.sources,
        )
        if self.corpus_config_id != registry.config_id():
            raise ValueError("corpus_config_identity_mismatch")
        for source in self.sources:
            for registered in source.documents:
                found = next(
                    d
                    for d in self.documents
                    if d.repository == source.repository and d.path == registered.path
                )
                if (
                    found.commit_sha != source.commit_sha
                    or found.model_dump(include=set(DocumentRegistration.model_fields))
                    != registered.model_dump()
                ):
                    raise ValueError("corpus_registration_mismatch")
        if self.exclusion_policy != EXCLUSIONS:
            raise ValueError("exclusion_policy_mismatch")
        groups: dict[str, list[str]] = defaultdict(list)
        for document in self.documents:
            groups[document.content_id].append(document.document_id)
        expected_groups = tuple(
            sorted(tuple(sorted(group)) for group in groups.values() if len(group) > 1)
        )
        if self.duplicate_content_groups != expected_groups:
            raise ValueError("duplicate_content_groups_mismatch")
        excluded = {(d.repository, d.path) for d in self.excluded_documents}
        if len(excluded) != len(self.excluded_documents) or excluded & bindings:
            raise ValueError("exclusion_coverage_mismatch")
        roots = {s.repository: s.allowed_roots for s in self.sources}
        for exclusion in self.excluded_documents:
            if not any(
                exclusion.path.startswith(root + "/") for root in roots[exclusion.repository]
            ):
                raise ValueError("exclusion_outside_roots")
        return self

    def identity_data(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"corpus_id"})
