import asyncio
import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client.parser import text_string_to_metric_families
from pydantic import ValidationError

from retailops_ai.adapters.telemetry import CORRELATION_ID, JsonFormatter, context_headers
from retailops_ai.api.app import create_app
from retailops_ai.api.models import Health, Problem, Ready, ServiceVersion
from retailops_ai.api.schema import contract_openapi
from retailops_ai.config import Settings
from retailops_ai.domain.readiness import Dependency

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-metrics-token-" + "x" * 32
SECRET = "private-request-value-must-never-appear"  # noqa: S105 - synthetic redaction sentinel
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1"


def settings(**overrides):
    return Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", **overrides)


def client(app):
    return TestClient(app, base_url=BASE)


def assert_problem(response, status):
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    problem = Problem.model_validate(response.json())
    assert str(problem.correlation_id) == response.headers["x-correlation-id"]
    assert problem.instance == f"urn:uuid:{problem.correlation_id}"
    assert problem.status == status
    assert SECRET not in response.text
    return problem


def captured_json(caplog):
    formatter = JsonFormatter()
    return [
        json.loads(formatter.format(record))
        for record in caplog.records
        if record.name == "retailops_ai.http"
        and getattr(record, "event_data", {}).get("event") == "http_request"
    ]


def test_diagnostics_and_startup_lifecycle_are_honest():
    app = create_app(settings())
    with client(app) as c:
        assert Health.model_validate(c.get("/health").json()).status == "ok"
        ready = Ready.model_validate(c.get("/ready").json())
        assert ready.status == "ready"
        assert ready.role == "foundation"
        assert [(d.name, d.status) for d in ready.dependencies] == [("startup", "up")]
        info = ServiceVersion.model_validate(c.get("/version").json())
        assert info.version == "0.1.0.dev0"
        assert info.model is info.image_digest is info.build_commit is None
    # A request outside an active ASGI lifespan must not claim readiness.
    c = client(app)
    assert c.get("/health").status_code == 200
    assert assert_problem(c.get("/ready"), 503).readiness.status == "not_ready"


def test_required_failure_optional_failure_recovery_and_health_independence():
    db_up = False
    calls = []

    async def database():
        calls.append("database")
        if not db_up:
            raise RuntimeError(SECRET)
        return True

    async def optional_llm():
        calls.append("bedrock")
        return False

    app = create_app(
        settings(),
        dependencies=(
            Dependency("database", database),
            Dependency("bedrock", optional_llm, required=False),
        ),
    )
    with client(app) as c:
        assert c.get("/health").status_code == 200
        assert not calls
        failure = assert_problem(c.get("/ready"), 503).readiness
        assert [(d.name, d.status) for d in failure.dependencies] == [
            ("startup", "up"),
            ("database", "down"),
            ("bedrock", "down"),
        ]
        db_up = True
        response = c.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert c.get("/version").status_code == 200


@pytest.mark.parametrize("required,expected_status", [(True, 503), (False, 200)])
def test_slow_provider_times_out_and_is_cancelled(required, expected_status):
    cancelled = []

    async def slow():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return True

    app = create_app(
        settings(READINESS_TIMEOUT_SECONDS=0.02),
        dependencies=(Dependency("slow", slow, required=required),),
    )
    with client(app) as c:
        response = c.get("/ready")
        assert response.status_code == expected_status
        body = response.json()
        report = body.get("readiness", body)
        assert report["dependencies"][1]["status"] == "timeout"
    assert cancelled == [True]


def test_correlations_traces_logs_and_outbound_provider_context(caplog):
    caplog.set_level(logging.INFO, logger="retailops_ai.http")
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name": "test"}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    outgoing = []
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
    correlation = str(uuid4())

    async def fake_provider():
        def respond(request):
            outgoing.append(dict(request.headers))
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as remote:
            result = await remote.get("https://provider.invalid/check", headers=context_headers())
        return result.status_code == 200

    app = create_app(
        settings(),
        dependencies=(Dependency("provider", fake_provider),),
        tracer=provider.get_tracer("test"),
    )
    with client(app) as c:
        response = c.get(
            "/ready",
            headers={
                "X-Correlation-ID": correlation,
                "traceparent": parent,
                "tracestate": "vendor=private",
                "baggage": SECRET,
                "Authorization": SECRET,
            },
        )
    assert response.status_code == 200
    propagated = response.headers["traceparent"]
    assert propagated.split("-")[1] == parent.split("-")[1]
    assert propagated.split("-")[2] != parent.split("-")[2]
    assert outgoing[0]["traceparent"] == propagated
    assert outgoing[0]["x-correlation-id"] == correlation
    assert "baggage" not in outgoing[0] and "tracestate" not in outgoing[0]
    (span,) = exporter.get_finished_spans()
    assert span.parent.span_id == int(parent.split("-")[2], 16)
    assert span.name == "GET /ready"
    events = captured_json(caplog)
    assert events[-1]["correlation_id"] == correlation
    assert events[-1]["trace_id"] == parent.split("-")[1]
    assert SECRET not in json.dumps(events)
    assert CORRELATION_ID.get() is None
    assert "X-Correlation-ID" not in context_headers()
    provider.shutdown()


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Correlation-ID": SECRET, "traceparent": SECRET},
        {"X-Correlation-ID": "x" * 2000, "traceparent": "y" * 2000},
        [
            ("X-Correlation-ID", str(uuid4())),
            ("X-Correlation-ID", str(uuid4())),
            ("traceparent", "bad"),
            ("traceparent", "bad"),
        ],
        {"traceparent": "00-" + "0" * 32 + "-" + "0" * 16 + "-01"},
    ],
)
def test_untrusted_or_duplicate_context_is_replaced(headers, caplog):
    caplog.set_level(logging.INFO, logger="retailops_ai.http")
    with client(create_app(settings())) as c:
        response = c.get("/health", headers=headers)
    assert response.status_code == 200
    assert UUID(response.headers["x-correlation-id"]).version == 4
    trace = response.headers["traceparent"].split("-")
    assert len(trace[1]) == 32 and int(trace[1], 16) != 0
    assert SECRET not in json.dumps(captured_json(caplog))
    assert SECRET not in str(response.headers)


def test_contexts_are_isolated_under_concurrent_requests():
    async def run():
        seen = []

        async def probe():
            before = context_headers()
            await asyncio.sleep(0.001)
            seen.append((before, context_headers()))
            return True

        app = create_app(settings(), dependencies=(Dependency("probe", probe),))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE) as c:
                ids = [str(uuid4()) for _ in range(12)]
                responses = await asyncio.gather(
                    *[c.get("/ready", headers={"X-Correlation-ID": value}) for value in ids]
                )
                assert [r.headers["x-correlation-id"] for r in responses] == ids
                assert all(r.status_code == 200 for r in responses)
                traces = {r.headers["traceparent"].split("-")[1] for r in responses}
                assert len(traces) == 12
                assert all(before == after for before, after in seen)
                assert {before["X-Correlation-ID"] for before, _ in seen} == set(ids)
        assert CORRELATION_ID.get() is None

    asyncio.run(run())


def test_metrics_requires_configured_credentials_and_has_bounded_labels(caplog):
    caplog.set_level(logging.INFO, logger="retailops_ai.http")
    with client(create_app(settings())) as c:
        assert_problem(c.get("/metrics", headers=AUTH), 404)
    with client(create_app(settings(METRICS_TOKEN=TOKEN))) as c:
        for headers in (
            {},
            {"Authorization": "Bearer " + SECRET},
            {"Authorization": "Basic " + TOKEN},
            [("Authorization", AUTH["Authorization"]), ("Authorization", AUTH["Authorization"])],
        ):
            assert_problem(c.get("/metrics", headers=headers), 401)
        for i in range(20):
            assert_problem(c.get(f"/{SECRET}-{i}?token={SECRET}"), 404)
        c.get("/health")
        response = c.get("/metrics", headers=AUTH)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        samples = [
            sample
            for family in text_string_to_metric_families(response.text)
            for sample in family.samples
        ]
        unmatched = [
            s
            for s in samples
            if s.name == "retailops_ai_http_requests_total" and s.labels["route"] == "unmatched"
        ]
        assert len(unmatched) == 1 and unmatched[0].value == 20
        assert any(s.name.endswith("_duration_seconds_count") for s in samples)
        assert SECRET not in response.text and TOKEN not in response.text
        assert "correlation_id" not in response.text and "trace_id" not in response.text
    events = json.dumps(captured_json(caplog))
    assert SECRET not in events and TOKEN not in events


def test_registries_are_per_application():
    with client(create_app(settings(METRICS_TOKEN=TOKEN))) as first:
        first.get("/health")
        assert 'route="/health"' in first.get("/metrics", headers=AUTH).text
    with client(create_app(settings(METRICS_TOKEN=TOKEN))) as second:
        assert 'route="/health"' not in second.get("/metrics", headers=AUTH).text


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("GET", "/private-request-value-must-never-appear", 404),
        ("POST", "/health", 405),
        ("GET", "/docs", 404),
        ("GET", "/openapi.json", 404),
        ("GET", "/health/", 404),
    ],
)
def test_error_contract_and_no_unintended_routes(method, path, status):
    with client(create_app(settings())) as c:
        response = c.request(method, path + "?secret=" + SECRET, headers={"Authorization": SECRET})
    assert_problem(response, status)
    assert response.headers["cache-control"] == "no-store"
    if status == 405:
        assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    "host", ["attacker.invalid", "localhost.attacker.invalid", "127.0.0.1.attacker.invalid", SECRET]
)
def test_host_boundary_rejects_dns_rebinding(host):
    with client(create_app(settings())) as c:
        assert_problem(c.get("/health", headers={"Host": host}), 400)


def test_validation_and_unhandled_error_are_sanitized(caplog):
    caplog.set_level(logging.INFO, logger="retailops_ai.http")
    app = create_app(settings())

    @app.get("/validate")
    async def validate(count: int):
        return {"count": count}

    @app.get("/fail")
    async def fail():
        raise RuntimeError(SECRET)

    @app.get("/forbidden")
    async def forbidden():
        raise HTTPException(403, detail=SECRET, headers={"X-Private": SECRET})

    with client(app) as c:
        assert_problem(c.get("/validate", params={"count": SECRET}), 422)
        failure = c.get("/fail")
        assert_problem(failure, 500)
        assert "traceparent" in failure.headers
        forbidden = c.get("/forbidden")
        assert_problem(forbidden, 403)
        assert "x-private" not in forbidden.headers
        assert c.get("/health").status_code == 200
    assert SECRET not in json.dumps(captured_json(caplog))
    assert captured_json(caplog)[-2]["status"] == 403


@pytest.mark.parametrize(
    "field,value",
    [
        ("HTTP_HOST", "0.0.0.0"),  # noqa: S104 - verify that public binding is rejected
        ("HTTP_HOST", "example.invalid"),
        ("HTTP_PORT", 0),
        ("HTTP_PORT", 65536),
        ("METRICS_TOKEN", "short"),
        ("METRICS_TOKEN", " " * 32),
        ("READINESS_TIMEOUT_SECONDS", 0),
        ("READINESS_TIMEOUT_SECONDS", "NaN"),
        ("BUILD_COMMIT", SECRET),
        ("IMAGE_DIGEST", SECRET),
    ],
)
def test_invalid_http_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_version_exposes_only_bounded_build_metadata():
    with client(
        create_app(
            settings(BUILD_COMMIT="a" * 40, IMAGE_DIGEST="sha256:" + "b" * 64, METRICS_TOKEN=TOKEN)
        )
    ) as c:
        response = c.get("/version")
        result = ServiceVersion.model_validate(response.json())
        assert result.build_commit == "a" * 40
        assert result.image_digest == "sha256:" + "b" * 64
        assert TOKEN not in response.text
        assert "artifact_root" not in response.text


def test_raw_third_party_messages_and_exception_text_are_not_logged():
    record = logging.LogRecord("uvicorn.error", logging.ERROR, "hidden", 1, SECRET, (), None)
    record.exc_text = SECRET
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "server_event"
    assert SECRET not in json.dumps(payload)


def test_http_contract_snapshots():
    app = create_app(settings())
    assert contract_openapi(app.openapi(), access=False) == json.loads(
        (ROOT / "contracts/diagnostics.openapi.json").read_text()
    )
    for name, model in [
        ("health", Health),
        ("readiness", Ready),
        ("problem", Problem),
        ("service-version", ServiceVersion),
    ]:
        example = json.loads((ROOT / f"contracts/{name}.v1.example.json").read_text())
        model.model_validate(example)
        assert model.model_json_schema() == json.loads(
            (ROOT / f"contracts/{name}.v1.schema.json").read_text()
        )


def test_disabled_trace_sdk_does_not_break_health_or_correlation(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with client(create_app(settings())) as c:
        response = c.get("/health")
    assert response.status_code == 200
    assert UUID(response.headers["x-correlation-id"]).version == 4
    assert "traceparent" not in response.headers
