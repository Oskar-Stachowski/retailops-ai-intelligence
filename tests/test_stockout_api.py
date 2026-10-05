"""HTTP authorization, intake and freshness on explicit mechanics fixtures."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from test_access import PRIVATE, bearer, policy_file, problem
from test_stockout_batch import backend as backend
from test_stockout_batch import conditional as conditional
from test_stockout_batch import context as context
from test_stockout_batch import job as job
from test_stockout_batch import records as records
from test_stockout_batch import source as source

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.stockout_jobs.batch import compute
from retailops_ai.stockout_jobs.contracts import public_run
from retailops_ai.stockout_jobs.input_store import StockoutError, authorize_scope
from retailops_ai.stockout_jobs.read_contracts import StockoutAttempts, StockoutRiskPage
from retailops_ai.stockout_jobs.reader import fresh, read_scope
from retailops_ai.stockout_runtime.contracts import RiskItem

PATH = "/api/v1/stockout-runs"
RISKS = "/api/v1/stockout-risks"


class Backend:
    def __init__(self, fixture):
        self.run, self.inputs, self.release, self.now = fixture
        self.output = compute(self.run, self.inputs, self.release, generated_at=self.now)
        self.calls = []
        self.failure = None

    def get(self, run_id, principal):
        if self.failure:
            raise self.failure
        authorize_scope(self.inputs.scope, principal, reading=True, owned=True)
        if run_id != self.run.run_id:
            raise StockoutError(404, "stockout-run-not-found")
        return public_run(self.run)

    def submit(self, body, principal, key):
        authorize_scope(body.scope, principal)
        self.calls.append((body, principal, key))
        return self.get(self.run.run_id, principal)

    def attempts(self, run_id, principal):
        self.get(run_id, principal)
        return StockoutAttempts(run_id=run_id, items=(), generated_at=self.now)

    def list(self, query, principal):
        products, stocks = read_scope(query, principal)
        if self.failure:
            raise self.failure
        items = tuple(
            fresh(i, self.now)
            for i in self.output.items
            if i.product_id in products and i.stock_location_id in stocks
        )
        return StockoutRiskPage(
            items=items,
            pagination=Pagination(limit=50, offset=0, total=len(items), next_offset=None),
            generated_at=self.now,
            data_status="available" if items else "no_data",
            selection="latest_per_product_stock",
            view_sha256="0" * 64,
        )

    def risk(self, risk_id, principal):
        raise AssertionError("unused")


class Reader:
    def __init__(self, backend):
        self.backend = backend

    def list(self, query, principal):
        return self.backend.list(query, principal)

    def get(self, risk_id, principal):
        from retailops_ai.stockout_jobs.read_contracts import StockoutQuery

        page = self.list(StockoutQuery(), principal)
        found = next((i for i in page.items if i.risk_id == risk_id), None)
        if found is None:
            raise StockoutError(404, "stockout-risk-not-found")
        return found


def client(tmp_path, backend, *, pipeline=True, foreign=False, wired=True):
    def mutate(value):
        grant = next(g for g in value["grants"] if g["principal_id"] == "local-viewer")
        grant.update(
            roles=["pipeline"] if pipeline else ["viewer"],
            capabilities=["stockout:read", "stockout:run"] if pipeline else ["stockout:read"],
            scope=None,
            stockout_scope=dict(
                product_ids=["outside"] if foreign else list(backend.inputs.scope.product_ids),
                stock_location_ids=list(backend.inputs.scope.stock_location_ids),
            ),
        )

    path, tokens = policy_file(tmp_path, mutate)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
        stockout_administration=backend if wired else None,
        stockout_reader=Reader(backend) if wired else None,
    )
    return TestClient(app, base_url="http://127.0.0.1"), tokens["local-viewer"]


def test_intake_committed_backend_location_safe_pin_and_scoped_risk_read(tmp_path, job):
    backend = Backend(job)
    api, token = client(tmp_path, backend)
    with api:
        result = api.post(
            PATH,
            headers={**bearer(token), "Idempotency-Key": "one"},
            json=backend.run.input_ref.request.model_dump(mode="json"),
        )
        assert result.status_code == 202
        assert result.headers["location"] == PATH + "/" + backend.run.run_id
        assert len(backend.calls) == 1 and backend.calls[0][2] == "one"
        assert (
            result.json()["resolved_model"]["release"]["release_id"] == backend.release.release_id
        )
        assert result.json()["publication_status"] == "not_published"
        for private in ("source_uri", "qualification", "reports/", "mlflow-artifacts:"):
            assert private not in result.text
        assert api.get(result.headers["location"], headers=bearer(token)).json() == result.json()
        assert (
            api.get(result.headers["location"] + "/attempts", headers=bearer(token)).json()["items"]
            == []
        )
        page = api.get(RISKS, headers=bearer(token))
        assert page.status_code == 200 and len(page.json()["items"]) == 1
        item = page.json()["items"][0]
        assert item["quality_status"] == "mechanics_only" and item["freshness_status"] == "stale"
        assert api.get(RISKS + "/" + item["risk_id"], headers=bearer(token)).json() == item
        identity = api.get("/api/v1/identity", headers=bearer(token)).json()
        assert identity["stockout_scope"]["stock_location_ids"] == list(
            backend.inputs.scope.stock_location_ids
        )


@pytest.mark.parametrize(
    "failure",
    [
        StockoutError(409, "stockout-idempotency-conflict"),
        StockoutError(429, "stockout-queue-full"),
        ValueError(PRIVATE),
        OperationalError(PRIVATE, {}, Exception(PRIVATE)),
    ],
)
def test_failed_intake_never_returns_location_or_private_details(tmp_path, job, failure):
    backend = Backend(job)
    backend.failure = failure
    api, token = client(tmp_path, backend)
    with api:
        result = api.post(
            PATH,
            headers={**bearer(token), "Idempotency-Key": "one"},
            json=backend.run.input_ref.request.model_dump(mode="json"),
        )
        problem(result, failure.status if isinstance(failure, StockoutError) else 503)
        assert "location" not in result.headers


def test_no_policy_role_or_scope_cannot_reach_authorized_data(tmp_path, job):
    backend = Backend(job)
    api, token = client(tmp_path, backend, pipeline=False, foreign=True)
    with api:
        problem(
            api.post(
                PATH,
                headers={**bearer(token), "Idempotency-Key": "one"},
                json=backend.run.input_ref.request.model_dump(mode="json"),
            ),
            403,
        )
        assert backend.calls == []
        problem(api.get(RISKS), 401)
        exists = api.get(PATH + "/" + backend.run.run_id, headers=bearer(token))
        absent = api.get(PATH + "/run-" + "f" * 32, headers=bearer(token))
        problem(exists, 404)
        assert exists.json()["code"] == absent.json()["code"]
        problem(
            api.get(
                RISKS + "?product_id=" + backend.inputs.scope.product_ids[0], headers=bearer(token)
            ),
            403,
        )


@pytest.mark.parametrize(
    "suffix",
    [
        "?limit=1&limit=2",
        "?offset=1",
        "?limit=-1",
        "?limit=101",
        "?limit=١",
        "?features=x",
        "?selling_location_id=x",
    ],
)
def test_unbounded_or_forecast_query_rejected(tmp_path, job, suffix):
    api, token = client(tmp_path, Backend(job))
    with api:
        problem(api.get(RISKS + suffix, headers=bearer(token)), 422)


def test_duplicate_key_private_rows_and_missing_database_fail_closed(tmp_path, job):
    backend = Backend(job)
    api, token = client(tmp_path, backend)
    body = backend.run.input_ref.request.model_dump(mode="json")
    with api:
        headers = [*bearer(token).items(), ("Idempotency-Key", "one"), ("Idempotency-Key", "two")]
        problem(api.post(PATH, headers=headers, json=body), 422)
        problem(
            api.post(
                PATH,
                headers={**bearer(token), "Idempotency-Key": "one"},
                json={**body, "features": PRIVATE},
            ),
            422,
        )
        problem(
            api.post(
                PATH + "?requested_by=admin",
                headers={**bearer(token), "Idempotency-Key": "one"},
                json=body,
            ),
            422,
        )
        assert backend.calls == []
    api, token = client(tmp_path, backend, wired=False)
    with api:
        problem(api.get(PATH + "/" + backend.run.run_id, headers=bearer(token)), 503)
        problem(api.get(RISKS, headers=bearer(token)), 503)


def test_read_freshness_does_not_claim_current_when_watermark_is_missing(job):
    run, inputs, release, now = job
    item = compute(run, inputs, release, generated_at=now).items[0]
    raw = item.model_dump(mode="json")
    raw.update(as_of=(now - timedelta(minutes=5)).isoformat())
    recent = RiskItem.model_validate_json(canonical_bytes(raw))
    assert fresh(recent, now).freshness_status == "unknown"
    assert fresh(recent, now, pending=True).freshness_reason == "newer_run_unpublished"
    assert fresh(item, now).freshness_reason == "origin_age_exceeded"
    with pytest.raises(ValueError):
        fresh(item, now - timedelta(seconds=1))
