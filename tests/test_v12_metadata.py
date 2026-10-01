"""Original metric preservation, whole-scope isolation and safe v12 HTTP boundaries."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from test_access import bearer, policy_file, problem
from v12_evaluation_fixture import evaluation_fixture

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle import v12_evaluation_importer as importer
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError
from retailops_ai.model_lifecycle.read_contracts import CatalogQuery
from retailops_ai.model_lifecycle.reader import CatalogError
from retailops_ai.model_lifecycle.v12_catalog import PostgresV12Catalog, V12CatalogSnapshot, page
from retailops_ai.model_lifecycle.v12_evaluation_store import (
    PostgresV12Evaluations,
    projection,
    summary,
)
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import TEST_MODEL
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    V12EvaluationEvidence,
    V12EvaluationQuery,
)


def viewer(*, narrow=False, name="local-viewer"):
    return Principal(
        name,
        frozenset({"viewer"}),
        frozenset({"forecast:read"}),
        frozenset({"p-101"} if narrow else {"p-101", "p-202"}),
        frozenset({"s-03"}),
        frozenset({"store"}),
    )


@pytest.fixture
def report(tmp_path, monkeypatch):
    return evaluation_fixture(tmp_path / "export", monkeypatch)[1]


class Reports:
    def __init__(self, evidence):
        self.evidence = evidence

    def evaluations(self, query, actor):
        return projection((self.evidence,), query, actor, datetime.now(UTC))

    def evaluation(self, identity, scope, actor):
        if (
            identity != self.evidence.evaluation_id
            or not self.evaluations(
                V12EvaluationQuery.model_validate_json(scope.model_dump_json()), actor
            ).items
        ):
            raise EvaluationError(404, "evaluation-not-found")
        return summary(self.evidence, datetime.now(UTC), detail=True)


class Catalog:
    def __init__(self):
        self.snapshot = V12CatalogSnapshot(TEST_MODEL, (), None, datetime.now(UTC))

    def models(self, query, actor):
        return page(self.snapshot, query, actor, models=True)

    def model(self, name, scope, actor):
        page(
            self.snapshot,
            CatalogQuery.model_validate_json(scope.model_dump_json()),
            actor,
            models=True,
        )
        return self.snapshot.summary()

    def versions(self, name, query, actor):
        self.models(query, actor)
        raise CatalogError(404, "model-not-found")


def http(tmp_path, report, *, narrow=False, backend=None):
    def mutate(value):
        value["grants"][0]["scope"]["product_ids"] = list(viewer(narrow=narrow).product_ids)

    policy, tokens = policy_file(tmp_path, mutate)
    return TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=policy),
            v12_model_catalog=backend or Catalog(),
            v12_evaluation_reader=backend or Reports(report),
        ),
        base_url="http://127.0.0.1",
    ), tokens


def test_projection_includes_excluded_members_and_keeps_exact_original_metrics(report):
    assert report.descriptor.scope.product_ids == ("p-101", "p-202")
    assert report.descriptor.membership_rows == 2
    detail = summary(report, datetime.now(UTC), detail=True)
    assert [s.model_dump(mode="json") for s in detail.segments] == report.original_metrics[
        "segments"
    ]
    assert detail.segments[0].candidate.median.mae == 1.25
    assert detail.segments[0].candidate.mean.mae == 3.5
    assert detail.segments[0].candidate.mean.normalized_bias is None
    assert detail.segments[1].status == "not_ready"
    assert detail.segments[0].status == "failed"
    assert not detail.serving_eligible and detail.registered_model_version is None
    assert detail.freshness.status == "unknown"


def test_whole_scope_filter_precedes_count_and_pagination(report):
    for who, query in (
        (viewer(narrow=True), V12EvaluationQuery()),
        (viewer(), V12EvaluationQuery(product_id="p-101")),
        (viewer(), V12EvaluationQuery(quality_status="passed")),
    ):
        result = projection((report,), query, who, datetime.now(UTC))
        assert result.pagination.total == 0 and result.data_status == "no_data"
    assert (
        projection((report,), V12EvaluationQuery(), viewer(), datetime.now(UTC)).pagination.total
        == 1
    )


def test_evaluation_view_binds_identity_scope_and_evidence(report):
    now = datetime.now(UTC)
    first = projection((report,), V12EvaluationQuery(), viewer(), now)
    query = V12EvaluationQuery(offset=1, view_sha256=first.view_sha256)
    assert projection((report,), query, viewer(), now).items == ()
    for who, values in ((viewer(name="different-viewer"), (report,)), (viewer(), ())):
        with pytest.raises(EvaluationError, match="view-changed"):
            projection(values, query, who, now)
    with pytest.raises(EvaluationError, match="view-required"):
        projection((report,), V12EvaluationQuery(offset=1), viewer(), now)


@pytest.mark.parametrize("mutation", ["metric", "count", "scope", "readiness"])
def test_rehashed_projection_still_binds_original_report(report, mutation):
    raw = report.model_dump(mode="json")
    if mutation == "metric":
        raw["original_metrics"]["segments"][0]["candidate"]["mean"]["mse"] = 15.0
    elif mutation == "count":
        raw["descriptor"]["membership_rows"] = 3
    elif mutation == "scope":
        raw["descriptor"]["scope"]["product_ids"].append("p-101")
    else:
        raw["descriptor"]["forecast_model_status"] = "ready"
    raw["evidence_sha256"] = canonical_sha256(
        {k: v for k, v in raw.items() if k != "evidence_sha256"}
    )
    with pytest.raises(ValueError):
        V12EvaluationEvidence.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("budget", ["MAX_MEMBERSHIPS", "MAX_LINE_BYTES"])
def test_membership_stream_budget_rejects_without_registering(tmp_path, monkeypatch, budget):
    evidence, _ = evaluation_fixture(tmp_path / "export", monkeypatch)
    monkeypatch.setattr(importer, budget, 1)
    with pytest.raises(ValueError, match="membership_budget"):
        importer.project_evaluation(evidence, model=TEST_MODEL)


def test_passed_original_quality_still_does_not_authorize_serving(tmp_path, monkeypatch):
    _, report = evaluation_fixture(tmp_path / "passed", monkeypatch, passed=True)
    assert report.descriptor.quality_status == "passed"
    assert report.descriptor.forecast_model_status == "ready"
    assert not report.descriptor.serving_eligible


def test_new_http_routes_authenticate_and_hide_partial_report_scope(tmp_path, report):
    client, tokens = http(tmp_path, report, narrow=True)
    with client:
        for uri in (
            "/api/v1/models/v12",
            "/api/v1/models/v12/" + TEST_MODEL,
            "/api/v1/models/v12/" + TEST_MODEL + "/versions",
            "/api/v1/evaluations/v12",
            "/api/v1/evaluations/v12/" + report.evaluation_id,
        ):
            problem(client.get(uri), 401)
            problem(client.get(uri, headers=bearer(tokens["local-admin"])), 403)
        headers = bearer(tokens["local-viewer"])
        response = client.get("/api/v1/evaluations/v12", headers=headers)
        assert response.status_code == 200 and response.json()["pagination"]["total"] == 0
        problem(client.get("/api/v1/evaluations/v12/" + report.evaluation_id, headers=headers), 404)
        # Static /v12 is registered before legacy /{id}; empty is a valid v12 page.
        response = client.get("/api/v1/models/v12", headers=headers)
        assert (
            response.status_code == 200
            and response.json()["version"] == "forecast-v12-model-page-1.0.0"
        )


def test_http_full_detail_retains_nullable_metrics_and_safe_lineage(tmp_path, report):
    client, tokens = http(tmp_path, report)
    with client:
        headers = bearer(tokens["local-viewer"])
        result = client.get("/api/v1/evaluations/v12/" + report.evaluation_id, headers=headers)
        assert result.status_code == 200
        assert result.json()["segments"] == report.original_metrics["segments"]
        for private in ("exclusion_reasons", "artifact_uri", "release_dir", 'execution_failure"'):
            assert private not in result.text
        problem(client.post("/api/v1/evaluations/v12", headers=headers, json={}), 405)
        problem(
            client.get(
                "/api/v1/evaluations/v12", headers=headers, params={"product_id": "outside"}
            ),
            422,
        )


@pytest.mark.parametrize(
    "query", ["limit=1&limit=2", "unexpected=x", "quality_status=failed", "offset=257"]
)
def test_http_rejects_ambiguous_or_unsupported_query(tmp_path, report, query):
    client, tokens = http(tmp_path, report)
    with client:
        problem(
            client.get("/api/v1/evaluations/v12?" + query, headers=bearer(tokens["local-viewer"])),
            422,
        )


def test_http_storage_failures_do_not_expose_private_inputs(tmp_path, report):
    class Broken(Reports, Catalog):
        def models(self, *_):
            raise ValueError("private-storage-path-and-credentials")

        def evaluations(self, *_):
            raise ValueError("private-storage-path-and-credentials")

    client, tokens = http(tmp_path, report, backend=Broken(report))
    with client:
        for uri in ("/api/v1/evaluations/v12", "/api/v1/models/v12"):
            result = client.get(uri, headers=bearer(tokens["local-viewer"]))
            problem(result, 503)
            assert "private-storage" not in result.text


def test_test_environment_does_not_implicitly_open_mechanics_namespace(report):
    engine = create_engine("sqlite://")
    try:
        assert PostgresV12Catalog(engine, "test").name != TEST_MODEL
        assert PostgresV12Evaluations(engine, "test").model != TEST_MODEL
        with pytest.raises(ValueError, match="operator_required"):
            PostgresV12Evaluations(engine, "test", mechanics=True).register(report, viewer())
        for constructor in (PostgresV12Catalog, PostgresV12Evaluations):
            with pytest.raises(ValueError, match="environment"):
                constructor(engine, "local", mechanics=True)
    finally:
        engine.dispose()
