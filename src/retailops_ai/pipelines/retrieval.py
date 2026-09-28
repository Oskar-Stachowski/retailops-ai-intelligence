"""Shared authorization, diversification and context budgets for SQL and offline evaluation."""

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from retailops_ai.adapters.embeddings import EmbeddingProvider, FakeEmbeddingProvider
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.chunks import MarkdownChunk
from retailops_ai.knowledge.indexes import EmbeddingConfig, IndexCandidate, IndexManifest, float32
from retailops_ai.knowledge.retrieval import (
    KnowledgeHit,
    RetrievalConfig,
    RetrievalRequest,
    RetrievalResult,
)
from retailops_ai.pipelines.releases import load_release_document


class KnowledgeDenied(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedScope:
    repositories: frozenset[str]
    document_types: frozenset[str]
    document_statuses: frozenset[str]
    access_classes: frozenset[str]


def resolve_scope(
    principal: Principal, request: RetrievalRequest, environment: str
) -> ResolvedScope:
    grant = principal.knowledge
    if (
        "knowledge:read" not in principal.capabilities
        or grant is None
        or grant.environment != environment
    ):
        raise KnowledgeDenied("knowledge_scope_denied")
    filters = request.filters
    groups = [
        (filters.repositories, grant.repositories),
        (filters.document_statuses, grant.document_statuses),
        (filters.access_classes, grant.access_classes),
    ]
    for requested, allowed in groups:
        if requested is not None and not set(requested) <= allowed:
            raise KnowledgeDenied("knowledge_scope_denied")
    statuses = (
        frozenset(filters.document_statuses)
        if filters.document_statuses
        else grant.document_statuses & {"specified", "implemented", "verified"}
    )
    if request.purpose == "implementation":
        statuses &= {"implemented", "verified"}
    elif request.purpose == "verified_state":
        statuses &= {"verified"}
    elif request.purpose == "history":
        statuses = (
            frozenset(filters.document_statuses)
            if filters.document_statuses
            else grant.document_statuses & {"historical", "deprecated"}
        )
        statuses &= {"historical", "deprecated"}
    return ResolvedScope(
        frozenset(filters.repositories) if filters.repositories else grant.repositories,
        frozenset(filters.document_types)
        if filters.document_types
        else frozenset(
            {
                "architecture",
                "contract",
                "guide",
                "runbook",
                "policy",
                "plan",
                "evidence",
                "model_card",
            }
        ),
        statuses,
        frozenset(filters.access_classes) if filters.access_classes else grant.access_classes,
    )


def allowed(chunk: MarkdownChunk, scope: ResolvedScope, denied: frozenset[str]) -> bool:
    return (
        chunk.document_id not in denied
        and chunk.repository in scope.repositories
        and chunk.document_type in scope.document_types
        and chunk.document_status in scope.document_statuses
        and chunk.access_class in scope.access_classes
    )


def checked_vector(config: EmbeddingConfig, vector: tuple[float, ...]) -> tuple[float, ...]:
    if (
        len(vector) != config.dimension
        or any(not math.isfinite(v) or float32(v) != v for v in vector)
        or not math.isclose(math.sqrt(math.fsum(v * v for v in vector)), 1, abs_tol=1e-6)
    ):
        raise ValueError("invalid_query_embedding")
    return vector


def context_size(hits: list[KnowledgeHit]) -> int:
    if not hits:
        return 0
    return len(
        json.dumps(
            [h.model_dump(mode="json") for h in hits], ensure_ascii=False, separators=(",", ":")
        ).encode()
    )


def result_from_ranked(
    manifest: IndexManifest,
    request: RetrievalRequest,
    config: RetrievalConfig,
    ranked: list[tuple[MarkdownChunk, float]],
) -> RetrievalResult:
    ranked = sorted(ranked, key=lambda row: (-row[1], row[0].chunk_id))
    # Reserve the best eligible hit from each repository, then each document.
    order: list[tuple[MarkdownChunk, float]] = []
    for attribute in (
        ("repository", "document_id")
        if config.diversification == "repository-then-document-v1"
        else ()
    ):
        seen = {getattr(c, attribute) for c, _ in order}
        for row in ranked:
            key = getattr(row[0], attribute)
            if key not in seen:
                order.append(row)
                seen.add(key)
    selected_ids = {c.chunk_id for c, _ in order}
    order.extend(row for row in ranked if row[0].chunk_id not in selected_ids)
    hits: list[KnowledgeHit] = []
    counts: Counter[str] = Counter()
    kinds = {
        "specified": "plan",
        "implemented": "implementation",
        "verified": "verified_evidence",
        "historical": "historical_reference",
        "deprecated": "historical_reference",
    }
    for chunk, score in order:
        if len(hits) == request.top_k:
            break
        if (
            score < config.min_cosine_score
            or counts[chunk.document_id] >= config.max_chunks_per_document
        ):
            continue
        hit = KnowledgeHit.model_validate(
            {
                "score": max(-1.0, min(1.0, score)),
                "chunk": chunk,
                "claim_kind": kinds[chunk.document_status],
            }
        )
        size = context_size([*hits, hit])
        if size > config.max_context_bytes or (size + 3) // 4 > request.max_context_tokens:
            continue
        hits.append(hit)
        counts[chunk.document_id] += 1
    size = context_size(hits)
    return RetrievalResult(
        status="ok" if hits else "insufficient_evidence",
        index_id=manifest.index_id,
        corpus_id=manifest.corpus_id,
        retrieval_config_id=config.config_id(),
        items=tuple(hits),
        context_bytes=size,
        context_tokens=(size + 3) // 4,
    )


def search_candidate(
    candidate: IndexCandidate,
    request: RetrievalRequest,
    principal: Principal,
    config: RetrievalConfig,
    *,
    denied: frozenset[str] = frozenset(),
    provider: EmbeddingProvider | None = None,
) -> RetrievalResult:
    """Private evaluation path; it neither qualifies nor activates a corpus."""
    scope = resolve_scope(principal, request, candidate.manifest.environment)
    provider = provider or FakeEmbeddingProvider(candidate.manifest.embedding_config)
    if provider.config != candidate.manifest.embedding_config:
        raise ValueError("embedding_provider_config_mismatch")
    vector = checked_vector(
        candidate.manifest.embedding_config,
        provider.embed(request.question),
    )
    records = {record.embedding_id: record.vector for record in candidate.embeddings}
    ranked = []
    for chunk, entry in zip(candidate.chunks.chunks, candidate.manifest.entries, strict=True):
        if allowed(chunk, scope, denied):
            values = records[entry.embedding_id]
            similarity = math.fsum(a * b for a, b in zip(vector, values, strict=True)) / (
                math.sqrt(math.fsum(v * v for v in vector))
                * math.sqrt(math.fsum(v * v for v in values))
            )
            ranked.append((chunk, similarity))
    ranked.sort(key=lambda row: (-row[1], row[0].chunk_id))
    counts: Counter[str] = Counter()
    pool = []
    for row in ranked:
        counts[row[0].document_id] += 1
        if counts[row[0].document_id] <= config.max_chunks_per_document:
            pool.append(row)
    return result_from_ranked(candidate.manifest, request, config, pool[: config.candidate_limit])


def load_retrieval_config(path: Path) -> RetrievalConfig:
    return load_release_document(path, RetrievalConfig)
