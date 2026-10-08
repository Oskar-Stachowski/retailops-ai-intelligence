"""Native runtime construction, grants, pins and budgets without AWS clients."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from test_assistant import setup
from test_assistant_routes import catalog
from test_document_runtime import runtime_config
from test_native_offline import settings_args

from retailops_ai.agent.graph_contracts import GraphRequest
from retailops_ai.assistant.native_bedrock import (
    NativeBedrockAssistant,
    NativeLazyBedrock,
    NativeRuntimeConfig,
    load_native_runtime,
)
from retailops_ai.assistant.service import AssistantError
from retailops_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def native_config():
    source = catalog()
    raw = runtime_config(source).model_dump(mode="json")
    raw["runtime_version"] = "assistant-native-bedrock-v1"
    routes = json.loads((ROOT / "agent/question-routes.prepaid.proposed.v1.json").read_bytes())
    # Test-owned accepted route, never an acceptance of the published proposals.
    routes.update(labels_state="accepted", graph_config_id=raw["graph"]["code_sha256"])
    routes["graph_config_id"] = runtime_config(source).graph.config_id()
    routes["routes"] = [r for r in routes["routes"] if r["intent"] == "operations"]
    raw.update(
        routes=routes,
        curated_dataset_id="curated-sha256-" + "a" * 64,
        full_dq_replay_id="full-dq-replay-sha256-" + "b" * 64,
        day_coverage_id="day-coverage-sha256-" + "c" * 64,
        embedding_input_per_million_usd="0.02",
        embedding_max_cost_usd="0.00004",
    )
    return NativeRuntimeConfig.model_validate_json(json.dumps(raw))


def test_native_runtime_builds_all_adapters_without_sql_or_aws(
    native_config, tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("AWS client was constructed before authorized execution")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    _, _, authority, _, _, _ = setup(tmp_path)
    source = catalog()
    sales = SimpleNamespace(
        environment="test",
        source_dataset_id=source.source_dataset_id,
        curated_dataset_id=native_config.curated_dataset_id,
        full_dq_replay_id=native_config.full_dq_replay_id,
        day_coverage_id=native_config.day_coverage_id,
    )
    inventory = SimpleNamespace(
        environment="test",
        source_dataset_id=source.source_dataset_id,
        curated_dataset_id=native_config.curated_dataset_id,
    )
    engine = create_engine("postgresql+psycopg://ai_app:synthetic@127.0.0.1:1/retailops_ai")
    try:
        backend = NativeBedrockAssistant(
            native_config, source, sales, inventory, engine, engine, authority
        )
        assert len(backend.adapters) == len(backend.native_tools) == 8
        assert all(adapter.source_kind == "runtime" for adapter in backend.adapters.values())
        assert backend.chat_provider.provider is None
        assert backend.embedding_provider is None
        assert backend.runtime_config.pin.lane == "retrieval"
        monkeypatch.setattr(backend, "dependencies_ready", lambda: False)
        request = GraphRequest.model_validate_json(
            json.loads((ROOT / "agent/golden.canonical.v1.json").read_text())["cases"][0][
                "request_json"
            ]
        )
        with pytest.raises(AssistantError) as error:
            asyncio.run(backend.run(request, None))
        assert error.value.status == 424
        assert backend.chat_provider.provider is None
        assert backend.embedding_provider is None
    finally:
        engine.dispose()


@pytest.mark.parametrize("change", ["proposed", "budget", "offline", "native_v2", "graph"])
def test_native_runtime_rejects_unreviewed_or_mixed_bindings(native_config, change):
    raw = native_config.model_dump(mode="json")
    if change == "proposed":
        raw["routes"]["labels_state"] = "proposed"
    elif change == "budget":
        raw["embedding_max_cost_usd"] = "0.000001"
    elif change == "offline":
        raw["graph"]["chat"]["knowledge_mode"] = "offline_test"
    elif change == "native_v2":
        raw["graph"]["policy"]["suggestions"].update(
            policy_version="read-only-review-native-v2", native_stockout_review=True
        )
    else:
        raw["routes"]["graph_config_id"] = "agent-graph-config-sha256-" + "0" * 64
    with pytest.raises(ValidationError):
        NativeRuntimeConfig.model_validate_json(json.dumps(raw))


def test_native_runtime_loader_rejects_stale_code_duplicates_and_oversize(native_config, tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(native_config.model_dump_json())
    assert load_native_runtime(path) == native_config
    raw = native_config.model_dump(mode="json")
    raw["code_sha256"] = "0" * 64
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="code_mismatch"):
        load_native_runtime(path)
    path.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_native_runtime(path)
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(ValueError, match="too_large"):
        load_native_runtime(path)


@pytest.mark.parametrize(
    "change",
    [
        {"RAG_BEDROCK_ENABLED": False},
        {"ASSISTANT_PRODUCER_DATABASE_URL": None},
        {"ASSISTANT_RUNTIME_FILE": "./document.json"},
        {"ASSISTANT_NATIVE_OFFLINE_FILE": "./offline.json"},
        {"DATABASE_URL": None},
        {"ASSISTANT_SOURCE_IMPORT": None},
    ],
)
def test_native_settings_require_explicit_complete_dependencies(change):
    raw = settings_args() | {
        "ASSISTANT_NATIVE_OFFLINE_FILE": None,
        "ASSISTANT_NATIVE_RUNTIME_FILE": "./native.json",
        "RAG_BEDROCK_ENABLED": True,
    }
    with pytest.raises(ValidationError):
        Settings(**(raw | change))


def test_native_settings_keep_configuration_private():
    value = Settings(
        **(
            settings_args()
            | {
                "ASSISTANT_NATIVE_OFFLINE_FILE": None,
                "ASSISTANT_NATIVE_RUNTIME_FILE": "./private-native.json",
                "RAG_BEDROCK_ENABLED": True,
            }
        )
    )
    assert "private-native" not in str(value)
    assert "assistant_native_runtime_file" not in value.model_dump()


@pytest.mark.parametrize("intent", ["operations", "documentation"])
def test_native_count_bound_and_generation_use_identical_wire(native_config, intent):
    from retailops_ai.agent.chat import ChatRequest

    references = {
        "content_trust": "untrusted_reference",
        "tool_results": [{"retained_business_evidence": "native persisted facts"}],
        "citation_candidates": [{"source_ref": "reviewed"}, {"source_ref": "unrelated"}],
        "data_freshness": {"unchanged": True},
        "server_evidence_policy": {
            "request": {"intent": intent},
            "facts": [{"evidence": {"source_ref": "reviewed", "claim": "exact quote"}}],
        },
    }
    request = ChatRequest(
        "config", "synthesize", "answer", (), "question", (), json.dumps(references), 1500
    )
    seen = []

    class Transport:
        def input_token_bound(self, value):
            seen.append(value)
            return 200

        async def count_input_tokens(self, value):
            seen.append(value)
            return 100

        async def generate(self, value):
            seen.append(value)
            return "scripted reply, no SDK"

    provider = NativeLazyBedrock(native_config)
    provider.provider = Transport()
    assert provider.input_token_bound(request) == 200
    assert asyncio.run(provider.count_input_tokens(request)) == 100
    assert asyncio.run(provider.generate(request)) == "scripted reply, no SDK"
    assert seen[0] == seen[1] == seen[2]
    if intent == "operations":
        assert all(value is request for value in seen)
    else:
        projected = json.loads(seen[0].references_json)
        assert "tool_results" not in projected
        assert projected["server_evidence_policy"] == references["server_evidence_policy"]
        assert projected["citation_candidates"] == [{"source_ref": "reviewed"}]
    assert json.loads(request.references_json) == references
