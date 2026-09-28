"""Mechanical acceptance is distinct from editorial approval and retrieval quality."""

from pathlib import Path
from typing import TypeVar

from retailops_ai.adapters.embeddings import FakeEmbeddingProvider
from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.releases import CorpusApproval, IndexValidation
from retailops_ai.pipelines.corpus import decode_json

T = TypeVar("T", bound=Contract)


def load_release_document(path: Path, model: type[T]) -> T:
    with path.open("rb") as source:
        raw = source.read(500001)
    decode_json(raw)
    return model.model_validate_json(raw)


def validate_candidate(candidate: IndexCandidate) -> IndexValidation:
    candidate = IndexCandidate.model_validate_json(candidate.model_dump_json())
    provider = FakeEmbeddingProvider(candidate.manifest.embedding_config)
    records = {r.embedding_id: r for r in candidate.embeddings}
    deterministic = all(
        records[e.embedding_id].vector == provider.embed(c.text)
        for e, c in zip(candidate.manifest.entries, candidate.chunks.chunks, strict=True)
    )
    value = {
        "schema_version": "1.0",
        "policy_version": "fake-mechanical-validation-v1",
        "index_id": candidate.manifest.index_id,
        "corpus_id": candidate.manifest.corpus_id,
        "space_id": candidate.manifest.space_id,
        "environment": candidate.manifest.environment,
        "provider": "fake",
        "golden_evaluation": "not_evaluated_fake_vectors",
        "checks": {
            "complete_graph": True,
            "source_metadata": True,
            "citation_binding": True,
            "vector_binding": True,
            "deterministic_fake_vectors": deterministic,
        },
        "result": "passed" if deterministic else "failed",
    }
    value["validation_id"] = "index-validation-sha256-" + canonical_sha256(value)
    return IndexValidation.model_validate(value)


def load_approval(path: Path) -> CorpusApproval:
    return load_release_document(path, CorpusApproval)
