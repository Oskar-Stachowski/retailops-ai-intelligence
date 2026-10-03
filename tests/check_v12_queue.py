"""Explicit v12 queue acceptance on runner-owned real PostgreSQL/MLflow; no real model qualification."""

import json
import os
import stat
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from test_access import bearer, problem
from test_v12_inference import actor as promoter
from test_v12_lifecycle import artifacts as artifacts
from test_v12_lifecycle import inputs as inputs
from test_v12_lifecycle import loaded as loaded
from test_v12_lifecycle import prepared_input as prepared_input
from test_v12_lifecycle import qualification as qualification
from test_v12_lifecycle import request as decision
from test_v12_lifecycle import serving as serving
from test_v12_lifecycle import tables as tables
from test_v12_lifecycle import timeline as timeline
from test_v12_queue import IMAGE, actor, expanded
from v12_http_fixture import PATH, job_client

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecast_jobs.contracts import BatchRequest, QueuePolicy
from retailops_ai.forecast_jobs.input_store import PostgresInputStore
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.forecast_jobs.v12_queue import PostgresV12Queue
from retailops_ai.forecast_jobs.v12_worker import preload, registry_guard, run_attempt
from retailops_ai.model_lifecycle import v12_mlflow
from retailops_ai.model_lifecycle.v12_evidence import load_evidence
from retailops_ai.model_lifecycle.v12_journal import PostgresV12Journal
from retailops_ai.model_lifecycle.v12_lifecycle import V12Lifecycle
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import TEST_MODEL
from retailops_ai.model_lifecycle.v12_registry import MLflowV12Registry, publish_approval


def test_queue_compute_retry_cancel_fencing_and_restart(request, tmp_path, monkeypatch):
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
    registry = MLflowV12Registry(environment="test", port=control["mlflow_port"])
    queue = PostgresV12Queue(engine, "test", mechanics=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT value FROM ai.service_metadata WHERE name='v12_acceptance_owner'")
                )
                == control["owner"]
            )
        state = json.loads(state_file.read_bytes())
        if os.environ.get("AI05_V12_RESTART_INSPECT") == "1":
            profile = PostgresInputStore(engine, "test").get(state["queue_profile_id"]).inputs
            principal = actor(profile)
            assert (
                queue.output(state["queue_run_id"], principal).artifact_id
                == state["queue_receipt_id"]
            )
            assert queue.get(state["queue_run_id"], principal).attempt == 2
            assert [r.status for r in queue.attempts(state["queue_run_id"], principal)] == [
                "failed",
                "succeeded",
            ]
            small_profile = (
                PostgresInputStore(engine, "test").get(state["queue_cancelled_profile_id"]).inputs
            )
            assert (
                queue.get(state["queue_cancelled_run_id"], actor(small_profile)).status
                == "cancelled"
            )
            api, token = job_client(tmp_path / "http-restart", queue, profile)
            with api:
                location = PATH + "/" + state["queue_run_id"]
                result = api.get(location, headers=bearer(token))
                assert result.status_code == 200 and result.json()["attempt"] == 2
                assert result.json()["computation_receipt_id"] == state["queue_receipt_id"]
                assert result.json()["publication_status"] == (
                    "published" if "publication_id" in state else "awaiting_publication"
                )
                if "publication_id" in state:
                    assert result.json()["output_ref"]["artifact_id"] == state["publication_id"]
                assert [
                    r["status"]
                    for r in api.get(location + "/attempts", headers=bearer(token)).json()["items"]
                ] == ["failed", "succeeded"]
            state["checks"].append(
                "v12_queue_receipt_pins_attempt_history_and_cancellation_survive_service_restart"
            )
            state["status"] = "passed"
            state_file.write_bytes(canonical_bytes(state) + b"\n")
            return
        package, model = request.getfixturevalue("serving")
        profile = expanded(request.getfixturevalue("inputs"))
        principal = actor(profile)
        store = PostgresInputStore(engine, "test")
        assert store.register(profile).inputs == profile
        campaign = v12_mlflow.import_evidence(
            load_evidence(model.export.root, model.export.python),
            v12_mlflow.LocalTracking(control["mlflow_port"]),
            tmp_path / "campaign-import",
        )
        imported = publish_approval(
            package,
            model,
            registry,
            campaign_mlflow_run_id=campaign["mlflow_run_id"],
            model=TEST_MODEL,
            actor=promoter(),
            work=tmp_path / "approval-import",
        )
        lifecycle = V12Lifecycle(registry, PostgresV12Journal(engine), environment="test")
        version = lifecycle.execute(decision(imported, "register", "queue-register"), promoter())[
            "model_version"
        ]
        promoted = lifecycle.execute(
            decision(imported, "promote", "queue-promote", version), promoter()
        )
        request_body = BatchRequest(
            profile_id=profile.profile_id, as_of=profile.as_of_time, channel="store"
        )
        api, token = job_client(tmp_path / "http-submit", queue, profile)
        request_label = "v12-computation"
        headers = {**bearer(token), "Idempotency-Key": request_label}
        with api:
            body = request_body.model_dump(mode="json")
            admitted = api.post(PATH, headers=headers, json=body)
            assert admitted.status_code == 202
            run = queue.get(admitted.json()["run_id"], principal)
            assert admitted.headers["location"] == PATH + "/" + run.run_id
            assert admitted.json()["publication_status"] == "not_computed"
            assert admitted.json()["output_ref"] is None
            assert "source_uri" not in admitted.text and "recipe_path" not in admitted.text
            repeated = api.post(PATH, headers=headers, json=body)
            assert repeated.status_code == 202 and repeated.json()["run_id"] == run.run_id
            conflict = api.post(PATH, headers=headers, json={**body, "horizons_days": [7]})
            problem(conflict, 409)
            assert conflict.json()["code"] == "idempotency-conflict"
            spoofed = api.post(
                PATH,
                headers={**bearer(token), "Idempotency-Key": "foreign"},
                json={**body, "product_ids": ["outside"]},
            )
            problem(spoofed, 403)
        foreign_api, foreign_token = job_client(
            tmp_path / "http-foreign", queue, profile, pipeline=False, foreign=True
        )
        with foreign_api:
            problem(foreign_api.get(PATH + "/" + run.run_id, headers=bearer(foreign_token)), 404)
            problem(foreign_api.get(PATH + "/run-" + "b" * 32, headers=bearer(foreign_token)), 404)
            problem(
                foreign_api.post(
                    PATH, headers={**bearer(foreign_token), "Idempotency-Key": "viewer"}, json=body
                ),
                403,
            )
        default_api, default_token = job_client(
            tmp_path / "http-default-namespace", PostgresV12Queue(engine, "test"), profile
        )
        with default_api:
            problem(default_api.get(PATH + "/" + run.run_id, headers=bearer(default_token)), 404)
        state["checks"].append(
            "v12_queue_atomic_idempotent_admission_with_registered_source_and_approved_release"
        )
        state["checks"].append(
            "v12_job_asgi_202_location_sql_idempotency_conflict_scope_and_private_projection"
        )
        # A later promotion must not repin this queued run or its retry.
        version2 = lifecycle.execute(decision(imported, "register", "queue-register2"), promoter())[
            "model_version"
        ]
        current = lifecycle.execute(
            decision(imported, "promote", "queue-promote2", version2), promoter()
        )
        release, assets = preload(
            engine,
            registry,
            queue,
            release_id=promoted["release_id"],
            approval_dir=package,
            run_dir=model.export.root,
            verifier_python=model.export.python,
            image_digest=IMAGE,
        )
        assert release.release_id == run.release_id and release.binding.model_version == version
        assert queue.get(run.run_id, principal).resolved_model.model_version == version
        claim = queue.claim(release_id=release.release_id)
        assert claim is not None and claim.run.run_id == run.run_id
        original = type(assets).predict
        calls = 0

        def partial(self, inputs, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("explicit_test_partial_failure")
            return original(self, inputs, **kwargs)

        monkeypatch.setattr(type(assets), "predict", partial)

        def guard(full):
            registry_guard(engine, registry, release, full=full)

        assert run_attempt(queue, claim, assets, guard=guard) == "failed"
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM ai.v12_batch_receipts WHERE run_id=:id"),
                    dict(id=run.run_id),
                )
                == 0
            )
        assert queue.get(run.run_id, principal).status == "queued"
        assert queue.get(run.run_id, principal).release_id == run.release_id
        assert queue.attempts(run.run_id, principal)[0].status == "failed"
        monkeypatch.setattr(type(assets), "predict", original)
        # Default backoff is pinned at intake; do not rewrite metadata to skip it.
        time.sleep(run.policy.retry_backoff_seconds + 0.1)
        retry = queue.claim(release_id=release.release_id)
        assert retry is not None and retry.run.attempt == 2
        with pytest.raises(LeaseLost):
            queue.heartbeat(claim)
        assert run_attempt(queue, retry, assets, guard=guard) == "succeeded"
        receipt = queue.output(run.run_id, principal)
        assert len(receipt.parts) == 4 and sum(len(p.predictions) for p in receipt.parts) == 560
        assert (
            receipt.release.release_id == run.release_id and receipt.published_forecast_outputs == 0
        )
        api, token = job_client(tmp_path / "http-computed", queue, profile)
        with api:
            location = PATH + "/" + run.run_id
            status = api.get(location, headers=bearer(token))
            assert status.status_code == 200
            assert status.json()["resolved_model"]["model_version"] == version
            assert status.json()["computation_receipt_id"] == receipt.artifact_id
            assert status.json()["publication_status"] == "awaiting_publication"
            assert status.json()["output_ref"] is None
            history = api.get(location + "/attempts", headers=bearer(token))
            assert history.status_code == 200 and history.json()["pagination"]["total"] == 2
            assert [r["status"] for r in history.json()["items"]] == ["failed", "succeeded"]
        with pytest.raises(LeaseLost):
            queue.complete(claim, receipt)
        state["checks"].append(
            "partial_v12_batch_has_no_receipt_then_retry_keeps_old_release_and_commits_all_560_functional_rows"
        )
        small = request.getfixturevalue("inputs")
        store.register(small)
        small_request = BatchRequest(
            profile_id=small.profile_id, as_of=small.as_of_time, channel="store"
        )
        small_principal = actor(small)
        cancelled = queue.submit(small_request, small_principal, "v12-cancel")
        current_release = current["release_id"]
        cancel_claim = queue.claim(release_id=current_release)
        assert cancel_claim.run.run_id == cancelled.run_id
        queue.cancel(cancelled.run_id)
        with pytest.raises(LeaseLost):
            queue.heartbeat(cancel_claim)
        short_queue = PostgresV12Queue(
            engine,
            "test",
            QueuePolicy(lease_seconds=2, heartbeat_seconds=0.2, retry_backoff_seconds=0),
            mechanics=True,
        )
        expired = short_queue.submit(small_request, small_principal, "v12-expire")
        abandoned = short_queue.claim(release_id=current_release)
        assert abandoned.run.run_id == expired.run_id
        time.sleep(2.1)
        recovered = short_queue.claim(release_id=current_release)
        assert (
            recovered.run.run_id == expired.run_id
            and recovered.run.attempt == 2
            and recovered.token != abandoned.token
        )
        with pytest.raises(LeaseLost):
            short_queue.heartbeat(abandoned)
        with pytest.raises(LeaseLost):
            short_queue.fail(abandoned, reason="stale_worker", retryable=True)
        short_queue.fail(recovered, reason="explicit_test_terminal_failure", retryable=False)
        state["checks"].append(
            "cancel_and_expired_lease_recovery_fence_old_workers_and_preserve_pins"
        )
        # SQL guards must reject pin rewrites and shortcut completion independently of the worker.
        for statement in (
            "UPDATE ai.v12_batch_attempts SET record=record",
            "UPDATE ai.v12_batch_receipts SET receipt=receipt",
            "DELETE FROM ai.v12_batch_runs",
        ):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement)
        queued = queue.submit(small_request, small_principal, "v12-pin-guard")
        with pytest.raises(IntegrityError, match="v12_batch_completion_requires_attempt_history"):
            with engine.begin() as connection:
                raw = queued.model_dump(mode="json")
                from datetime import UTC, datetime

                raw.update(
                    status="cancelled",
                    completed_at=datetime.now(UTC).isoformat(),
                    error=dict(code="cancelled", retryable=False),
                )
                connection.execute(
                    text(
                        "UPDATE ai.v12_batch_runs SET record=CAST(:record AS jsonb) WHERE run_id=:id"
                    ),
                    dict(id=queued.run_id, record=json.dumps(raw)),
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ai.v12_batch_runs SET record=jsonb_set(record,'{image_digest}',to_jsonb(CAST(:image AS text))) WHERE run_id=:id"
                    ),
                    dict(id=queued.run_id, image="sha256:" + "d" * 64),
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ai.v12_batch_runs SET record=jsonb_set(record,'{status}','\"succeeded\"'::jsonb) WHERE run_id=:id"
                    ),
                    dict(id=queued.run_id),
                )
        queue.cancel(queued.run_id)
        state["checks"].append(
            "real_v12_sql_rejects_pin_mutations_terminal_shortcuts_and_history_deletion"
        )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai.forecast_batch_runs")) == 0
            assert connection.scalar(text("SELECT count(*) FROM ai.forecast_output_manifests")) == 0
        state.update(
            queue_run_id=run.run_id,
            queue_profile_id=profile.profile_id,
            queue_receipt_id=receipt.artifact_id,
            queue_cancelled_run_id=cancelled.run_id,
            queue_cancelled_profile_id=small.profile_id,
            computed_fixture_rows=560,
            computed_fixture_parts=4,
            release_id=current_release,
            model_version=version2,
            aliases=registry.aliases(TEST_MODEL),
            registered_fixture_versions=6,
            status="restart_pending",
            fixture_boundary="Real PostgreSQL, MLflow, queue, transactions, lease expiry/retry/cancel and worker integration. Full-export/source/predictor/review use explicit small doubles, including invented repeated series for chunk tests. No real source or model is qualified.",
        )
        state_file.write_bytes(canonical_bytes(state) + b"\n")
    finally:
        engine.dispose()
