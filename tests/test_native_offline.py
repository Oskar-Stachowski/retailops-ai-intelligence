"""Explicit offline pin/provider boundary; no production or semantic qualification."""

import json
from pathlib import Path

import pytest
from test_chunks import build
from test_chunks import config as config
from test_chunks import sources as sources
from test_index_lifecycle import approval, request
from test_indexes import embedding_config as embedding_config

from retailops_ai.agent.evaluation import evaluator_checksum
from retailops_ai.agent.execution import ToolExecutor, ToolFailure
from retailops_ai.agent.graph_config import AgentGraphConfig, load_graph_config
from retailops_ai.assistant.native_runtime import NativeOfflineConfig, load_native_offline_config
from retailops_ai.assistant.routes import QuestionRoutes
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.knowledge.releases import IndexPin
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.releases import validate_candidate

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_QUESTION = "What does the synthetic scope fixture say?"


def offline_config(candidate, catalog, sales):
    """Test-owned fake vectors and proposed route labels; never an owner acceptance."""
    graph = load_graph_config(ROOT / "agent/graph.evaluate.fake.suggestion-outbox.v1.json").config
    raw = graph.model_dump(mode="json")
    raw["chat"]["embeddings"] = candidate.manifest.embedding_config.model_dump(mode="json")
    raw["chat"]["knowledge_mode"] = "offline_test"
    raw["chat"]["knowledge_index_id"] = candidate.manifest.index_id
    chunk = next(c for c in candidate.chunks.chunks if "Body 0." in c.text)
    raw["policy"]["document_rules"] = [
        dict(
            question=DOCUMENT_QUESTION,
            intent="documentation",
            requirements=[
                dict(
                    requirement_id="synthetic-scope",
                    description="The test-only registered scope paragraph.",
                    supports=[
                        dict(
                            chunk_id=chunk.chunk_id,
                            chunk_sha256=canonical_sha256(chunk.model_dump(mode="json")),
                            quote="Body 0.",
                        )
                    ],
                )
            ],
        )
    ]
    graph = AgentGraphConfig.model_validate_json(json.dumps(raw))
    routes = json.loads(
        (ROOT / "agent/question-routes.suggestion-outbox.proposed.v1.json").read_bytes()
    )
    routes["routes"] = [
        r for r in routes["routes"] if r["intent"] not in {"documentation", "verified_state"}
    ] + [dict(question=DOCUMENT_QUESTION, intent="documentation")]
    routes["graph_config_id"] = graph.config_id()
    pin = IndexPin(
        schema_version="1.0",
        environment="test",
        lane="offline_test",
        purpose="lifecycle_validation_only",
        generation=1,
        request_id=request().request_id,
        review_id=approval(candidate).review_id,
        validation_id=validate_candidate(candidate).validation_id,
        manifest=candidate.manifest,
    )
    return NativeOfflineConfig(
        schema_version="1.0",
        runtime_version="assistant-native-offline-v1",
        environment="test",
        code_sha256=evaluator_checksum(),
        source_dataset_id=catalog.source_dataset_id,
        snapshot_id=catalog.snapshot_id,
        source_catalog_sha256=catalog.checksum(),
        curated_dataset_id=sales.curated_dataset_id,
        full_dq_replay_id=sales.full_dq_replay_id,
        day_coverage_id=sales.day_coverage_id,
        channel="store",
        graph=graph,
        routes=QuestionRoutes.model_validate_json(json.dumps(routes)),
        pin=pin,
    )


@pytest.fixture
def runtime_config(sources, config, embedding_config):
    from test_assistant_routes import catalog

    class QualifiedBinding:
        curated_dataset_id = "curated-sha256-" + "d" * 64
        full_dq_replay_id = "full-dq-replay-sha256-" + "e" * 64
        day_coverage_id = "day-coverage-sha256-" + "f" * 64

    return offline_config(
        build_index(build(sources, config), embedding_config), catalog(), QualifiedBinding()
    )


def test_native_config_is_strict_and_bound_to_current_code(runtime_config, tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(runtime_config.model_dump_json())
    assert load_native_offline_config(path) == runtime_config
    raw = runtime_config.model_dump(mode="json")
    raw["code_sha256"] = "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="code_mismatch"):
        load_native_offline_config(path)
    path.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_native_offline_config(path)
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="too_large"):
        load_native_offline_config(path)


@pytest.mark.parametrize("field", ["embedding", "chat", "pin"])
def test_offline_configuration_cannot_select_aws_or_retrieval(runtime_config, field):
    raw = runtime_config.model_dump(mode="json")
    if field == "embedding":
        raw["graph"]["chat"]["embeddings"] = json.loads(
            (ROOT / "agent/graph.evaluate.fake.suggestion-outbox.v1.json").read_bytes()
        )["chat"]["embeddings"]
    elif field == "chat":
        raw["graph"]["chat"]["model"]["provider"] = "bedrock"
    else:
        raw["pin"]["lane"] = "retrieval"
    with pytest.raises(ValueError):
        NativeOfflineConfig.model_validate_json(json.dumps(raw))


def settings_args():
    return dict(
        APP_ENV="test",
        ARTIFACT_ROOT="./artifacts",
        DATABASE_URL="postgresql+psycopg://ai_app:synthetic@127.0.0.1:1/retailops_ai",
        ASSISTANT_NATIVE_OFFLINE_FILE="./offline.json",
        ASSISTANT_SOURCE_IMPORT="./source",
        ASSISTANT_CURATED="./curated",
        ASSISTANT_REPLAY="./replay",
        ASSISTANT_COVERAGE="./coverage",
        ASSISTANT_PRODUCER_DATABASE_URL="postgresql+psycopg://reader:synthetic@127.0.0.1:1/producer",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"APP_ENV": "local"},
        {"RAG_BEDROCK_ENABLED": True},
        {"ASSISTANT_RUNTIME_FILE": "./runtime.json"},
        {"ASSISTANT_CURATED": None},
        {"ASSISTANT_PRODUCER_DATABASE_URL": None},
        {"DATABASE_URL": None},
        {"ASSISTANT_SOURCE_IMPORT": None},
    ],
)
def test_settings_close_offline_environment_and_dependencies(change):
    with pytest.raises(ValueError):
        Settings(**(settings_args() | change))


def test_offline_private_settings_do_not_expose_paths_or_credentials():
    value = Settings(**settings_args())
    rendered = repr(value) + value.model_dump_json()
    assert "synthetic" not in rendered and "offline.json" not in rendered
    with pytest.raises(ValueError):
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", ASSISTANT_CURATED="./curated")


def test_fake_index_lane_requires_test_executor_opt_in(runtime_config):
    from test_agent_tools import NOW, authority, policy

    auth, token = authority()
    for env, allow, permitted in [
        ("test", True, True),
        ("test", False, False),
        ("local", True, False),
    ]:
        if env == "local" and allow:
            with pytest.raises(ValueError, match="fixture_tools_require_test"):
                ToolExecutor(auth, {}, policy(), env, allow_fixtures=allow, clock=lambda: NOW)
            continue
        executor = ToolExecutor(auth, {}, policy(), env, allow_fixtures=allow, clock=lambda: NOW)
        if permitted:
            assert executor.open_session(token, pin=runtime_config.pin).pin == runtime_config.pin
        else:
            with pytest.raises(ToolFailure, match="unavailable"):
                executor.open_session(token, pin=runtime_config.pin)
