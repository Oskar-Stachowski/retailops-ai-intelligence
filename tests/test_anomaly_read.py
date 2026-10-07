"""Read-only routes enforce capability, scope, query bounds and safe dependency errors."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_access import bearer, policy_file, problem

from retailops_ai.anomaly_portfolio.result_store import ReadError, authorized
from retailops_ai.anomaly_portfolio.serving_contract import Page, Pagination
from retailops_ai.api.app import create_app
from retailops_ai.config import Settings


class EmptyReader:
    def read(self, query, actor):
        authorized(actor, query)
        if query.anomaly_id:
            raise ReadError(404, "anomaly-not-found")
        return Page(
            items=(),
            pagination=Pagination(limit=query.limit, offset=query.offset, total=0),
            generated_at=datetime.now(UTC),
            view_sha256="0" * 64,
            selection="pinned_batch" if query.batch_id else "latest_complete_batch",
            data_status="no_data",
        )


def setup(tmp_path, backend=None, *, available=True):
    def mutate(value):
        value["grants"][0]["capabilities"].append("anomaly:read")

    path, tokens = policy_file(tmp_path, mutate)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
        anomaly_reader=(backend or EmptyReader()) if available else None,
    )
    return TestClient(app, base_url="http://127.0.0.1"), tokens


def test_auth_scope_and_invisible_id(tmp_path):
    client, tokens = setup(tmp_path)
    with client as c:
        problem(c.get("/api/v1/anomalies"), 401)
        problem(c.get("/api/v1/anomalies", headers=bearer(tokens["local-admin"])), 403)
        problem(
            c.get("/api/v1/anomalies?product_id=p-202", headers=bearer(tokens["local-viewer"])), 403
        )
        r = c.get("/api/v1/anomalies", headers=bearer(tokens["local-viewer"]))
        assert r.status_code == 200 and r.json()["data_status"] == "no_data"
        problem(
            c.get(
                "/api/v1/anomalies/anomaly-sha256-" + "1" * 64,
                headers=bearer(tokens["local-viewer"]),
            ),
            404,
        )


@pytest.mark.parametrize(
    "query",
    [
        "limit=201",
        "offset=1",
        "business_from=2026-01-01",
        "business_from=2026-01-01&business_to=2026-07-31",
        "business_from=20260101&business_to=2026-01-02",
        "limit=1&limit=2",
        "artifact_path=private-marker",
        "anomaly_id=forged",
    ],
)
def test_query_rejects_unbounded_unknown_and_ambiguous_filters(tmp_path, query):
    client, tokens = setup(tmp_path)
    with client as c:
        problem(c.get("/api/v1/anomalies?" + query, headers=bearer(tokens["local-viewer"])), 422)


def test_no_writes_and_dependency_fail_closed(tmp_path):
    client, tokens = setup(tmp_path, available=False)
    with client as c:
        problem(c.get("/api/v1/anomalies", headers=bearer(tokens["local-viewer"])), 503)
        problem(c.post("/api/v1/anomalies", headers=bearer(tokens["local-viewer"]), json={}), 405)
        problem(
            c.patch(
                "/api/v1/anomalies/anomaly-sha256-" + "1" * 64,
                headers=bearer(tokens["local-viewer"]),
                json={},
            ),
            405,
        )
