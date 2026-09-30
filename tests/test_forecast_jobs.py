import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_access import bearer, policy_file, problem
from test_model_lifecycle import actor as promoter
from test_model_lifecycle import request as lifecycle_request
from test_model_lifecycle import setup

from retailops_ai.api.app import create_app
from retailops_ai.config import Settings
from retailops_ai.data_contracts.run import MLRunRecord, transition_run
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs import worker
from retailops_ai.forecast_jobs.contracts import BatchInput, BatchRequest, BatchRun, QueuePolicy
from retailops_ai.forecast_jobs.mechanics import fixture
from retailops_ai.forecast_jobs.queue import BatchError, Claim, LeaseLost, authorize_read, scope_for
from retailops_ai.model_lifecycle.contracts import TEST_MODEL
from retailops_ai.security.models import GrantTemplate


def principal(name="local-viewer", role="pipeline", caps=("forecast:run",), products=("p-101",)):
    return Principal(
        name,
        frozenset({role}),
        frozenset(caps),
        frozenset(products),
        frozenset({"s-03"}),
        frozenset({"store"}),
    )


@pytest.fixture
def run():
    profile, request = fixture()
    lifecycle, backend, journal = setup()
    lifecycle.execute(lifecycle_request(backend, "register", 1), promoter())
    lifecycle.execute(lifecycle_request(backend, "promote", 1, "1"), promoter())
    release = journal.active(TEST_MODEL)
    return BatchRun(
        schema_version="1.0",
        contract_type="run",
        run_id="run-" + "a" * 32,
        status="queued",
        attempt=1,
        requested_at=datetime.now(UTC),
        requested_by="local-viewer",
        started_at=None,
        completed_at=None,
        output_ref=None,
        error=None,
        input_ref=BatchInput(
            source_dataset_id=profile.source_dataset_id,
            curated_dataset_id=profile.curated_dataset_id,
            feature_set_id=profile.feature_set_id,
            as_of_time=profile.as_of_time,
            profile_id=profile.profile_id,
            request_hash=request.request_hash(),
            request=request,
            scope=scope_for(request, principal()),
        ),
        resolved_model=release.binding,
        release_id=release.release_id,
        image_digest=release.image_digest,
        environment="test",
        purpose="lifecycle_mechanics_only",
        policy=QueuePolicy(),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_by", "other"),
        ("roles", ["pipeline"]),
        ("model_version", "1"),
        ("model_alias", "candidate"),
        ("profile_id", "/untrusted/private-model"),
        ("horizons_days", []),
        ("horizons_days", [7, 7]),
        ("product_ids", ["p-101", "p-101"]),
        ("as_of", "2026-01-01T12:00:00Z"),
        ("channel", "wholesale"),
    ],
)
def test_request_rejects_untrusted_identity_paths_and_unbounded_shapes(field, value):
    _, request = fixture()
    raw = request.model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        BatchRequest.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "change",
    [
        {"max_attempts": True},
        {"max_attempts": 6},
        {"heartbeat_seconds": 15.0},
        {"lease_seconds": 2},
        {"run_timeout_seconds": 60},
        {"max_pending": 1},
    ],
)
def test_policy_has_consistent_strict_budgets(change):
    with pytest.raises(ValidationError):
        QueuePolicy.model_validate_json(json.dumps(change))


def test_request_hash_is_order_independent_and_scope_is_whole_authorized_set():
    _, request = fixture()
    first = request.model_copy(update={"product_ids": ("p-101", "p-202"), "horizons_days": (7, 14)})
    second = request.model_copy(
        update={"product_ids": ("p-202", "p-101"), "horizons_days": (14, 7)}
    )
    assert first.request_hash() == second.request_hash()
    assert scope_for(request, principal(products=("p-101", "p-202"))).product_ids == (
        "p-101",
        "p-202",
    )
    with pytest.raises(BatchError, match="scope-denied"):
        scope_for(first, principal())


def test_model_pins_and_shared_run_state_machine_remain_closed(run):
    raw = run.model_dump(mode="json")
    with pytest.raises(ValidationError):
        MLRunRecord.model_validate_json(json.dumps(raw))
    raw["environment"] = "local"
    with pytest.raises(ValidationError, match="namespace"):
        BatchRun.model_validate_json(json.dumps(raw))
    raw = run.model_dump(mode="json")
    raw.update(status="running", started_at=datetime.now(UTC).isoformat())
    running = BatchRun.model_validate_json(json.dumps(raw))
    transition_run(run, running)
    raw.update(status="succeeded", completed_at=datetime.now(UTC).isoformat())
    with pytest.raises(ValidationError, match="complete_output"):
        BatchRun.model_validate_json(json.dumps(raw))
    with pytest.raises(ValueError):
        transition_run(
            run,
            running.model_copy(
                update={
                    "resolved_model": run.resolved_model.model_copy(update={"model_version": "2"})
                }
            ),
        )


def test_scoped_read_requires_read_capability_or_run_owner(run):
    authorize_read(run, principal())
    authorize_read(run, principal("reader", role="viewer", caps=("forecast:read",)))
    for who in (principal("other"), principal(products=("p-202",)), principal(caps=())):
        with pytest.raises(BatchError, match="forecast-run-not-found"):
            authorize_read(run, who)


class Backend:
    def __init__(self, run):
        self.run, self.calls, self.failure = run, [], None

    def submit(self, request, actor, key):
        self.calls.append((request, actor, key))
        if self.failure:
            raise self.failure
        return self.run

    def get(self, run_id, actor):
        authorize_read(self.run, actor)
        return self.run

    def attempts(self, run_id, actor):
        self.get(run_id, actor)
        return []


def client(tmp_path, backend, *, pipeline=True):
    def mutate(value):
        if pipeline:
            grant = next(g for g in value["grants"] if g["principal_id"] == "local-viewer")
            grant.update(roles=["pipeline"], capabilities=["forecast:run"])

    path, tokens = policy_file(tmp_path, mutate)
    app = create_app(
        Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path),
        forecast_administration=backend,
    )
    return TestClient(app, base_url="http://127.0.0.1"), tokens


def test_http_admission_returns_durable_location_and_private_pins(tmp_path, run):
    backend = Backend(run)
    api, tokens = client(tmp_path, backend)
    with api:
        headers = dict(bearer(tokens["local-viewer"]), **{"Idempotency-Key": "batch-1"})
        response = api.post(
            "/api/v1/forecast-runs",
            headers=headers,
            json=run.input_ref.request.model_dump(mode="json"),
        )
        assert response.status_code == 202
        assert response.headers["Location"] == "/api/v1/forecast-runs/" + run.run_id
        assert "lease_token" not in response.text
        identity = api.get("/api/v1/identity", headers=headers).json()
        assert identity["scope"]["product_ids"] == ["p-101"]
        assert api.get(response.headers["Location"], headers=headers).status_code == 200
        assert api.get(response.headers["Location"] + "/attempts", headers=headers).json() == []
        assert backend.calls[0][1].principal_id == "local-viewer"


def test_http_unauthorized_role_and_duplicate_key_never_call_backend(tmp_path, run):
    backend = Backend(run)
    api, tokens = client(tmp_path, backend, pipeline=False)
    with api:
        for token in tokens.values():
            headers = dict(bearer(token), **{"Idempotency-Key": "batch-1"})
            problem(
                api.post(
                    "/api/v1/forecast-runs",
                    headers=headers,
                    json=run.input_ref.request.model_dump(mode="json"),
                ),
                403,
            )
    assert backend.calls == []
    api, tokens = client(tmp_path, backend)
    with api:
        headers = [
            ("Authorization", "Bearer " + tokens["local-viewer"]),
            ("Idempotency-Key", "key-1"),
            ("Idempotency-Key", "key-1"),
        ]
        problem(
            api.post(
                "/api/v1/forecast-runs",
                headers=headers,
                json=run.input_ref.request.model_dump(mode="json"),
            ),
            422,
        )
    assert backend.calls == []


@pytest.mark.parametrize(
    "failure,status",
    [
        (BatchError(409, "idempotency-conflict"), 409),
        (BatchError(429, "queue-full"), 429),
        (ValueError("secret-private-marker"), 503),
    ],
)
def test_http_errors_are_static_and_safe(tmp_path, run, failure, status):
    backend = Backend(run)
    backend.failure = failure
    api, tokens = client(tmp_path, backend)
    with api:
        headers = dict(bearer(tokens["local-viewer"]), **{"Idempotency-Key": "batch-1"})
        response = api.post(
            "/api/v1/forecast-runs",
            headers=headers,
            json=run.input_ref.request.model_dump(mode="json"),
        )
        problem(response, status)
        assert "secret-private-marker" not in response.text


def test_grant_requires_pipeline_role_and_explicit_scope():
    raw = {
        "schema_version": "1.0",
        "policy_id": "test-policy",
        "grants": [
            {
                "principal_id": "pipeline",
                "roles": ["admin"],
                "capabilities": ["forecast:run"],
                "scope": None,
            }
        ],
    }
    with pytest.raises(ValidationError):
        GrantTemplate.model_validate_json(json.dumps(raw))
    raw["grants"][0]["roles"] = ["pipeline"]
    with pytest.raises(ValidationError):
        GrantTemplate.model_validate_json(json.dumps(raw))


def test_supervisor_kills_own_computation_child_when_lease_is_lost(monkeypatch, run):
    profile, _ = fixture()
    running = run.model_copy(
        update={
            "status": "running",
            "started_at": datetime.now(UTC),
            "policy": QueuePolicy(lease_seconds=2, heartbeat_seconds=0.1),
        }
    )
    claim = Claim(running, profile, "00000000-0000-0000-0000-000000000001")
    popen = subprocess.Popen
    children = []

    def slow_child(*args, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(worker.subprocess, "Popen", slow_child)

    class Queue:
        environment = "test"

        def heartbeat(self, claim):
            raise LeaseLost("batch_lease_lost")

        def complete(self, *args):
            pytest.fail("stale worker published output")

        def fail(self, *args, **kwargs):
            pytest.fail("stale worker changed run state")

    assert worker.run_attempt(Queue(), claim, compose=False) == "lease_lost"
    assert children[0].poll() == -9


def test_executor_checks_the_exact_loaded_bytes_after_registry_validation(monkeypatch, run):
    from retailops_ai.forecast_jobs import executor

    profile, _ = fixture()

    class Registry:
        def __init__(self, **kwargs):
            pass

        def validate(self, binding):
            pass  # Validated capsule was subsequently replaced in the artifact store.

        def artifact(self, *args):
            return b'{"format":"mechanics-v1","bias":999.0}'

    monkeypatch.setattr(executor, "MLflowRegistry", Registry)
    with pytest.raises(ValueError, match="loaded_model_checksum_mismatch"):
        executor.execute(run, profile, compose=False)
