import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from pydantic import ValidationError
from sqlalchemy import text
from test_chunks import build, replace
from test_chunks import config as config
from test_chunks import sources as sources
from test_retrieval import reader, request, retrieval_config

from retailops_ai.adapters.bedrock_embeddings import BedrockEmbeddingProvider
from retailops_ai.adapters.embedding_cache import CachedEmbeddingProvider
from retailops_ai.adapters.embedding_snapshot import SnapshotEmbeddingProvider, embedding_record
from retailops_ai.adapters.embeddings import FakeEmbeddingProvider
from retailops_ai.knowledge.indexes import EmbeddingConfig, embedding_text
from retailops_ai.pipelines.indexes import build_index, load_embedding_config
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.pipelines.retrieval import KnowledgeDenied, search_candidate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def real_config():
    return load_embedding_config(ROOT / "knowledge/embeddings.bedrock.v1.json").model_copy(
        update={"dimension": 256}
    )


def client_with(values=None, tokens=3):
    client = Mock()

    def invoke(**kwargs):
        return {
            "body": io.BytesIO(
                json.dumps(
                    {
                        "embedding": [1.0] + [0.0] * 255 if values is None else values,
                        "inputTextTokenCount": tokens,
                    }
                ).encode()
            )
        }

    client.invoke_model.side_effect = invoke
    return client


def provider(config, client=None, requests=10, size=10000):
    return BedrockEmbeddingProvider(
        config, client=client or client_with(), max_requests=requests, max_input_bytes=size
    )


def test_titan_payload_normalization_and_closed_body(real_config):
    client = client_with()
    p = provider(real_config, client)
    vector = p.embed("Jak działa API?")
    record = embedding_record(real_config, "Jak działa API?", vector)
    assert record.dimension == 256 and p.requests == 1 and p.input_tokens == 3
    call = client.invoke_model.call_args.kwargs
    assert call["modelId"] == "amazon.titan-embed-text-v2:0"
    assert json.loads(call["body"]) == {
        "inputText": "Jak działa API?",
        "dimensions": 256,
        "normalize": True,
    }


@pytest.mark.parametrize(
    "values,tokens",
    [
        ([0.0] * 256, 3),
        ([float("nan")] * 256, 3),
        ([True] * 256, 3),
        ([1.0], 3),
        ([1.0] + [0.0] * 255, True),
        ([1.0] + [0.0] * 255, 8193),
    ],
)
def test_malformed_response_rejected(real_config, values, tokens):
    p = provider(real_config, client_with(values, tokens))
    with pytest.raises(ValueError):
        p.embed("question")
    assert p.requests == 1


def test_failure_consumes_budget_without_reflecting_provider_details(real_config):
    client = client_with()
    client.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "private-input-sentinel"}},
        "InvokeModel",
    )
    p = provider(real_config, client, requests=1)
    with pytest.raises(ValueError, match="^embedding_provider_unavailable$"):
        p.embed("question")
    with pytest.raises(ValueError, match="budget_exhausted"):
        p.embed("question")
    assert client.invoke_model.call_count == 1


def test_parallel_calls_cannot_exceed_process_budget(real_config):
    p = provider(real_config, requests=3)

    def invoke(i):
        try:
            p.embed(str(i))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(invoke, range(20))) == 3
    assert p.requests == 3


@pytest.mark.parametrize(
    "change",
    [
        {"region": "offline"},
        {"dimension": 32},
        {"model_id": "sha256-unit-f32-v1"},
        {"inference_profile": "arbitrary"},
    ],
)
def test_provider_spaces_cannot_be_mixed(real_config, change):
    raw = real_config.model_dump(mode="json")
    raw.update(change)
    with pytest.raises(ValidationError):
        EmbeddingConfig.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError, match="explicit_real"):
        FakeEmbeddingProvider(real_config)


def test_heading_context_changes_cache_identity_without_changing_citations(
    sources, config, real_config
):
    replace(sources, "# First\n\nShared body.\n", 0)
    replace(sources, "# Second\n\nShared body.\n", 1)
    chunks = build(sources, config)
    p = provider(real_config)
    candidate = build_index(chunks, real_config, provider=p)
    assert candidate.manifest.embedding_count == 2 and p.requests == 2
    assert candidate.chunks == chunks
    assert embedding_text(chunks.chunks[0], real_config).endswith("\n\nShared body.")
    assert validate_candidate(candidate).checks.deterministic_fake_vectors is None
    replay = build_index(
        chunks, real_config, provider=SnapshotEmbeddingProvider(real_config, candidate.embeddings)
    )
    assert replay == candidate
    with pytest.raises(ValueError, match="explicit_real"):
        build_index(chunks, real_config)


def test_private_cache_replay_binding_and_no_implicit_network(tmp_path, real_config):
    p = provider(real_config)
    cache = CachedEmbeddingProvider(real_config, tmp_path / "cache", p)
    first = cache.embed("one")
    assert cache.embed("one") == first and p.requests == 1
    offline = CachedEmbeddingProvider(real_config, tmp_path / "cache", None)
    assert offline.embed("one") == first
    with pytest.raises(ValueError, match="miss_offline"):
        offline.embed("two")
    path = next((tmp_path / "cache").iterdir())
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text(embedding_record(real_config, "another", first).model_dump_json())
    with pytest.raises(ValueError, match="binding_mismatch"):
        offline.embed("one")


def test_unauthorized_query_never_calls_real_provider(sources, config, real_config):
    p = provider(real_config)
    candidate = build_index(build(sources, config), real_config, provider=p)
    before = p.requests
    with pytest.raises(KnowledgeDenied):
        search_candidate(
            candidate, request(), reader(capability=False), retrieval_config(), provider=p
        )
    assert p.requests == before


def test_semantic_migration_contains_no_accidental_bind_parameters(monkeypatch):
    from importlib import import_module

    module = import_module("retailops_ai.migrations.versions.0008_rag_semantic")
    statements = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module.upgrade()
    assert statements and all(not text(s).compile().params for s in statements)
    with pytest.raises(RuntimeError, match="backup_restore"):
        module.downgrade()
