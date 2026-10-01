"""Explicit ASGI fixture with real PostgreSQL queue and private test-only namespace injection."""

from fastapi.testclient import TestClient
from test_access import policy_file

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.forecast_jobs.v12_administration import PostgresV12JobAdministration

PATH = "/api/v1/forecast-runs/v12"


def job_client(tmp_path, queue, profile, *, pipeline=True, foreign=False):
    tmp_path.mkdir(parents=True, exist_ok=True)

    def mutate(value):
        grant = next(g for g in value["grants"] if g["principal_id"] == "local-viewer")
        grant["principal_id"] = "unit-pipeline"
        next(c for c in value["credentials"] if c["principal_id"] == "local-viewer")[
            "principal_id"
        ] = "unit-pipeline"
        grant["scope"] = dict(
            product_ids=["outside"] if foreign else list(profile.scope.product_ids),
            selling_location_ids=list(profile.scope.selling_location_ids),
            channels=[profile.scope.channel],
        )
        grant.update(
            roles=["pipeline"] if pipeline else ["viewer"],
            capabilities=["forecast:run"] if pipeline else ["forecast:read"],
        )

    path, tokens = policy_file(tmp_path, mutate)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
        v12_forecast_administration=PostgresV12JobAdministration(queue),
    )
    return TestClient(app, base_url="http://127.0.0.1"), tokens["local-viewer"]
