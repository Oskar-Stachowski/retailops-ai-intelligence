import json
from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_chunks import build, replace
from test_chunks import config as config
from test_chunks import sources as sources

from retailops_ai.adapters.embeddings import FakeEmbeddingProvider
from retailops_ai.adapters.git_documents import CorpusError
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.cli import main
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.indexes import EmbeddingConfig, IndexCandidate, embedding_id
from retailops_ai.pipelines.indexes import build_index, load_embedding_config, load_index_candidate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def embedding_config():
    return load_embedding_config(ROOT / "knowledge/embeddings.fake.v1.json")


def rebind(value):
    manifest = value["manifest"]
    manifest["index_id"] = "index-sha256-" + canonical_sha256(
        {k: v for k, v in manifest.items() if k != "index_id"}
    )


@pytest.mark.parametrize("dimension", [8, 16, 32, 64])
def test_fake_provider_is_repeatable_float32_unit_space(
    sources, config, embedding_config, dimension
):
    chosen = EmbeddingConfig.model_validate_json(
        embedding_config.model_copy(update={"dimension": dimension}).model_dump_json()
    )
    first = build_index(build(sources, config), chosen)
    second = build_index(build(sources, config), chosen)
    assert first == second
    assert all(len(r.vector) == dimension for r in first.embeddings)
    assert first.manifest.lifecycle == "candidate"
    assert first.manifest.semantic_quality == "not_evaluated_fake_vectors"
    assert first.manifest.retrieval_version == "not_implemented"
    for name, value in [
        ("embedding-config", chosen),
        ("index-manifest", first.manifest),
        ("index-candidate", first),
    ]:
        schema = json.loads((ROOT / f"contracts/knowledge/v1/{name}.v1.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(value.model_dump(mode="json"))


def test_space_changes_are_isolated(sources, config, embedding_config):
    chunks = build(sources, config)
    first = build_index(chunks, embedding_config)
    second = build_index(chunks, embedding_config.model_copy(update={"dimension": 16}))
    assert first.manifest.space_id != second.manifest.space_id
    assert first.manifest.index_id != second.manifest.index_id
    assert {r.embedding_id for r in first.embeddings}.isdisjoint(
        r.embedding_id for r in second.embeddings
    )


class CountingProvider(FakeEmbeddingProvider):
    calls = 0

    def embed(self, text):
        self.calls += 1
        return super().embed(text)


def test_identical_bodies_share_cache_but_keep_separate_citations(
    sources, config, embedding_config
):
    replace(sources, "# First\n\nShared body.\n", 0)
    replace(sources, "# Second\n\nShared body.\n", 1)
    provider = CountingProvider(embedding_config)
    candidate = build_index(build(sources, config), embedding_config, provider=provider)
    assert provider.calls == 1
    assert candidate.manifest.chunk_count == 2
    assert candidate.manifest.embedding_count == 1
    assert len({c.source_ref for c in candidate.chunks.chunks}) == 2
    assert len({e.chunk_id for e in candidate.manifest.entries}) == 2
    replay_provider = CountingProvider(embedding_config)
    replay = build_index(
        candidate.chunks,
        embedding_config,
        provider=replay_provider,
        cache={r.embedding_id: r for r in candidate.embeddings},
    )
    assert replay == candidate
    assert replay_provider.calls == 0


def test_metadata_change_reuses_vectors_new_index_drops_removed_chunks(
    sources, config, embedding_config
):
    original = build_index(build(sources, config), embedding_config)
    sources[0]["sources"][0]["documents"][0]["access_class"] = "project_internal"
    provider = CountingProvider(embedding_config)
    changed = build_index(
        build(sources, config),
        embedding_config,
        provider=provider,
        cache={r.embedding_id: r for r in original.embeddings},
    )
    assert original.manifest.index_id != changed.manifest.index_id
    assert original.embeddings == changed.embeddings
    assert provider.calls == 0
    replace(sources, "# Empty document\n", 0)
    reduced = build_index(build(sources, config), embedding_config)
    assert reduced.manifest.chunk_count < changed.manifest.chunk_count
    assert {e.chunk_id for e in reduced.manifest.entries} < {
        e.chunk_id for e in changed.manifest.entries
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", "bedrock"),
        ("dimension", 0),
        ("dimension", True),
        ("dimension", 9),
        ("normalization", "none"),
        ("distance", "l2"),
        ("region", "eu-central-1"),
        ("model_id", "other"),
        ("inference_profile", "arn:placeholder"),
        ("vector_format", "float64"),
    ],
)
def test_only_pinned_offline_space_is_supported(embedding_config, field, value):
    raw = embedding_config.model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        EmbeddingConfig.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "failure", ["dimension", "nan", "infinity", "zero", "float64", "too_large"]
)
def test_bad_provider_stops_build_without_artifact(sources, config, embedding_config, failure):
    class BadProvider(FakeEmbeddingProvider):
        def embed(self, text):
            good = super().embed(text)
            return {
                "dimension": good[:-1],
                "nan": (float("nan"),) + good[1:],
                "infinity": (float("inf"),) + good[1:],
                "zero": (0.0,) * len(good),
                "float64": (0.12345678901234,) + good[1:],
                "too_large": (1e100,) + good[1:],
            }[failure]

    with pytest.raises((ValueError, OverflowError)):
        build_index(
            build(sources, config), embedding_config, provider=BadProvider(embedding_config)
        )


def test_config_mismatch_and_corrupt_cache_fail_closed(sources, config, embedding_config):
    chunks = build(sources, config)
    with pytest.raises(CorpusError, match="provider_config"):
        build_index(
            chunks,
            embedding_config,
            provider=FakeEmbeddingProvider(embedding_config.model_copy(update={"dimension": 16})),
        )
    candidate = build_index(chunks, embedding_config)
    record = candidate.embeddings[0]
    bad = record.model_copy(update={"vector_checksum": "0" * 64})
    with pytest.raises(ValidationError):
        build_index(chunks, embedding_config, cache={record.embedding_id: bad})
    wrong = candidate.embeddings[-1]
    if wrong != record:
        with pytest.raises(CorpusError, match="cache_binding"):
            build_index(chunks, embedding_config, cache={record.embedding_id: wrong})


@pytest.mark.parametrize(
    "failure",
    ["missing", "extra", "order", "vector", "checksum", "entry", "source", "space", "active"],
)
def test_closed_graph_rejects_tampering_even_with_recomputed_manifest_id(
    sources, config, embedding_config, failure
):
    raw = deepcopy(build_index(build(sources, config), embedding_config).model_dump(mode="json"))
    if failure == "missing":
        raw["embeddings"].pop()
    elif failure == "extra":
        extra = deepcopy(raw["embeddings"][0])
        extra["content_checksum"] = "a" * 64
        extra["embedding_id"] = embedding_id(extra["content_checksum"], extra["space_id"])
        raw["embeddings"].append(extra)
        raw["embeddings"].sort(key=lambda r: r["embedding_id"])
    elif failure == "order":
        raw["embeddings"].reverse()
    elif failure == "vector":
        raw["embeddings"][0]["vector"][0] *= -1
    elif failure == "checksum":
        raw["embeddings"][0]["vector_checksum"] = "0" * 64
    elif failure == "entry":
        raw["manifest"]["entries"][0]["vector_checksum"] = "0" * 64
    elif failure == "source":
        raw["manifest"]["chunk_manifest_id"] = "chunks-sha256-" + "0" * 64
    elif failure == "space":
        raw["manifest"]["embedding_config"]["dimension"] = 16
        raw["manifest"]["space_id"] = EmbeddingConfig.model_validate_json(
            json.dumps(raw["manifest"]["embedding_config"])
        ).space_id()
    else:
        raw["manifest"]["lifecycle"] = "active"
    rebind(raw)
    with pytest.raises(ValidationError):
        IndexCandidate.model_validate_json(json.dumps(raw))


def test_index_cli_compiles_pinned_sources_and_never_overwrites(
    sources, config, embedding_config, tmp_path, capsys, monkeypatch
):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(sources[0]))
    config_path = tmp_path / "chunker.json"
    config_path.write_text(config.model_dump_json())
    embedding_path = tmp_path / "embedding.json"
    embedding_path.write_text(embedding_config.model_dump_json())
    output = tmp_path / "candidate.json"
    monkeypatch.setenv("DATABASE_URL", "invalid-private-marker")
    repos = list(sources[1].values())
    argv = [
        "index-build",
        "--registry",
        str(registry),
        "--chunker-config",
        str(config_path),
        "--embedding-config",
        str(embedding_path),
        "--retailops-repo",
        str(repos[0]),
        "--ai-repo",
        str(repos[1]),
        "--output",
        str(output),
    ]
    assert main(argv) == 0
    original = output.read_bytes()
    assert load_index_candidate(output) == build_index(build(sources, config), embedding_config)
    assert output.stat().st_mode & 0o777 == 0o600
    assert main(argv) == 2
    assert output.read_bytes() == original
    captured = capsys.readouterr()
    assert "invalid-private-marker" not in captured.out + captured.err
    assert "Body 0" not in captured.out + captured.err


@pytest.mark.parametrize(
    "payload", [b'{"schema_version":"1.0","schema_version":"1.0"}', b'{"x":NaN}', b"\xff"]
)
def test_loader_rejects_duplicate_keys_nonfinite_and_invalid_utf8(tmp_path, payload):
    path = tmp_path / "candidate.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        load_index_candidate(path)


@pytest.mark.parametrize(
    "database,user",
    [
        ("retailops_mlflow", "mlflow_app"),
        ("retailops_ai", "retailops_admin"),
        ("retailops", "ai_app"),
    ],
)
def test_store_requires_isolated_ai_role_and_database(database, user):
    settings = Settings(
        APP_ENV="local",
        ARTIFACT_ROOT=ROOT / ".local/artifacts",
        DATABASE_URL=f"postgresql+psycopg://{user}:private-marker@db:5432/{database}",
    )
    with pytest.raises(CorpusError, match="isolated_ai"):
        index_engine(settings)


def test_fake_build_requires_nonempty_local_or_test_corpus(sources, config, embedding_config):
    sources[0]["environment"] = "dev"
    with pytest.raises(CorpusError, match="environment_forbidden"):
        build_index(build(sources, config), embedding_config)
    sources[0]["environment"] = "test"
    replace(sources, "# Empty\n", 0)
    replace(sources, "# Empty\n", 1)
    with pytest.raises(CorpusError, match="empty_index"):
        build_index(build(sources, config), embedding_config)


def test_store_cli_rejects_environment_before_connecting(
    sources, config, embedding_config, tmp_path, monkeypatch, capsys
):
    from retailops_ai.adapters import vector_store

    path = tmp_path / "candidate.json"
    path.write_text(build_index(build(sources, config), embedding_config).model_dump_json())
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))

    def forbidden(settings):
        pytest.fail("Environment mismatch must not connect to a database.")

    monkeypatch.setattr(vector_store, "index_engine", forbidden)
    assert main(["index-store", "--candidate", str(path)]) == 2
    assert capsys.readouterr().err.strip() == '{"error":"index_storage_failed"}'


def test_store_cli_redacts_database_error_and_disposes_engine(
    sources, config, embedding_config, tmp_path, monkeypatch, capsys
):
    from sqlalchemy.exc import OperationalError

    from retailops_ai.adapters import vector_store

    path = tmp_path / "candidate.json"
    path.write_text(build_index(build(sources, config), embedding_config).model_dump_json())
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))

    class Engine:
        disposed = False

        def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(vector_store, "index_engine", lambda _: engine)

    def fail(engine, candidate):
        raise OperationalError(
            "private-sql-marker",
            {"password": "private-credential-marker"},
            RuntimeError("private-document-marker"),
        )

    monkeypatch.setattr(vector_store, "store_candidate", fail)
    assert main(["index-store", "--candidate", str(path)]) == 2
    captured = capsys.readouterr()
    assert "private-" not in captured.out + captured.err
    assert captured.err.strip() == '{"error":"index_storage_failed"}'
    assert engine.disposed


def test_index_and_embedding_config_loaders_bound_input(tmp_path, monkeypatch):
    import retailops_ai.pipelines.indexes as pipeline

    path = tmp_path / "large.json"
    path.write_bytes(b" " * 64001)
    with pytest.raises(CorpusError, match="config_too_large"):
        load_embedding_config(path)
    monkeypatch.setattr(pipeline, "MAX_INDEX_BYTES", 32)
    with pytest.raises(CorpusError, match="candidate_too_large"):
        load_index_candidate(path)
