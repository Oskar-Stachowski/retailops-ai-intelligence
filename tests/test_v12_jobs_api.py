"""Public v12 job boundaries on explicit small source/export/predictor fixtures."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from test_access import PRIVATE, bearer, policy_file, problem
from test_v12_lifecycle import artifacts as artifacts
from test_v12_lifecycle import inputs as inputs
from test_v12_lifecycle import loaded as loaded
from test_v12_lifecycle import prepared_input as prepared_input
from test_v12_lifecycle import qualification as qualification
from test_v12_lifecycle import serving as serving
from test_v12_lifecycle import tables as tables
from test_v12_lifecycle import timeline as timeline
from test_v12_publication import completed as completed
from test_v12_publication import rehashed, viewer

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.forecast_jobs.read_contracts import Pagination
from retailops_ai.forecast_jobs.v12_administration import job_projection
from retailops_ai.forecast_jobs.v12_job_contracts import V12JobAttempts, V12JobRun

PATH = "/api/v1/forecast-runs/v12"


def test_projection_distinguishes_computation_from_publication_and_omits_private_capsule(completed):
    output, run, receipt = completed
    principal = viewer(output)
    computed = job_projection(run, principal)
    assert computed.status == "succeeded" and computed.publication_status == "awaiting_publication"
    assert computed.computation_receipt_id == receipt.artifact_id and computed.output_ref is None
    published = job_projection(run, principal, output, receipt)
    assert published.publication_status == "published"
    assert published.output_ref.artifact_id == output.artifact_id
    assert published.resolved_model.model_version == run.resolved_model.model_version
    assert published.input_ref == run.input_ref and published.release_id == run.release_id
    serialized = published.model_dump_json()
    for private in ("source_uri", "mlflow-artifacts:", "recipe_path", "reports/", "qualification"):
        assert private not in serialized


def test_corrupt_or_incomplete_publication_cannot_acquire_public_output_reference(completed):
    output, run, receipt = completed
    principal = viewer(output)
    raw = output.model_dump(mode="json")
    raw["rows"][-1]["execution_profile_id"] = "batch-profile-sha256-" + "b" * 64
    with pytest.raises(ValueError):
        job_projection(run, principal, rehashed(raw), receipt)
    with pytest.raises(ValueError, match="incomplete_publication"):
        job_projection(run, principal, output)


def test_public_history_validates_state_order_and_count(completed):
    output, run, _ = completed
    item = job_projection(run, viewer(output))
    raw = dict(
        run_id=run.run_id,
        items=[item.model_dump(mode="json")],
        pagination=dict(limit=5, offset=0, total=1, next_offset=None),
        generated_at=datetime.now(UTC).isoformat(),
    )
    V12JobAttempts.model_validate_json(json.dumps(raw))
    raw["items"].append(raw["items"][0])
    raw["pagination"]["total"] = 2
    with pytest.raises(ValidationError):
        V12JobAttempts.model_validate_json(json.dumps(raw))
    document = item.model_dump(mode="json")
    document["publication_status"] = "published"
    with pytest.raises(ValidationError):
        V12JobRun.model_validate_json(json.dumps(document))


class Backend:
    """Route-only double; real SQL admission/idempotency has its own acceptance."""

    def __init__(self, completed):
        self.output, self.run, self.receipt = completed
        self.calls = []
        self.failure = None
        self.published = False

    def submit(self, request, principal, key):
        self.calls.append((request, principal, key))
        return self.get(self.run.run_id, principal)

    def get(self, run_id, principal):
        if self.failure:
            raise self.failure
        if run_id != self.run.run_id:
            raise BatchError(404, "forecast-run-not-found")
        return job_projection(
            self.run,
            principal,
            self.output if self.published else None,
            self.receipt if self.published else None,
        )

    def attempts(self, run_id, principal):
        run = self.get(run_id, principal)
        return V12JobAttempts(
            run_id=run_id,
            items=(run,),
            pagination=Pagination(limit=5, offset=0, total=1, next_offset=None),
            generated_at=datetime.now(UTC),
        )


def client(tmp_path, backend, *, pipeline=False, foreign=False):
    def mutate(value):
        grant = next(g for g in value["grants"] if g["principal_id"] == "local-viewer")
        grant["principal_id"] = "unit-pipeline"
        next(c for c in value["credentials"] if c["principal_id"] == "local-viewer")[
            "principal_id"
        ] = "unit-pipeline"
        grant["scope"] = dict(
            product_ids=["outside"] if foreign else list(backend.run.input_ref.scope.product_ids),
            selling_location_ids=list(backend.run.input_ref.scope.selling_location_ids),
            channels=[backend.run.input_ref.scope.channel],
        )
        if pipeline:
            grant.update(roles=["pipeline"], capabilities=["forecast:run"])

    path, tokens = policy_file(tmp_path, mutate)
    return TestClient(
        create_app(
            Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
            v12_forecast_administration=backend,
        ),
        base_url="http://127.0.0.1",
    ), tokens["local-viewer"]


def test_http_202_location_safe_status_and_history_then_publication(tmp_path, completed):
    backend = Backend(completed)
    api, token = client(tmp_path, backend, pipeline=True)
    headers = {**bearer(token), "Idempotency-Key": "v12-batch-1"}
    with api:
        result = api.post(
            PATH, headers=headers, json=backend.run.input_ref.request.model_dump(mode="json")
        )
        assert result.status_code == 202
        location = result.headers["location"]
        assert location == PATH + "/" + backend.run.run_id
        assert result.json()["publication_status"] == "awaiting_publication"
        assert "source_uri" not in result.text
        assert len(backend.calls) == 1
        assert backend.calls[0][1].principal_id == "unit-pipeline"
        assert backend.calls[0][2] == "v12-batch-1"
        assert api.get(location, headers=headers).json() == result.json()
        attempts = api.get(location + "/attempts", headers=headers)
        assert attempts.status_code == 200 and attempts.json()["pagination"]["total"] == 1
        backend.published = True
        published = api.get(location, headers=headers)
        assert published.json()["output_ref"]["artifact_id"] == backend.output.artifact_id
        assert published.json()["publication_status"] == "published"


def test_viewer_cannot_submit_and_foreign_scope_matches_absent_run(tmp_path, completed):
    backend = Backend(completed)
    api, token = client(tmp_path, backend, foreign=True)
    with api:
        headers = {**bearer(token), "Idempotency-Key": "v12-batch-1"}
        problem(
            api.post(
                PATH, headers=headers, json=backend.run.input_ref.request.model_dump(mode="json")
            ),
            403,
        )
        assert backend.calls == []
        foreign = api.get(PATH + "/" + backend.run.run_id, headers=headers)
        absent = api.get(PATH + "/run-" + "b" * 32, headers=headers)
        problem(foreign, 404)
        problem(absent, 404)
        assert foreign.json()["code"] == absent.json()["code"] == "forecast-run-not-found"
        problem(api.get(PATH + "/" + backend.run.run_id + "/attempts", headers=headers), 404)
        problem(api.get(PATH + "/" + backend.run.run_id), 401)


@pytest.mark.parametrize(
    "failure",
    [
        BatchError(409, "idempotency-conflict"),
        BatchError(429, "queue-full"),
        ValueError(PRIVATE),
        OperationalError(PRIVATE, {}, Exception(PRIVATE)),
    ],
)
def test_http_errors_are_safe_and_do_not_return_admission_location(tmp_path, completed, failure):
    backend = Backend(completed)
    backend.failure = failure
    api, token = client(tmp_path, backend, pipeline=True)
    with api:
        result = api.post(
            PATH,
            headers={**bearer(token), "Idempotency-Key": "v12-1"},
            json=backend.run.input_ref.request.model_dump(mode="json"),
        )
        problem(result, failure.status if isinstance(failure, BatchError) else 503)
        assert "location" not in result.headers


def test_duplicate_key_unknown_queries_and_client_model_paths_do_not_reach_backend(
    tmp_path, completed
):
    backend = Backend(completed)
    api, token = client(tmp_path, backend, pipeline=True)
    body = backend.run.input_ref.request.model_dump(mode="json")
    with api:
        headers = [*bearer(token).items(), ("Idempotency-Key", "one"), ("Idempotency-Key", "two")]
        problem(api.post(PATH, headers=headers, json=body), 422)
        problem(
            api.post(
                PATH,
                headers={**bearer(token), "Idempotency-Key": "one"},
                json={**body, "model_path": PRIVATE},
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
        problem(
            api.get(PATH + "/" + backend.run.run_id + "?role=admin", headers=bearer(token)), 422
        )
        assert backend.calls == []


def test_missing_database_and_private_test_namespace_are_not_silently_enabled(tmp_path, completed):
    backend = Backend(completed)
    _, token = client(tmp_path, backend, pipeline=True)
    principal = Principal(
        "other",
        frozenset({"pipeline"}),
        frozenset({"forecast:run"}),
        frozenset(backend.run.input_ref.scope.product_ids),
        frozenset(backend.run.input_ref.scope.selling_location_ids),
        frozenset({"store"}),
    )
    with pytest.raises(BatchError, match="forecast-run-not-found"):
        job_projection(backend.run, principal)
    # No DB/backend means unavailable, never a process-local queue.
    path = tmp_path / "api-access-policy.json"
    with TestClient(
        create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path)),
        base_url="http://127.0.0.1",
    ) as api:
        problem(api.get(PATH + "/" + backend.run.run_id, headers=bearer(token)), 503)
