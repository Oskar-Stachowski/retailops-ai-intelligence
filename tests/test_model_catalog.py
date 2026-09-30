import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from test_access import bearer, policy_file, problem
from test_forecast_publication import claim, inputs  # noqa: F401 - shared fixtures
from test_forecast_read import actor, completed  # noqa: F401 - shared fixture

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.model_lifecycle.contracts import MODEL
from retailops_ai.model_lifecycle.read_contracts import (
    ApprovedModelRelease,
    CatalogFreshness,
    CatalogQuery,
    CatalogScope,
    CatalogVersion,
)
from retailops_ai.model_lifecycle.reader import (
    CatalogError,
    CatalogSnapshot,
    PostgresModelCatalog,
    authorized,
    page_snapshot,
)


@pytest.fixture
def snapshot(request):
    publication_pair = request.getfixturevalue("completed")
    out, _ = publication_pair
    binding = out.manifest.resolved_model
    q = binding.qualification
    now = out.manifest.generated_at
    versions = tuple(
        CatalogVersion(
            model_version=v,
            status="previously_published",
            model_family=q.model_family,
            flavor=q.flavor,
            mlflow_run_id=binding.mlflow_run_id,
            model_artifact=q.model,
            model_card_report=q.gates["model_card"].report,
            qualification_sha256=binding.qualification_sha256,
            config_sha256=q.config_sha256,
            evaluation_id=q.evaluation_id,
            visible_last_published_at=now,
            freshness=CatalogFreshness(evaluated_at=now),
        )
        for v in ("2", "10")
    )
    return CatalogSnapshot(versions, None, now)


def test_stable_pages_principal_scope_clock_and_changed_metadata(snapshot, request):
    publication_pair = request.getfixturevalue("completed")
    who = actor(publication_pair[0])
    first = page_snapshot(snapshot, CatalogQuery(limit=1), who, models=False)
    second = page_snapshot(
        snapshot, CatalogQuery(limit=1, offset=1, view_sha256=first.view_sha256), who, models=False
    )
    assert first.items[0].model_version == "2" and second.items[0].model_version == "10"
    assert first.pagination.total == 2 and second.pagination.next_offset is None
    later = CatalogSnapshot(snapshot.versions, None, snapshot.generated_at + timedelta(hours=1))
    assert page_snapshot(later, CatalogQuery(), who, models=False).view_sha256 == first.view_sha256
    for query, changed_actor, changed_snapshot, code in (
        (CatalogQuery(offset=1), who, snapshot, "model-view-required"),
        (
            CatalogQuery(view_sha256=first.view_sha256, product_id="fixture-product-00"),
            who,
            snapshot,
            "model-view-changed",
        ),
        (
            CatalogQuery(view_sha256=first.view_sha256),
            who.__class__(
                "another",
                who.roles,
                who.capabilities,
                who.product_ids,
                who.selling_location_ids,
                who.channels,
            ),
            snapshot,
            "model-view-changed",
        ),
        (
            CatalogQuery(view_sha256=first.view_sha256),
            who,
            CatalogSnapshot(snapshot.versions[:1], None, snapshot.generated_at),
            "model-view-changed",
        ),
    ):
        with pytest.raises(CatalogError, match=code):
            page_snapshot(changed_snapshot, query, changed_actor, models=False)
    with pytest.raises(CatalogError, match="model-view-changed"):
        page_snapshot(snapshot, CatalogQuery(view_sha256=first.view_sha256), who, models=True)


def test_model_summary_unknown_runtime_and_no_private_metadata(snapshot, request):
    publication_pair = request.getfixturevalue("completed")
    who = actor(publication_pair[0])
    summary = snapshot.summary()
    assert summary.visible_version_count == 2 and summary.approved_release is None
    assert summary.registry_aliases is None and summary.deployed_model_version is None
    assert summary.deployment_status == "not_attested" and summary.drift_status == "not_run"
    assert summary.freshness.status == "unknown"
    raw = page_snapshot(snapshot, CatalogQuery(), who, models=False).model_dump_json()
    for private in ("source_uri", "gates", "metric", "fixture-product-19", "mlflow-artifacts:"):
        assert private not in raw
    empty = CatalogSnapshot((), None, snapshot.generated_at)
    assert page_snapshot(empty, CatalogQuery(), who, models=True).data_status == "no_data"
    with pytest.raises(CatalogError, match="model-not-found"):
        empty.summary()
    first = page_snapshot(snapshot, CatalogQuery(), who, models=True)
    approved = ApprovedModelRelease(
        release_id="model-release-sha256-" + "a" * 64,
        model_version="10",
        image_digest="sha256:" + "b" * 64,
    )
    changed = CatalogSnapshot(snapshot.versions, approved, snapshot.generated_at)
    with pytest.raises(CatalogError, match="model-view-changed"):
        page_snapshot(changed, CatalogQuery(view_sha256=first.view_sha256), who, models=True)


@pytest.mark.parametrize(
    "raw",
    [
        {"limit": 201},
        {"limit": True},
        {"offset": 1001},
        {"role": "admin"},
        {"channel": "all"},
        {"view_sha256": "bad"},
    ],
)
def test_closed_query(raw):
    with pytest.raises(ValidationError):
        CatalogQuery.model_validate_json(json.dumps(raw))


def test_capability_scope_and_budget(snapshot, request):
    publication_pair = request.getfixturevalue("completed")
    out, _ = publication_pair
    with pytest.raises(CatalogError, match="model-read-denied"):
        authorized(CatalogScope(), actor(out, caps=("forecast:run",)))
    with pytest.raises(CatalogError, match="model-scope-invalid"):
        authorized(CatalogScope(product_id="outside"), actor(out))
    broad = actor(out, products=tuple(f"p-{i}" for i in range(21)))
    with pytest.raises(CatalogError, match="model-scope-limit"):
        authorized(CatalogScope(), broad)
    assert authorized(CatalogScope(product_id="p-0"), broad).products == ("p-0",)


class MemoryCatalog(PostgresModelCatalog):
    def __init__(self, snapshot, error=None):
        self.value, self.error = snapshot, error
        self.calls = 0

    def _snapshot(self, request, actor):
        self.calls += 1
        authorized(request, actor)
        if self.error:
            raise self.error
        return self.value


def api(tmp_path, snapshot, error=None):
    path, tokens = policy_file(tmp_path)
    backend = MemoryCatalog(snapshot, error)
    settings = Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path)
    return (
        TestClient(create_app(settings, model_catalog=backend), base_url="http://127.0.0.1"),
        tokens,
        backend,
    )


@pytest.mark.parametrize("suffix", ["", "/" + MODEL, "/" + MODEL + "/versions"])
def test_http_authentication_and_explicit_read_capability(tmp_path, snapshot, suffix):
    c, tokens, backend = api(tmp_path, snapshot)
    with c:
        problem(c.get("/api/v1/models" + suffix), 401)
        problem(c.get("/api/v1/models" + suffix, headers=bearer(tokens["local-admin"])), 403)
        assert backend.calls == 0
        assert (
            c.get("/api/v1/models" + suffix, headers=bearer(tokens["local-viewer"])).status_code
            == 200
        )


@pytest.mark.parametrize(
    "query",
    [
        "role=admin",
        "channel=store&channel=online",
        "limit=201",
        "offset=1001",
        "product_id=outside",
    ],
)
def test_http_closed_queries_and_outside_scope(tmp_path, snapshot, query):
    c, tokens, _ = api(tmp_path, snapshot)
    with c:
        problem(c.get("/api/v1/models?" + query, headers=bearer(tokens["local-viewer"])), 422)


@pytest.mark.parametrize(
    "error,status,code",
    [
        (ValueError("private-metadata"), 503, "model-metadata-invalid"),
        (SQLAlchemyError("private-dsn"), 503, None),
        (CatalogError(429, "model-read-budget"), 429, "model-read-budget"),
    ],
)
def test_http_failures_are_safe(tmp_path, snapshot, error, status, code):
    c, tokens, _ = api(tmp_path, snapshot, error)
    with c:
        response = c.get("/api/v1/models", headers=bearer(tokens["local-viewer"]))
        problem(response, status)
        assert "private-" not in response.text
        assert response.json().get("code") == code


def test_http_missing_backend_unknown_name_and_no_visible_models(tmp_path, snapshot):
    c, tokens, backend = api(tmp_path, snapshot)
    headers = bearer(tokens["local-viewer"])
    with c:
        problem(c.get("/api/v1/models/unknown", headers=headers), 404)
        assert backend.calls == 0
        backend.value = CatalogSnapshot((), None, snapshot.generated_at)
        assert c.get("/api/v1/models", headers=headers).json()["pagination"]["total"] == 0
        problem(c.get("/api/v1/models/" + MODEL + "/versions", headers=headers), 404)
        problem(c.get("/api/v1/models/" + MODEL, headers=headers), 404)
    path, tokens = policy_file(tmp_path)
    with TestClient(
        create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path)),
        base_url="http://127.0.0.1",
    ) as c:
        problem(c.get("/api/v1/models", headers=bearer(tokens["local-viewer"])), 503)
