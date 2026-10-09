"""Real SQL/HTTP integration in an explicitly provisioned disposable AI12 database."""

import asyncio
import json
import os
import secrets
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from test_chunks import build
from test_chunks import config as config
from test_chunks import sources as sources
from test_day_qualification import prepared as source_prepared
from test_index_lifecycle import approval, request
from test_indexes import embedding_config as embedding_config
from test_native_offline import DOCUMENT_QUESTION, offline_config

from retailops_ai.adapters.index_lifecycle import current_index, qualify_index, switch_index
from retailops_ai.adapters.native_inventory_tool import NativeInventoryReader
from retailops_ai.adapters.qualified_sales_tool import QualifiedSalesReader
from retailops_ai.adapters.vector_store import store_candidate
from retailops_ai.agent.evidence import required_calls
from retailops_ai.agent.execution import READ_CAPABILITIES
from retailops_ai.agent.graph_contracts import GraphRequest
from retailops_ai.api.app import create_app
from retailops_ai.assistant.source_catalog import load_source_catalog
from retailops_ai.config import Settings
from retailops_ai.data_contracts.common import end_of_day
from retailops_ai.knowledge.contracts import REPOSITORIES
from retailops_ai.knowledge.releases import SwitchRequest
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.security.local import token_fingerprint
from retailops_ai.stockout_jobs.read_contracts import StockoutQuery


@pytest.fixture(scope="module", params=["physical"])
def prepared(request, tmp_path_factory):
    # An optional local cache only reuses fixture files; every server reader still
    # verifies Source, Curated, DQ and coverage bindings independently.
    cache_path = os.getenv("AI12_NATIVE_PARENTS_CACHE")
    cache = Path(cache_path) if cache_path else None
    if cache and cache.is_file():
        paths = tuple(Path(p) for p in json.loads(cache.read_text()))
        if len(paths) == 4 and all(p.is_dir() for p in paths):
            return {"args": paths}
    value = source_prepared.__wrapped__(request, tmp_path_factory)
    if cache:
        stable = cache.parent / ("native-offline-fixture-parents-" + secrets.token_hex(6))
        paths = []
        for i, path in enumerate(value["args"]):
            target = stable / str(i) / path.name
            target.parent.mkdir(parents=True, mode=0o700)
            shutil.copytree(path, target)
            paths.append(target)
        cache.write_text(json.dumps([str(p) for p in paths]))
        value = {"args": tuple(paths)}
    return value


@pytest.fixture(scope="module")
def databases():
    ai, producer = (
        os.getenv("AI12_NATIVE_DATABASE_URL"),
        os.getenv("AI12_NATIVE_PRODUCER_DATABASE_URL"),
    )
    admin = os.getenv("AI12_NATIVE_PRODUCER_ADMIN_URL")
    if not ai or not producer or not admin:
        if os.getenv("REQUIRE_AI12_NATIVE_POSTGRES") == "1":
            pytest.fail("explicit disposable AI12 database URLs are required")
        pytest.skip("dedicated AI12 PostgreSQL acceptance is not provisioned")
    engines = [create_engine(url, hide_parameters=True) for url in (ai, producer, admin)]
    try:
        yield ai, producer, *engines
    finally:
        for engine in engines:
            engine.dispose()


def authority_file(path, product, store, stocks):
    now = datetime.now(UTC)
    tokens = [secrets.token_urlsafe(32) for _ in range(2)]
    grant = dict(
        principal_id="native-offline-operator",
        roles=["operator"],
        capabilities=[
            "assistant:query",
            *sorted(set(READ_CAPABILITIES.values())),
            "anomaly:read",
        ],
        scope=dict(product_ids=[product], selling_location_ids=[store], channels=["store"]),
        stockout_scope=dict(product_ids=[product], stock_location_ids=stocks),
        knowledge_scope=dict(
            environment="test",
            repositories=list(REPOSITORIES),
            access_classes=["public_project"],
            document_statuses=["specified"],
        ),
    )
    raw = dict(
        schema_version="1.0",
        policy_id="native-offline-acceptance",
        grants=[grant | {"principal_id": f"native-offline-operator-{i}"} for i in range(2)],
        credentials=[
            dict(
                principal_id=f"native-offline-operator-{i}",
                token_sha256=token_fingerprint(token),
                not_before=(now - timedelta(seconds=1)).isoformat(),
                expires_at=(now + timedelta(hours=1)).isoformat(),
                revoked=False,
            )
            for i, token in enumerate(tokens)
        ],
    )
    path.write_text(json.dumps(raw))
    path.chmod(0o600)
    return tokens, raw


@pytest.mark.parametrize("prepared", ["physical"], indirect=True)
def test_all_eight_native_adapters_http_sql_persistence_revocation_and_index_drift(
    databases,
    prepared,
    sources,
    config,
    embedding_config,
    tmp_path,
    monkeypatch,
):
    ai_url, producer_url, engine, producer_engine, producer_admin = databases
    replay, coverage, curated, source = prepared["args"]
    catalog = load_source_catalog(source)
    sales = QualifiedSalesReader(replay, coverage, curated, source, "test")
    inventory = NativeInventoryReader(curated, source, "test")
    product = catalog.products[0].product_id
    store = next(a.selling_location_id for a in catalog.assignments if a.channel == "store")
    stocks = sorted({r.stock_location_id for rows in inventory._routes.values() for r in rows})
    assert stocks
    # Test-owned Producer records; the runtime itself only receives SELECT grants.
    with producer_admin.begin() as conn:
        conn.execute(
            text("""INSERT INTO realtime_event_log
            (event_id,schema_version,event_type,payload,status,ingested_at,processed_at,updated_at)
            VALUES(:id,'1.0','sale_completed',CAST(:payload AS jsonb),
              'failed_dead_lettered',clock_timestamp()-interval '4 seconds',NULL,
              clock_timestamp()-interval '2 seconds')"""),
            {
                "id": UUID(secrets.token_hex(16)),
                "payload": json.dumps(
                    {
                        "product_id": product,
                        "store_id": store,
                        "channel": "store",
                        "private_field": "native-ignored-payload-sentinel",
                    }
                ),
            },
        )
    candidate = build_index(build(sources, config), embedding_config)
    store_candidate(engine, candidate)
    qualify_index(
        engine,
        candidate.manifest.index_id,
        "test",
        "offline_test",
        approval(candidate),
        validate_candidate(candidate),
    )
    active = current_index(engine, "test", "offline_test")
    activation = SwitchRequest.model_validate_json(
        json.dumps(
            request().model_dump(mode="json")
            | {
                "target_index_id": candidate.manifest.index_id,
                "expected_generation": active.generation if active else 0,
                "request_id": "rag-change-" + secrets.token_hex(16),
            }
        )
    )
    pin = switch_index(engine, activation).pin
    bound = offline_config(candidate, catalog, sales)
    bound = type(bound).model_validate_json(bound.model_copy(update={"pin": pin}).model_dump_json())
    runtime_path = tmp_path / "native-runtime.json"
    runtime_path.write_text(bound.model_dump_json())
    auth_path = tmp_path / "authority.json"
    tokens, policy = authority_file(auth_path, product, store, stocks)
    kwargs = dict(
        APP_ENV="test",
        ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        DATABASE_URL=ai_url,
        API_AUTH_FILE=auth_path,
        ASSISTANT_NATIVE_OFFLINE_FILE=runtime_path,
        ASSISTANT_SOURCE_IMPORT=source,
        ASSISTANT_CURATED=curated,
        ASSISTANT_REPLAY=replay,
        ASSISTANT_COVERAGE=coverage,
        ASSISTANT_PRODUCER_DATABASE_URL=producer_url,
        ASSISTANT_SUGGESTION_OUTBOX_ENABLED=True,
    )
    # No AWS SDK client may be constructed, even accidentally by a fallback.
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("AWS client construction"))
    monkeypatch.setattr(
        boto3.session.Session,
        "client",
        lambda *a, **k: pytest.fail("AWS session client construction"),
    )
    headers_by_owner = [{"Authorization": "Bearer " + token} for token in tokens]
    now = datetime.now(UTC)
    # This immutable fixture's channel assignments end in August. Historical
    # requests remain valid; present forecast horizons must be rejected by HTTP.
    historical_day = next(a.effective_from for a in catalog.assignments if a.channel == "store")
    snapshots = []
    durations = []
    with TestClient(create_app(Settings(**kwargs)), base_url="http://127.0.0.1") as client:
        backend = client.app.state.assistant_backend
        assert len(backend.adapters) == 8
        assert len(backend.native_tools) == 7
        assert backend.adapters["search_knowledge"].source_kind == "fixture"
        assert client.get("/ready").status_code == 200
        with backend.producer_engine.begin() as conn:
            conn.execute(text("SET TimeZone='Europe/Warsaw'"))
        assert not asyncio.run(backend.check())
        assert client.get("/ready").status_code == 503
        with backend.producer_engine.begin() as conn:
            conn.execute(text("SET TimeZone='UTC'"))
        assert client.get("/ready").status_code == 200
        for route_number, intent in enumerate(
            (
                "sales",
                "inventory",
                "forecast",
                "risk",
                "anomalies",
                "operations",
                "model",
                "documentation",
            )
        ):
            headers = headers_by_owner[route_number // 4]
            question = next(r.question for r in bound.routes.routes if r.intent == intent)
            start = now.date() if intent in {"forecast", "risk"} else historical_day
            end = start + timedelta(days=6) if intent == "risk" else start
            body = dict(
                question=question,
                scope={
                    "product_ids": [product],
                    "store_ids": [store],
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                },
                conversation_id=None,
            )
            import time

            begun = time.monotonic()
            response = client.post("/api/v1/assistant/queries", headers=headers, json=body)
            durations.append(time.monotonic() - begun)
            if intent in {"forecast", "risk"}:
                assert response.status_code == 422
                principal = backend.runner().executor.authority.authenticate(
                    headers["Authorization"]
                )
                assert principal is not None
                origin = end_of_day(now.date() - timedelta(days=1))
                graph_request = GraphRequest.model_validate_json(
                    json.dumps(
                        dict(
                            schema_version="1.0",
                            question=question,
                            intent=intent,
                            scope=dict(
                                product_ids=[product], selling_location_ids=[store], channel="store"
                            ),
                            as_of=origin.isoformat(),
                            window=dict(start=start.isoformat(), end=end.isoformat()),
                            comparison_window=None,
                            limit=5,
                        )
                    )
                )
                call = required_calls(graph_request)[0]
                output = asyncio.run(
                    backend.adapters[call.tool].execute(call, principal, bound.pin)
                )
                if intent == "forecast":
                    assert output.result.page.data_status == "no_data"
                else:
                    assert output.status == "no_data" and output.items == []
                    page = backend.adapters[call.tool].reader.list(
                        StockoutQuery(as_of=origin, limit=5), principal
                    )
                    assert page.data_status == "no_data" and page.items == ()
                continue
            assert response.status_code == 200, (intent, response.text)
            answer = response.json()
            assert "native-ignored-payload-sentinel" not in response.text
            trace = client.get("/api/v1/assistant/runs/" + answer["trace_id"], headers=headers)
            assert trace.status_code == 200
            assert trace.json()["status"] == "succeeded"
            if intent == "documentation":
                assert question == DOCUMENT_QUESTION
                assert answer["outcome"] == "answered"
                assert answer["citations"]
            elif intent in {"forecast", "risk", "anomalies"}:
                assert answer["outcome"] == "insufficient_evidence"
                assert answer["recommended_actions"] == []
            elif intent == "operations":
                assert answer["outcome"] == "answered"
                assert len(answer["recommended_actions"]) == 1
                with engine.connect() as conn:
                    assert (
                        conn.scalar(
                            text(
                                "SELECT count(*) FROM ai.assistant_suggestions WHERE trace_id=:id"
                            ),
                            {"id": UUID(answer["trace_id"])},
                        )
                        == 1
                    )
            snapshots.append((intent, answer["trace_id"], trace.json(), headers))
            if intent == "operations":
                with engine.connect() as conn:
                    assert (
                        conn.scalar(
                            text(
                                "SELECT count(*) FROM ai.assistant_suggestion_outbox WHERE trace_id=:id"
                            ),
                            {"id": UUID(answer["trace_id"])},
                        )
                        == 1
                    )
            with engine.connect() as conn:
                access = conn.scalar(
                    text("SELECT access_context FROM ai.assistant_runs WHERE trace_id=:id"),
                    {"id": UUID(answer["trace_id"])},
                )
                if intent == "anomalies":
                    assert "anomaly:read" in access["required_capabilities"]
                if intent == "inventory":
                    assert "inventory:read" in access["required_capabilities"]
                if intent == "model":
                    assert {"forecast:read", "anomaly:read", "model:read"} <= set(
                        access["required_capabilities"]
                    )
        assert max(durations) < bound.graph.chat.tool_policy.request_deadline_seconds
        # Real SELECT-only grants: a write must be denied before changing any row.
        from sqlalchemy.exc import DBAPIError

        with producer_engine.begin() as conn:
            with pytest.raises(DBAPIError):
                conn.execute(text("DELETE FROM realtime_event_log WHERE false"))
    # Grants are a process snapshot: restart to apply the changed policy.
    policy["grants"][1]["capabilities"].remove("anomaly:read")
    policy["grants"][1]["knowledge_scope"]["repositories"] = [REPOSITORIES[1]]
    auth_path.write_text(json.dumps(policy))
    auth_path.chmod(0o600)
    # Recreate the API/store; authorized persisted trace bytes survive process objects.
    with TestClient(create_app(Settings(**kwargs)), base_url="http://127.0.0.1") as client:
        for intent, trace_id, trace, headers in snapshots:
            response = client.get("/api/v1/assistant/runs/" + trace_id, headers=headers)
            if intent in {"anomalies", "model", "documentation"}:
                assert response.status_code == 404
                continue
            assert response.status_code == 200 and response.json() == trace
        # Backend checks the frozen active generation before serving any request.
        # A raw pointer edit is itself rejected by the SQL lifecycle guard.
        with engine.begin() as conn:
            with pytest.raises(DBAPIError, match="rag_pointer_generation_mismatch"):
                conn.execute(
                    text(
                        "UPDATE ai.rag_active_indexes SET generation=generation+1 WHERE environment='test' AND lane='offline_test'"
                    )
                )
        assert asyncio.run(client.app.state.assistant_backend.check())
        # Activate another fully validated test index through the audited writer.
        next_embeddings = type(embedding_config).model_validate_json(
            embedding_config.model_copy(
                update={"dimension": 16 if embedding_config.dimension != 16 else 8}
            ).model_dump_json()
        )
        next_candidate = build_index(candidate.chunks, next_embeddings)
        store_candidate(engine, next_candidate)
        qualify_index(
            engine,
            next_candidate.manifest.index_id,
            "test",
            "offline_test",
            approval(next_candidate),
            validate_candidate(next_candidate),
        )
        change = SwitchRequest.model_validate_json(
            activation.model_copy(
                update={
                    "target_index_id": next_candidate.manifest.index_id,
                    "expected_generation": pin.generation,
                    "request_id": "rag-change-" + secrets.token_hex(16),
                }
            ).model_dump_json()
        )
        assert switch_index(engine, change).pin.generation == pin.generation + 1
        assert not asyncio.run(client.app.state.assistant_backend.check())
        assert client.get("/ready").status_code == 503
    report_path = os.getenv("AI12_NATIVE_REPORT")
    if report_path:
        from retailops_ai.agent.evaluation import evaluator_checksum

        report = dict(
            status="passed",
            application_code_sha256=evaluator_checksum(),
            tools=sorted(backend.adapters),
            native_read_adapters=7,
            chat_provider="fake",
            embedding_provider="fake",
            semantic_quality="not_evaluated_fake_vectors",
            aws_executed=False,
            source_fixture="full-raw-dq-v2/physical + day-coverage-v1/physical",
            source_dataset_id=catalog.source_dataset_id,
            curated_dataset_id=sales.curated_dataset_id,
            http_succeeded_intents=[entry[0] for entry in snapshots],
            http_rejected_expired_source_horizons=["forecast", "risk"],
            canonical_forecast_and_stockout_sql="missing_outputs_preserved",
            positive_model_publication="not_qualified_by_this_run",
            persisted_answers=len(snapshots),
            persisted_operations_review_suggestions=1,
            persisted_operations_review_outbox_events=1,
            authorized_trace_restart="passed",
            native_and_knowledge_trace_revocation="passed_after_policy_restart",
            producer_write_denied=True,
            utc_readiness_rejection=True,
            index_generation_drift_rejected=True,
            query_duration_seconds=durations,
            deadline_seconds=bound.graph.chat.tool_policy.request_deadline_seconds,
        )
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
