"""A queued job and a lost registration response survive coherent PostgreSQL/MLflow restore."""

import json
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from test_access import bearer
from test_v12_inference import actor as promoter
from test_v12_queue import actor
from v12_http_fixture import PATH, job_client

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.contracts import BatchRequest, QueuePolicy
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import TEST_MODEL, V12LifecycleRequest
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry


def test_pending_v12_state_restore_and_recovery(tmp_path, monkeypatch):
    invocation = os.environ.get("AI05_V12_PRIVATE_INVOCATION")
    if not invocation:
        pytest.fail("Task-owned disposable runner required", pytrace=False)
    fd = os.open(invocation, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        assert (
            stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077
        )
        control = json.loads(stream.read(16384))
    engine = create_engine(control["database_url"], hide_parameters=True)
    state_file = Path(control["state_file"])
    try:
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
        state = json.loads(state_file.read_bytes())
        profile = PostgresInputStore(engine, "test").get(state["queue_cancelled_profile_id"]).inputs
        principal = actor(profile)
        queue = PostgresV12Queue(
            engine, "test", policy=QueuePolicy(run_timeout_seconds=900), mechanics=True
        )
        registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
        journal = PostgresV12Journal(engine)
        if os.environ.get("AI05_V12_BACKUP_PREPARE") == "1":
            queued = queue.submit(
                BatchRequest(
                    profile_id=profile.profile_id, as_of=profile.as_of_time, channel="store"
                ),
                principal,
                "v12-backup-queued-input",
            )
            with journal.locked(TEST_MODEL):
                active = journal.active(TEST_MODEL)
                assert active.release_id == state["release_id"]
                binding = active.binding
            request = V12LifecycleRequest(
                decision_id="decision-v12-backup-lost-registration",
                action="register",
                model_name=TEST_MODEL,
                mlflow_run_id=binding.mlflow_run_id,
                approval_id=binding.approval.release_id,
                approval_sha256=binding.approval_sha256,
                reason="Explicit fixture registration interrupted before its reply.",
            )
            create = registry.create
            created = []

            def lost(source, decision):
                created.append(create(source, decision))
                raise TimeoutError("explicit_backup_fixture_lost_registration_reply")

            monkeypatch.setattr(registry, "create", lost)
            with pytest.raises(TimeoutError, match="lost_registration_reply"):
                V12Lifecycle(registry, journal, environment="test").execute(request, promoter())
            assert len(created) == 1
            with journal.locked(TEST_MODEL):
                assert journal.pending(TEST_MODEL) == [request.decision_id]
                assert journal.step(request.decision_id, "create_attempted") is not None
                assert journal.step(request.decision_id, "completed") is None
                assert journal.active(TEST_MODEL).release_id == state["release_id"]
            assert registry.aliases(TEST_MODEL) == state["aliases"]
            state.update(
                backup_pending_job_id=queued.run_id,
                backup_pending_job=queued.model_dump(mode="json"),
                backup_pending_request=request.model_dump(mode="json"),
                backup_pending_created_version=created[0],
            )
            state["checks"].append(
                "v12_backup_prepared_queued_job_and_lost_registry_reply_with_unfinished_decision"
            )
        else:
            queued = queue.get(state["backup_pending_job_id"], principal)
            request = V12LifecycleRequest.model_validate_json(
                json.dumps(state["backup_pending_request"])
            )
            if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
                assert queued.status == "cancelled"
                result = V12Lifecycle(registry, journal, environment="test").execute(
                    request, promoter()
                )
                assert (
                    result["replayed"] is True
                    and result["model_version"] == state["backup_pending_created_version"]
                )
                state["checks"].append(
                    "v12_restored_recovered_decision_and_resumed_queue_survive_restart"
                )
            else:
                assert queued.model_dump(mode="json") == state["backup_pending_job"]
                assert (
                    queued.status == "queued"
                    and queued.resolved_model.model_version == state["model_version"]
                )
                with journal.locked(TEST_MODEL):
                    assert journal.pending(TEST_MODEL) == [request.decision_id]
                    assert journal.active(TEST_MODEL).release_id == state["release_id"]
                assert registry.find(TEST_MODEL, request.decision_id) == [
                    state["backup_pending_created_version"]
                ]

                def forbidden_create(*_):
                    pytest.fail(
                        "Recovery must find the restored original version, never create another."
                    )

                monkeypatch.setattr(registry, "create", forbidden_create)
                recovered = V12Lifecycle(registry, journal, environment="test").execute(
                    request, promoter()
                )
                assert recovered["model_version"] == state["backup_pending_created_version"]
                with journal.locked(TEST_MODEL):
                    assert not journal.pending(TEST_MODEL)
                    assert journal.active(TEST_MODEL).release_id == state["release_id"]
                assert registry.aliases(TEST_MODEL)["champion"] == state["aliases"]["champion"]
                claim = queue.claim(release_id=queued.release_id)
                assert claim is not None and claim.run.run_id == queued.run_id
                assert claim.run.resolved_model == queued.resolved_model
                queue.cancel(queued.run_id)
                assert queue.get(queued.run_id, principal).status == "cancelled"
                api, token = job_client(tmp_path / "restored-job-http", queue, profile)
                with api:
                    response = api.get(PATH + "/" + queued.run_id, headers=bearer(token))
                    assert response.status_code == 200 and response.json()["status"] == "cancelled"
                    assert response.json()["output_ref"] is None
                state["aliases"] = registry.aliases(TEST_MODEL)
                state["registered_fixture_versions"] = int(state["backup_pending_created_version"])
                state["checks"].extend(
                    [
                        "v12_queued_job_preserves_exact_input_release_model_and_idempotency_state_after_restore",
                        "v12_pending_registration_recovers_original_mlflow_version_without_second_create",
                        "v12_recovered_registration_preserves_champion_and_resumed_job_can_be_claimed_and_cancelled",
                        "v12_restored_queue_state_visible_through_authenticated_asgi_http",
                    ]
                )
        state_file.write_bytes(canonical_bytes(state) + b"\n")
    finally:
        engine.dispose()
