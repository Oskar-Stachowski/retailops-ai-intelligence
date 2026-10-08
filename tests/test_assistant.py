import asyncio
import json
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_agent_graph import PolicyFixture
from test_agent_tools import example

from retailops_ai.adapters.agent_tools import FixtureTools
from retailops_ai.agent.evidence import required_calls
from retailops_ai.agent.execution import ToolExecutor
from retailops_ai.agent.graph import GraphRunner
from retailops_ai.agent.graph_config import load_graph_config
from retailops_ai.agent.graph_contracts import GraphRequest, GraphResult
from retailops_ai.agent.graph_traces import MemoryTraces
from retailops_ai.agent.tools import OUTPUT
from retailops_ai.api.app import create_app
from retailops_ai.assistant.contracts import AssistantQuery
from retailops_ai.assistant.service import (
    AssistantError,
    AssistantService,
    GraphAssistant,
    readable,
)
from retailops_ai.config import Settings
from retailops_ai.domain.access import KnowledgeAccess
from retailops_ai.security.local import LocalAccess, token_fingerprint
from retailops_ai.security.models import AccessPolicy, KnowledgeResourceScope

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"
PRIVATE = "private-assistant-marker"


def setup(tmp_path, *, intent="sales", capabilities=None, provider_options=None, adapters=True):
    now = datetime.now(UTC).replace(microsecond=0)
    tokens = {
        name: secrets.token_urlsafe(32)
        for name in ("owner", "foreign", "admin", "plain-admin", "viewer")
    }
    grants = []
    for name in tokens:
        operator = name in {"owner", "foreign"}
        grants.append(
            {
                "principal_id": name,
                "roles": ["operator" if operator else "viewer" if name == "viewer" else "admin"],
                "capabilities": capabilities
                if name == "owner" and capabilities is not None
                else ["assistant:query", "sales:read", "operations:read"]
                if operator
                else ["assistant:audit"]
                if name == "admin"
                else ["access:admin"]
                if name == "plain-admin"
                else ["sales:read"],
                "scope": {
                    "product_ids": [PRODUCT],
                    "selling_location_ids": [STORE],
                    "channels": ["store"],
                }
                if operator or name == "viewer"
                else None,
            }
        )
    raw = {
        "schema_version": "1.0",
        "policy_id": "assistant-test",
        "grants": grants,
        "credentials": [
            {
                "principal_id": name,
                "token_sha256": token_fingerprint(token),
                "not_before": (now - timedelta(seconds=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "revoked": False,
            }
            for name, token in tokens.items()
        ],
    }
    path = tmp_path / "authority.json"
    path.write_text(json.dumps(raw))
    path.chmod(0o600)
    authority = LocalAccess(AccessPolicy.model_validate_json(json.dumps(raw)))
    config = load_graph_config(ROOT / "agent/graph.fake.prepaid.v3.json")
    question = "What evidence is available?"
    body = {
        "question": question,
        "scope": {
            "product_ids": [PRODUCT],
            "store_ids": [STORE],
            "from": (now.date() - timedelta(days=6)).isoformat(),
            "to": now.date().isoformat(),
        },
        "conversation_id": None,
    }
    providers = []
    requests = []

    async def planner(query, principal):
        request = GraphRequest.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "question": query.question,
                    "intent": intent,
                    "scope": {
                        "product_ids": [str(x) for x in query.scope.product_ids],
                        "selling_location_ids": [str(x) for x in query.scope.store_ids],
                        "channel": "store",
                    },
                    "as_of": now.isoformat(),
                    "window": {
                        "start": query.scope.from_.isoformat(),
                        "end": query.scope.to.isoformat(),
                    },
                    "comparison_window": None,
                    "limit": 5,
                }
            )
        )
        requests.append(request)
        return request

    def runner():
        query = requests[-1]
        cases = []
        for call in required_calls(query):
            raw_output = example(call.tool, "result")
            raw_output["as_of"] = now.isoformat()
            for row in raw_output["items"]:
                row["product_id"], row["selling_location_id"] = PRODUCT, STORE
                if "window" in row:
                    row["window"] = query.window.model_dump(mode="json")
                if "lag_seconds" in row:
                    row["lag_seconds"] = 120.0
            cases.append((call, OUTPUT.validate_json(json.dumps(raw_output))))
        fixture = FixtureTools(cases)
        executor = ToolExecutor(
            authority,
            {call.tool: fixture for call, _ in cases} if adapters else {},
            config.config.chat.tool_policy,
            "test",
            allow_fixtures=True,
            clock=lambda: now,
        )
        provider = PolicyFixture(config.config.chat.model, **(provider_options or {}))
        providers.append(provider)
        return GraphRunner(executor, config, provider, MemoryTraces(config.config.policy))

    backend = GraphAssistant(
        config.config_id,
        config.config.chat.knowledge_index_id,
        45.0,
        13500,
        str(config.config.chat.budget.pricing.max_run_cost),
        "fixture",
        planner,
        runner,
    )
    return path, tokens, authority, body, backend, providers


class CaptureStore:
    """Protocol test double only. Shared PostgreSQL admission is tested in Compose."""

    def __init__(self):
        self.entries, self.answers, self.suggestions = {}, {}, {}
        self.admissions = 0
        self.fail_admission = self.fail_finish = None

    async def admit(self, lease, policy, deadline_seconds):
        if self.fail_admission:
            raise self.fail_admission
        self.admissions += 1
        self.entries[lease.run.trace_id] = (lease, lease.run)
        return lease

    async def finish(self, lease, run, answer, suggestions):
        if self.fail_finish:
            raise self.fail_finish
        self.entries[run.trace_id] = (lease, run)
        if answer:
            self.answers[answer.answer_id] = answer
        self.suggestions.update({s.recommendation_id: s for s in suggestions})

    async def get(self, trace_id, principal):
        entry = self.entries.get(trace_id)
        return (
            entry[1]
            if entry
            and readable(
                principal,
                entry[0].owner_id,
                entry[0].scope_json,
                entry[0].required_capabilities,
                entry[0].knowledge_scope,
            )
            else None
        )


def client(path, backend=None, store=None):
    return TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
            assistant_backend=backend,
            assistant_store=store,
        ),
        base_url="http://127.0.0.1",
    )


def headers(tokens, name="owner"):
    return {"Authorization": "Bearer " + tokens[name]}


def test_recommendation_physical_and_knowledge_grants_are_checked_again(tmp_path):
    from retailops_ai.agent.suggestions import SuggestionCandidate
    from retailops_ai.assistant.contracts import PersistedSuggestion
    from retailops_ai.assistant.service import recommendation_readable
    from retailops_ai.data_contracts.identity import canonical_sha256
    from retailops_ai.domain.access import StockoutAccess

    _, tokens, authority, body, backend, _ = setup(tmp_path, intent="operations")
    principal = authority.authenticate("Bearer " + tokens["owner"])
    store = CaptureStore()
    asyncio.run(
        AssistantService(backend, store, "test").query(
            AssistantQuery.model_validate_json(json.dumps(body)),
            principal,
            "Bearer " + tokens["owner"],
        )
    )
    original = next(iter(store.suggestions.values()))
    candidate = original.model_dump(mode="json", include=set(SuggestionCandidate.model_fields))
    candidate["stock_location_id"] = "warehouse-01"
    candidate["candidate_id"] = "candidate-sha256-" + canonical_sha256(
        {key: value for key, value in candidate.items() if key != "candidate_id"}
    )
    item = PersistedSuggestion.model_validate_json(
        json.dumps(original.model_dump(mode="json") | candidate)
    )
    lease = store.entries[item.trace_id][0]
    principal = replace(
        principal,
        capabilities=principal.capabilities | {"stockout:read", "knowledge:read"},
        stockout=StockoutAccess(frozenset({PRODUCT}), frozenset({"warehouse-01"})),
        knowledge=KnowledgeAccess(
            "test",
            frozenset({"Oskar-Stachowski/retailops-ai-intelligence"}),
            frozenset({"project_internal"}),
            frozenset({"implemented"}),
        ),
    )
    knowledge = KnowledgeResourceScope.model_validate_json(
        json.dumps(
            {
                "environment": "test",
                "repositories": sorted(principal.knowledge.repositories),
                "access_classes": ["project_internal"],
                "document_statuses": ["implemented"],
            }
        )
    )

    def visible(actor):
        return recommendation_readable(
            actor,
            item,
            lease.owner_id,
            lease.scope_json,
            ["operations:read", "knowledge:read"],
            knowledge,
        )

    assert visible(principal)
    for revoked in (
        replace(principal, stockout=None),
        replace(principal, capabilities=principal.capabilities - {"stockout:read"}),
        replace(
            principal, stockout=StockoutAccess(frozenset({PRODUCT}), frozenset({"other-warehouse"}))
        ),
        replace(
            principal,
            stockout=StockoutAccess(frozenset({"other-product"}), frozenset({"warehouse-01"})),
        ),
        replace(principal, knowledge=None),
        replace(
            principal,
            knowledge=replace(principal.knowledge, document_statuses=frozenset({"specified"})),
        ),
    ):
        assert not visible(revoked)


def test_http_runs_actual_graph_and_persists_response_and_safe_metadata(tmp_path):
    path, tokens, _, body, backend, providers = setup(tmp_path)
    store = CaptureStore()
    with client(path, backend, store) as c:
        correlation = str(uuid4())
        response = c.post(
            "/api/v1/assistant/queries",
            json=body,
            headers=headers(tokens) | {"X-Correlation-ID": correlation},
        )
        assert response.status_code == 200, response.text
        answer = response.json()
        assert "kind" not in answer and answer["outcome"] == "answered"
        assert len(providers) == 1 and providers[0].calls == 2
        assert UUID(answer["answer_id"]) == uuid5(UUID(answer["trace_id"]), "answer")
        assert str(store.entries[UUID(answer["trace_id"])][0].correlation_id) == correlation
        assert store.answers[UUID(answer["answer_id"])].model_dump(mode="json") == answer
        trace_path = "/api/v1/assistant/runs/" + answer["trace_id"]
        trace = c.get(trace_path, headers=headers(tokens))
        assert trace.status_code == 200
        assert trace.json()["tools"][0] == {
            "name": "get_sales_summary",
            "status": "ok",
            "source_refs": ["fixture-get_sales_summary"],
            "freshness_status": "current",
        }
        assert body["question"] not in trace.text and "observed_sales_units" not in trace.text
        assert trace.headers["cache-control"] == "no-store"
        for name, status in [
            ("foreign", 404),
            ("plain-admin", 404),
            ("admin", 200),
            ("viewer", 404),
        ]:
            result = c.get(trace_path, headers=headers(tokens, name))
            assert result.status_code == status
            if status == 404:
                unknown = c.get(
                    "/api/v1/assistant/runs/" + str(uuid4()), headers=headers(tokens, name)
                )
                assert result.json()["detail"] == unknown.json()["detail"]


def test_suggestion_identity_and_full_review_metadata_are_durable(tmp_path):
    path, tokens, _, body, backend, _ = setup(tmp_path, intent="operations")
    store = CaptureStore()
    with client(path, backend, store) as c:
        response = c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens))
        assert response.status_code == 200, response.text
        action = response.json()["recommended_actions"][0]
        item = store.suggestions[UUID(action["recommendation_id"])]
        assert item.recommendation_id == uuid5(item.trace_id, item.candidate_id)
        assert (
            item.requires_human_review
            and item.status == "proposed"
            and item.origin == "retailops-ai"
        )
        assert item.store_id == UUID(STORE) and item.expires_at > item.created_at
        assert item.evidence_refs == action["evidence_refs"]
        assert (
            c.post(
                "/api/v1/recommendations/" + str(item.recommendation_id) + "/execute",
                json={},
                headers=headers(tokens),
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "change",
    [
        {"question": " "},
        {"question": "x" * 2001},
        {"question": "nul\0"},
        {"intent": "sales"},
        {"principal_id": "admin"},
        {"conversation_id": STORE},
        {
            "scope": {
                "product_ids": [PRODUCT],
                "store_ids": [STORE],
                "from": "2026-01-01",
                "to": "2026-04-01",
            }
        },
        {
            "scope": {
                "product_ids": ["fake"],
                "store_ids": [STORE],
                "from": "2026-01-01",
                "to": "2026-01-01",
            }
        },
        {
            "scope": {
                "product_ids": [PRODUCT, PRODUCT],
                "store_ids": [STORE],
                "from": "2026-01-01",
                "to": "2026-01-01",
            }
        },
        {
            "scope": {
                "product_ids": [PRODUCT],
                "store_ids": [STORE],
                "from": "2026-01-02",
                "to": "2026-01-01",
            }
        },
    ],
)
def test_invalid_wire_requests_never_admit_or_invoke_model(tmp_path, change):
    path, tokens, _, body, backend, providers = setup(tmp_path)
    store = CaptureStore()
    with client(path, backend, store) as c:
        response = c.post("/api/v1/assistant/queries", json=body | change, headers=headers(tokens))
        assert response.status_code == 422
        assert store.admissions == 0 and not providers


@pytest.mark.parametrize("name,status", [(None, 401), ("viewer", 403), ("admin", 403)])
def test_auth_and_roles_checked_before_disabled_backend(tmp_path, name, status):
    path, tokens, _, body, _, _ = setup(tmp_path)
    with client(path) as c:
        response = c.post(
            "/api/v1/assistant/queries", json=body, headers=headers(tokens, name) if name else {}
        )
        assert response.status_code == status
        if status == 401:
            assert response.headers["www-authenticate"] == "Bearer"


def test_unauthorized_scope_and_tool_rights_never_admit(tmp_path):
    path, tokens, _, body, backend, providers = setup(
        tmp_path, capabilities=["assistant:query", "operations:read"]
    )
    store = CaptureStore()
    with client(path, backend, store) as c:
        assert (
            c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens)).status_code
            == 403
        )
        foreign = body | {"scope": body["scope"] | {"product_ids": [str(uuid4())]}}
        assert (
            c.post("/api/v1/assistant/queries", json=foreign, headers=headers(tokens)).status_code
            == 403
        )
        assert not store.admissions and not providers


@pytest.mark.parametrize(
    "code,status",
    [
        ("dependency_unavailable", 424),
        ("provider_unavailable", 503),
        ("invalid_output", 502),
        ("invalid_evidence", 502),
        ("budget_exceeded", 429),
        ("deadline_exceeded", 504),
        ("unauthorized", 403),
    ],
)
def test_safe_errors_persist_failed_run_without_answer_or_candidate(tmp_path, code, status):
    path, tokens, _, body, backend, _ = setup(tmp_path)
    original = backend.run

    async def fail(request, authorization):
        result = await original(request, authorization)
        trace = result.trace.model_copy(update={"status": "failed", "error_code": code})
        return GraphResult(status="failed", error_code=code, answer=None, trace=trace)

    backend.run = fail
    store = CaptureStore()
    with client(path, backend, store) as c:
        response = c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens))
        assert response.status_code == status
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
        if status == 424:
            assert response.json()["type"].endswith("dependency-unavailable")
        assert not store.answers and not store.suggestions
        assert next(iter(store.entries.values()))[1].error_code == code


def test_admission_rejection_starts_no_graph(tmp_path):
    path, tokens, _, body, backend, providers = setup(tmp_path)
    store = CaptureStore()
    store.fail_admission = AssistantError(429)
    with client(path, backend, store) as c:
        assert (
            c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens)).status_code
            == 429
        )
        assert not providers and not store.entries


def test_missing_real_source_returns_424_and_default_application_503(tmp_path):
    path, tokens, _, body, backend, _ = setup(tmp_path, adapters=False)
    with client(path, backend, CaptureStore()) as c:
        assert (
            c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens)).status_code
            == 424
        )
    with client(path) as c:
        assert (
            c.post("/api/v1/assistant/queries", json=body, headers=headers(tokens)).status_code
            == 503
        )
        assert c.get("/health").status_code == 200


def test_fixture_environment_gate_and_admin_grant_are_explicit(tmp_path):
    _, _, _, _, backend, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="fixture_assistant"):
        AssistantService(backend, CaptureStore(), "local")
    with pytest.raises(ValueError, match="durable_store"):
        create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts"), assistant_backend=backend)
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "policy_id": "invalid",
                    "grants": [
                        {
                            "principal_id": "op",
                            "roles": ["operator"],
                            "capabilities": ["assistant:audit"],
                            "scope": None,
                        }
                    ],
                    "credentials": [],
                }
            )
        )


def test_reduced_scope_revokes_trace_and_cancelled_request_finishes_safely(tmp_path):
    _, tokens, authority, body, backend, _ = setup(tmp_path)
    principal = authority.authenticate("Bearer " + tokens["owner"])
    store = CaptureStore()
    service = AssistantService(backend, store, "test")

    async def scenario():
        ready = asyncio.Event()

        async def wait(request, authorization):
            ready.set()
            await asyncio.Event().wait()

        backend.run = wait
        task = asyncio.create_task(
            service.query(
                AssistantQuery.model_validate_json(json.dumps(body)),
                principal,
                "Bearer " + tokens["owner"],
            )
        )
        await ready.wait()
        trace_id = next(iter(store.entries))
        assert (await store.get(trace_id, principal)).status == "running"
        assert await store.get(trace_id, replace(principal, product_ids=frozenset())) is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await store.get(trace_id, principal)).error_code == "cancelled"
        assert not store.answers

    asyncio.run(scenario())


def test_deadline_is_shared_with_server_resolver(tmp_path):
    _, tokens, authority, body, backend, providers = setup(tmp_path)
    backend.deadline_seconds = 0.02

    async def delayed(query, principal):
        await asyncio.sleep(0.1)

    backend.prepare = delayed
    store = CaptureStore()
    service = AssistantService(backend, store, "test")
    with pytest.raises(AssistantError) as exc:
        asyncio.run(
            service.query(
                AssistantQuery.model_validate_json(json.dumps(body)),
                authority.authenticate("Bearer " + tokens["owner"]),
                "Bearer " + tokens["owner"],
            )
        )
    assert exc.value.status == 504 and not store.entries and not providers


@pytest.mark.parametrize(
    "raw,status",
    [
        ('{"question":"first","question":"second"}', 422),
        ('{"question":NaN}', 422),
        ('{"question":"' + "x" * 17000 + '"}', 413),
    ],
)
def test_assistant_uses_global_strict_body_boundary(tmp_path, raw, status):
    path, tokens, _, _, backend, providers = setup(tmp_path)
    store = CaptureStore()
    with client(path, backend, store) as c:
        response = c.post(
            "/api/v1/assistant/queries",
            content=raw,
            headers=headers(tokens) | {"Content-Type": "application/json"},
        )
        assert response.status_code == status and not providers and not store.entries


def test_resolver_cannot_broaden_scope_and_required_capability_revokes_trace(tmp_path):
    _, tokens, authority, body, backend, _ = setup(tmp_path)
    original = backend.prepare

    async def widened(query, principal):
        prepared = await original(query, principal)
        scope = prepared.scope.model_copy(update={"product_ids": [PRODUCT, str(uuid4())]})
        return prepared.model_copy(update={"scope": scope})

    backend.prepare = widened
    store = CaptureStore()
    service = AssistantService(backend, store, "test")
    owner = authority.authenticate("Bearer " + tokens["owner"])
    query = AssistantQuery.model_validate_json(json.dumps(body))
    with pytest.raises(AssistantError) as exc:
        asyncio.run(service.query(query, owner, "Bearer " + tokens["owner"]))
    assert exc.value.status == 422 and not store.entries
    backend.prepare = original
    answer = asyncio.run(service.query(query, owner, "Bearer " + tokens["owner"]))
    assert (
        asyncio.run(
            store.get(answer.trace_id, replace(owner, capabilities=frozenset({"assistant:query"})))
        )
        is None
    )


def test_knowledge_scope_revocation_hides_safe_trace(tmp_path):
    _, tokens, authority, _, _, _ = setup(tmp_path)
    owner = authority.authenticate("Bearer " + tokens["owner"])
    required = KnowledgeResourceScope(
        environment="test",
        repositories=["Oskar-Stachowski/retailops-ai-intelligence"],
        access_classes=["public_project"],
        document_statuses=["verified"],
    )
    knowledge = KnowledgeAccess(
        "test",
        frozenset(required.repositories),
        frozenset(required.access_classes),
        frozenset(required.document_statuses),
    )
    owner = replace(
        owner, knowledge=knowledge, capabilities=owner.capabilities | {"knowledge:read"}
    )
    scope = json.dumps(
        {"product_ids": [PRODUCT], "selling_location_ids": [STORE], "channel": "store"}
    )
    assert readable(owner, owner.principal_id, scope, ["knowledge:read"], required)
    assert not readable(
        replace(owner, knowledge=None), owner.principal_id, scope, ["knowledge:read"], required
    )
    assert not readable(
        replace(owner, knowledge=replace(knowledge, document_statuses=frozenset({"specified"}))),
        owner.principal_id,
        scope,
        ["knowledge:read"],
        required,
    )
