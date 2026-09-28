"""Compile validated metadata and exact Git bytes into an immutable candidate."""

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from retailops_ai.adapters.git_documents import CorpusError, GitDocuments
from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.registry import _invalid_constant, _pairs
from retailops_ai.knowledge.contracts import (
    EXCLUSIONS,
    MAX_MARKDOWN_BYTES,
    CorpusManifest,
    CorpusRegistry,
    ExcludedDocument,
    ManifestDocument,
    Repository,
    document_id,
    document_path,
    source_ref,
)


def decode_json(raw: bytes) -> Any:
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise CorpusError("registry_too_large")
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def load_registry(path: Path) -> CorpusRegistry:
    with path.open("rb") as source:
        raw = source.read(MAX_MARKDOWN_BYTES + 1)
    decode_json(raw)
    return CorpusRegistry.model_validate_json(raw)


def pointer_value(value: Any, pointer: str) -> Any:
    try:
        for key in pointer.lstrip("/").split("/"):
            if isinstance(value, list):
                if not key.isdigit() or (key.startswith("0") and key != "0"):
                    raise ValueError("invalid_index")
                value = value[int(key)]
            else:
                value = value[key]
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise CorpusError("evidence_binding_missing") from exc
    return value


def build_candidate(
    registry: CorpusRegistry, repositories: dict[Repository, Path]
) -> CorpusManifest:
    if set(repositories) != {s.repository for s in registry.sources}:
        raise CorpusError("repository_mapping_mismatch")
    readers = {name: GitDocuments(path, name) for name, path in repositories.items()}
    documents = []
    excluded = []
    for source in sorted(registry.sources, key=lambda s: s.repository):
        reader = readers[source.repository]
        registered = {d.path for d in source.documents}
        for path, entry in sorted(reader.tree(source.commit_sha).items()):
            if path in registered or not path.endswith(".md"):
                continue
            if not any(path.startswith(root + "/") for root in source.allowed_roots):
                continue
            reason: Any = "not_registered"
            try:
                document_path(path)
            except ValueError:
                reason = "credentials_or_private_path"
            if entry.mode != "100644" or entry.kind != "blob":
                reason = "non_regular_git_entry"
            excluded.append(
                ExcludedDocument(repository=source.repository, path=path, reason=reason)
            )
        for document in sorted(source.documents, key=lambda d: d.path):
            raw = reader.read(source.commit_sha, document.path)
            if hashlib.sha256(raw).hexdigest() != document.byte_sha256:
                raise CorpusError("document_checksum_mismatch")
            try:
                normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeError as exc:
                raise CorpusError("markdown_encoding_invalid") from exc
            if not normalized.strip() or "\0" in normalized:
                raise CorpusError("markdown_content_invalid")
            for code in document.implementation_refs:
                code_raw = readers[code.repository].read(code.commit_sha, code.path)
                if hashlib.sha256(code_raw).hexdigest() != code.byte_sha256:
                    raise CorpusError("implementation_checksum_mismatch")
            if document.verification:
                evidence = document.verification
                evidence_raw = readers[evidence.repository].read(evidence.commit_sha, evidence.path)
                if hashlib.sha256(evidence_raw).hexdigest() != evidence.byte_sha256:
                    raise CorpusError("evidence_checksum_mismatch")
                proof = decode_json(evidence_raw)
                if pointer_value(proof, evidence.result_pointer) != evidence.expected_result:
                    raise CorpusError("evidence_result_mismatch")
                if pointer_value(proof, evidence.commit_pointer) != evidence.verified_commit:
                    raise CorpusError("verified_commit_mismatch")
                measured_at = TypeAdapter(UtcTime).validate_json(
                    json.dumps(pointer_value(proof, evidence.verified_at_pointer))
                )
                if measured_at != evidence.verified_at:
                    raise CorpusError("verification_time_mismatch")
                readers[evidence.repository].tree(evidence.verified_commit)
            documents.append(
                ManifestDocument(
                    **document.model_dump(),
                    repository=source.repository,
                    commit_sha=source.commit_sha,
                    document_id=document_id(source.repository, document.path),
                    content_id="markdown-sha256-" + hashlib.sha256(normalized.encode()).hexdigest(),
                    source_ref=source_ref(source.repository, source.commit_sha, document.path),
                )
            )
    groups: dict[str, list[str]] = defaultdict(list)
    for document in documents:
        groups[document.content_id].append(document.document_id)
    payload = {
        "schema_version": "1.0",
        "policy_version": registry.policy_version,
        "canonicalization_version": "utf8-lf-v1",
        "environment": registry.environment,
        "lifecycle": "candidate",
        "review_owner": registry.review_owner,
        "content_trust": "untrusted_reference",
        "corpus_config_id": registry.config_id(),
        "sources": registry.semantic_data()["sources"],
        "documents": [d.model_dump(mode="json") for d in documents],
        "excluded_documents": [d.model_dump(mode="json") for d in excluded],
        "exclusion_policy": EXCLUSIONS,
        "duplicate_content_groups": sorted(sorted(g) for g in groups.values() if len(g) > 1),
    }
    payload["corpus_id"] = "corpus-sha256-" + canonical_sha256(payload)
    return CorpusManifest.model_validate_json(json.dumps(payload))


def write_candidate(manifest: CorpusManifest, output: Path) -> None:
    """Publish a complete file atomically and exclusively; never replace a manifest."""
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=output.parent, delete=False) as target:
            temporary = target.name
            target.write((manifest.model_dump_json(indent=2) + "\n").encode())
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, output)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
