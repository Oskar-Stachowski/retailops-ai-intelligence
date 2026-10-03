import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from test_access import bearer, policy_file, problem

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationEvidence,
    EvaluationQuery,
    EvaluationScope,
)
from retailops_ai.model_lifecycle.evaluation_fixture import fixture
from retailops_ai.model_lifecycle.evaluation_store import (
    EvaluationError,
    PostgresEvaluations,
    authorized,
    projection,
    summary,
)
from retailops_ai.model_lifecycle.read_contracts import CatalogScope

NOW = datetime(2026, 9, 1, tzinfo=UTC)


def actor(
    products=("p-101",),
    locations=("s-1",),
    channels=("store",),
    caps=("forecast:read",),
    identity="local-viewer",
):
    return Principal(
        identity,
        frozenset({"viewer"}),
        frozenset(caps),
        frozenset(products),
        frozenset(locations),
        frozenset(channels),
    )


@pytest.fixture
def evidence():
    return (
        fixture(("p-101",), NOW, salt="one", status="failed"),
        fixture(("p-101", "p-202"), NOW, salt="broad"),
        fixture(("p-202",), NOW, salt="outside", status="passed"),
    )


def test_whole_scope_and_status_before_counts_never_project_global_metrics(evidence):
    narrow = projection(evidence, EvaluationQuery(), actor(), NOW)
    assert (
        narrow.pagination.total == 1
        and narrow.items[0].evaluation_id == evidence[0].descriptor.evaluation_id
    )
    all_scope = actor(products=("p-101", "p-202"))
    assert projection(evidence, EvaluationQuery(), all_scope, NOW).pagination.total == 3
    assert (
        projection(evidence, EvaluationQuery(product_id="p-101"), all_scope, NOW).pagination.total
        == 1
    )
    assert (
        projection(evidence, EvaluationQuery(quality_status="passed"), actor(), NOW).data_status
        == "no_data"
    )
    assert (
        projection(evidence, EvaluationQuery(), actor(locations=("another",)), NOW).pagination.total
        == 0
    )
    assert (
        projection(evidence, EvaluationQuery(), actor(channels=("online",)), NOW).pagination.total
        == 0
    )
    assert "p-202" not in narrow.model_dump_json() and "metrics" not in narrow.model_dump_json()


def test_pagination_identity_clock_scope_status_and_changed_evidence(evidence):
    who = actor(products=("p-101", "p-202"))
    first = projection(evidence, EvaluationQuery(limit=1), who, NOW)
    second = projection(
        evidence, EvaluationQuery(limit=1, offset=1, view_sha256=first.view_sha256), who, NOW
    )
    assert first.items[0].evaluation_id != second.items[0].evaluation_id
    assert (
        projection(evidence, EvaluationQuery(), who, NOW + timedelta(days=30)).view_sha256
        == first.view_sha256
    )
    for query, principal, values, code in (
        (EvaluationQuery(offset=1), who, evidence, "evaluation-view-required"),
        (
            EvaluationQuery(view_sha256=first.view_sha256),
            actor(identity="other", products=("p-101", "p-202")),
            evidence,
            "evaluation-view-changed",
        ),
        (
            EvaluationQuery(view_sha256=first.view_sha256, quality_status="failed"),
            who,
            evidence,
            "evaluation-view-changed",
        ),
        (
            EvaluationQuery(view_sha256=first.view_sha256, product_id="p-101"),
            who,
            evidence,
            "evaluation-view-changed",
        ),
        (
            EvaluationQuery(view_sha256=first.view_sha256),
            who,
            evidence[:1],
            "evaluation-view-changed",
        ),
    ):
        with pytest.raises(EvaluationError, match=code):
            projection(values, query, principal, NOW)


def test_point_metric_validity_zero_denominator_and_no_serving_claim(evidence):
    detail = summary(evidence[0], NOW, detail=True)
    assert detail.serving_eligible is False and detail.registered_model_version is None
    assert detail.quality_status == "failed" and detail.freshness.status == "unknown"
    assert all(m.point.mae == 1.0 and m.point.wape == 2 / 3 for m in detail.metrics)
    raw = evidence[0].model_dump(mode="json")
    metric = raw["descriptor"]["metrics"][0]["point"]
    metric.update(absolute_actual_sum=0.0, wape=None, wape_status="zero_denominator")
    raw["evidence_sha256"] = canonical_sha256(raw["descriptor"])
    parsed = EvaluationEvidence.model_validate_json(json.dumps(raw))
    assert parsed.descriptor.metrics[0].point.wape is None
    for field, value in (("mae", 999.0), ("predicted_rows", 3), ("wape", float("nan"))):
        altered = evidence[0].model_dump(mode="json")
        altered["descriptor"]["metrics"][0]["point"][field] = value
        altered["evidence_sha256"] = (
            canonical_sha256(altered["descriptor"]) if field != "wape" else "a" * 64
        )
        with pytest.raises(ValidationError):
            EvaluationEvidence.model_validate_json(json.dumps(altered))
    changed = evidence[0].model_dump(mode="json")
    changed["descriptor"]["serving_eligible"] = 0
    with pytest.raises(ValidationError):
        EvaluationEvidence.model_validate_json(json.dumps(changed))


@pytest.mark.parametrize(
    "raw",
    [
        {"offset": 257},
        {"limit": True},
        {"quality_status": "done"},
        {"role": "admin"},
        {"view_sha256": "bad"},
    ],
)
def test_bounded_closed_query(raw):
    with pytest.raises(ValidationError):
        EvaluationQuery.model_validate_json(json.dumps(raw))


def test_scope_capability_bounds_and_fixture_boundary(evidence):
    with pytest.raises(EvaluationError, match="evaluation-read-denied"):
        authorized(CatalogScope(), actor(caps=("forecast:run",)))
    with pytest.raises(EvaluationError, match="evaluation-scope-invalid"):
        authorized(CatalogScope(product_id="outside"), actor())
    broad = actor(products=tuple(f"p-{i}" for i in range(21)))
    with pytest.raises(EvaluationError, match="evaluation-scope-limit"):
        authorized(CatalogScope(), broad)
    assert authorized(CatalogScope(product_id="p-0"), broad).products == ("p-0",)
    with pytest.raises(ValueError, match="synthetic_evaluation_requires_test_environment"):
        PostgresEvaluations(None, "local").register(evidence[0])
    with pytest.raises(ValidationError):
        EvaluationScope.model_validate_json(
            '{"product_ids":["p-2","p-1"],"selling_location_ids":["s"],"channels":["store"]}'
        )


class MemoryEvaluations(PostgresEvaluations):
    def __init__(self, evidence, error=None):
        self.values, self.error = evidence, error
        self.calls = 0

    def _read(self, scope, actor, *, identity=None, status=None):
        self.calls += 1
        resolved = authorized(scope, actor)
        if self.error:
            raise self.error
        from retailops_ai.model_lifecycle.evaluation_store import visible

        return tuple(
            v
            for v in self.values
            if visible(v, resolved)
            and (identity is None or v.descriptor.evaluation_id == identity)
            and (status is None or v.descriptor.quality_status == status)
        ), NOW


def api(tmp_path, evidence, error=None):
    def grant(value):
        value["grants"][0]["scope"]["selling_location_ids"] = ["s-1"]

    path, tokens = policy_file(tmp_path, grant)
    backend = MemoryEvaluations(evidence, error)
    c = TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
            evaluation_reader=backend,
        ),
        base_url="http://127.0.0.1",
    )
    return c, tokens, backend


def test_http_auth_scope_single_report_and_closed_filters(tmp_path, evidence):
    c, tokens, backend = api(tmp_path, evidence)
    headers = bearer(tokens["local-viewer"])
    uri = "/api/v1/evaluations"
    with c:
        problem(c.get(uri), 401)
        problem(c.get(uri, headers=bearer(tokens["local-admin"])), 403)
        assert backend.calls == 0
        assert c.get(uri, headers=headers).json()["pagination"]["total"] == 1
        assert c.get(uri + "/" + evidence[0].descriptor.evaluation_id, headers=headers).json()[
            "metrics"
        ]
        problem(c.get(uri + "/" + evidence[1].descriptor.evaluation_id, headers=headers), 404)
        problem(c.get(uri + "/forecast-quality-sha256-" + "f" * 64, headers=headers), 404)
        for query in (
            "user_id=admin",
            "quality_status=failed&quality_status=passed",
            "offset=257",
            "product_id=outside",
        ):
            problem(c.get(uri + "?" + query, headers=headers), 422)


@pytest.mark.parametrize(
    "error,status,code",
    [
        (ValueError("private-metadata"), 503, "evaluation-evidence-invalid"),
        (SQLAlchemyError("private-dsn"), 503, None),
        (EvaluationError(429, "evaluation-read-budget"), 429, "evaluation-read-budget"),
    ],
)
def test_safe_backend_errors(tmp_path, evidence, error, status, code):
    c, tokens, _ = api(tmp_path, evidence, error)
    with c:
        response = c.get("/api/v1/evaluations", headers=bearer(tokens["local-viewer"]))
        problem(response, status)
        assert response.json().get("code") == code and "private-" not in response.text


def test_private_cli_auth_failure_precedes_import_and_is_sanitized(tmp_path, monkeypatch, capsys):
    from retailops_ai.model_lifecycle import evaluation_cli

    monkeypatch.setattr(
        "sys.argv",
        [
            "evaluation-import",
            "--run-dir",
            str(tmp_path),
            "--policy-file",
            str(tmp_path / "policy"),
            "--credentials-file",
            str(tmp_path / "credentials"),
        ],
    )
    monkeypatch.setattr(
        evaluation_cli,
        "model_operator",
        lambda *_: (_ for _ in ()).throw(ValueError("private-token")),
    )
    monkeypatch.setattr(
        evaluation_cli, "import_evidence", lambda *_: pytest.fail("auth must precede import")
    )
    assert evaluation_cli.main() == 2
    captured = capsys.readouterr()
    assert not captured.out and captured.err == '{"error":"evaluation_import_failed"}\n'
