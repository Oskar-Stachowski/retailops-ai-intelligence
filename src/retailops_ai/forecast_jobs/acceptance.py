"""Fault injection in a fresh, disposable real PostgreSQL/MLflow project only."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRun, QueuePolicy
from retailops_ai.forecast_jobs.executor import execute
from retailops_ai.forecast_jobs.mechanics import fixture
from retailops_ai.forecast_jobs.queue import (
    BatchError,
    Claim,
    LeaseLost,
    PostgresBatchQueue,
    register_mechanics_profile,
)
from retailops_ai.model_lifecycle.acceptance import require, run_acceptance
from retailops_ai.model_lifecycle.contracts import TEST_MODEL
from retailops_ai.model_lifecycle.mlflow import MLflowRegistry
from retailops_ai.security.provision import provision


def principal(name: str = "batch-pipeline", *, product: str = "p-101") -> Principal:
    return Principal(
        name,
        frozenset({"pipeline"}),
        frozenset({"forecast:run"}),
        frozenset({product}),
        frozenset({"s-03"}),
        frozenset({"store"}),
    )


def expect_error(action: Any, kind: type[Exception], code: str | None = None) -> None:
    try:
        action()
    except kind as exc:
        if code is not None:
            require(getattr(exc, "code", None) == code, "unexpected_batch_error_code")
    else:
        raise ValueError("expected_batch_rejection_missing")


def worker() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-m", "retailops_ai.forecast_jobs.worker", "--once", "--mechanics"],
        capture_output=True,
        timeout=45,
        check=True,
    )  # noqa: S603 - fixed worker
    return dict(json.loads(result.stdout))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    if settings.app_env != "test" or settings.database_url is None:
        raise ValueError("batch_acceptance_requires_disposable_test_database")
    engine = create_engine(
        settings.database_url.get_secret_value(), connect_args={"connect_timeout": 3}
    )
    actor = principal()
    policy = QueuePolicy(
        lease_seconds=2,
        heartbeat_seconds=0.2,
        attempt_timeout_seconds=20,
        run_timeout_seconds=60,
        retry_backoff_seconds=0,
        max_pending=3,
        max_pending_per_principal=2,
    )
    queue = PostgresBatchQueue(engine, "test", policy)
    checks: list[str] = []
    try:
        if args.inspect:
            with engine.connect() as connection:
                rows: Any = (
                    connection.execute(
                        text("SELECT record FROM ai.forecast_batch_runs ORDER BY run_id")
                    )
                    .scalars()
                    .all()
                )
                counts = {
                    "runs": len(rows),
                    "attempts": connection.scalar(
                        text("SELECT count(*) FROM ai.forecast_batch_attempts")
                    ),
                    "outputs": connection.scalar(
                        text("SELECT count(*) FROM ai.forecast_mechanics_outputs")
                    ),
                }
                require(
                    all(r["status"] in {"succeeded", "failed", "cancelled"} for r in rows),
                    "pending_batch_after_acceptance",
                )
                require(
                    counts["outputs"] == 2 and counts["attempts"] > counts["runs"],
                    "batch_state_missing_after_restart",
                )
                require(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM ai.forecast_batch_runs WHERE lease_token IS NOT NULL"
                        )
                    )
                    == 0,
                    "stale_batch_lease",
                )
                snapshot = {"runs": rows}
                for table, column, ordering in (
                    ("forecast_batch_profiles", "profile", "profile_id"),
                    ("forecast_batch_attempts", "record", "run_id,attempt"),
                    ("forecast_mechanics_outputs", "output", "artifact_id"),
                ):
                    snapshot[table] = list(
                        connection.scalars(
                            text(f"SELECT {column} FROM ai.{table} ORDER BY {ordering}")  # noqa: S608 - fixed inventory
                        )
                    )
                state_sha256 = canonical_sha256(snapshot)
                for statement in (
                    "UPDATE ai.forecast_batch_profiles SET profile=profile",
                    "UPDATE ai.forecast_batch_attempts SET record=record",
                    "UPDATE ai.forecast_mechanics_outputs SET output=output",
                ):
                    try:
                        connection.execute(text(statement))
                    except IntegrityError:
                        connection.rollback()
                    else:
                        raise ValueError("immutable_batch_history_update_allowed")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "counts": counts,
                        "state_sha256": state_sha256,
                        "checks": [
                            "sigkill_database_restart_retains_runs_attempts_outputs_and_pins",
                            "database_blocks_history_and_profile_mutation",
                        ],
                    }
                )
            )
            return 0
        lifecycle = run_acceptance(inspect=False)
        profile, request = fixture()
        register_mechanics_profile(engine, profile, environment="test")
        register_mechanics_profile(engine, profile, environment="test")
        checks.append("private_content_addressed_test_profile_registration_is_idempotent")
        with tempfile.TemporaryDirectory(prefix="batch-access-") as temporary:
            private = Path(temporary)
            grants = private / "grants.json"
            grants.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "policy_id": "batch-acceptance",
                        "grants": [
                            {
                                "principal_id": "http-pipeline",
                                "roles": ["pipeline"],
                                "capabilities": ["forecast:run"],
                                "scope": {
                                    "product_ids": ["p-101"],
                                    "selling_location_ids": ["s-03"],
                                    "channels": ["store"],
                                },
                            },
                            {
                                "principal_id": "http-viewer",
                                "roles": ["viewer"],
                                "capabilities": ["forecast:read"],
                                "scope": {
                                    "product_ids": ["p-101"],
                                    "selling_location_ids": ["s-03"],
                                    "channels": ["store"],
                                },
                            },
                            {
                                "principal_id": "http-outside",
                                "roles": ["viewer"],
                                "capabilities": ["forecast:read"],
                                "scope": {
                                    "product_ids": ["p-202"],
                                    "selling_location_ids": ["s-03"],
                                    "channels": ["store"],
                                },
                            },
                        ],
                    }
                )
            )
            provision(grants, private / "access", 1)
            tokens = {
                v["principal_id"]: v["bearer_token"]
                for v in json.loads((private / "access/api-client-credentials.json").read_text())[
                    "credentials"
                ]
            }
            server_env = dict(
                os.environ,
                HTTP_PORT="18081",
                HTTP_HOST="127.0.0.1",
                API_AUTH_FILE=str(private / "access/api-access-policy.json"),
            )
            server = subprocess.Popen(
                [sys.executable, "-m", "retailops_ai", "serve"],
                env=server_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )  # noqa: S603 - own server

            def http(
                method: str, path: str, who: str = "http-pipeline", body: Any = None
            ) -> tuple[int, Any, str | None]:
                connection = HTTPConnection("127.0.0.1", 18081, timeout=10)
                try:
                    connection.request(
                        method,
                        path,
                        None if body is None else json.dumps(body),
                        {
                            "Authorization": "Bearer " + tokens[who],
                            "Content-Type": "application/json",
                            "Idempotency-Key": "http-admission",
                        },
                    )
                    response = connection.getresponse()
                    return (
                        response.status,
                        json.loads(response.read()),
                        response.getheader("Location"),
                    )
                finally:
                    connection.close()

            try:
                for _ in range(100):
                    try:
                        if http("GET", "/ready")[0] == 200:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                else:
                    raise ValueError("acceptance_http_server_not_ready")
                body = request.model_dump(mode="json")
                require(
                    http("POST", "/api/v1/forecast-runs", "http-viewer", body)[0] == 403,
                    "viewer_submitted_batch",
                )
                status, run, location = http("POST", "/api/v1/forecast-runs", body=body)
                require(
                    status == 202 and run["status"] == "queued" and location is not None,
                    "batch_http_admission_failed",
                )
                require(
                    http("POST", "/api/v1/forecast-runs", body=body)[1]["run_id"] == run["run_id"],
                    "batch_http_duplicate",
                )
                require(
                    http("POST", "/api/v1/forecast-runs", body=dict(body, horizons_days=[7]))[0]
                    == 409,
                    "batch_http_conflict_missing",
                )
                require(
                    http("GET", str(location), "http-outside")[0] == 404,
                    "batch_status_leaked_scope",
                )
                client = MLflowRegistry(compose=True, environment="test")
                client.set_alias(TEST_MODEL, "champion", "2")
                require(worker()["status"] == "succeeded", "separate_worker_failed")
                done = http("GET", str(location), "http-viewer")[1]
                require(
                    done["status"] == "succeeded"
                    and done["resolved_model"]["model_version"] == "1",
                    "alias_change_rebound_batch",
                )
                require(
                    http("GET", str(location) + "/attempts")[1][0]["status"] == "succeeded",
                    "http_attempt_history_missing",
                )
                with engine.connect() as connection:
                    raw = connection.scalar(
                        text("SELECT output FROM ai.forecast_mechanics_outputs WHERE run_id=:id"),
                        {"id": run["run_id"]},
                    )
                require(
                    len(raw["predictions"]) == 14
                    and all(
                        r["predicted_units"] == r["horizon_days"] + 1 for r in raw["predictions"]
                    ),
                    "numeric_version_prediction_mismatch",
                )
                client.set_alias(TEST_MODEL, "champion", "1")
                checks.extend(
                    [
                        "real_http_202_idempotency_conflict_and_scope_authorization",
                        "separate_supervisor_child_publishes_complete_fourteen_row_receipt_atomically",
                        "alias_change_does_not_change_admitted_numeric_model_or_data",
                    ]
                )
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)

        # Two real connections race for one durable lease.
        race = queue.submit(request, actor, "race")
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: queue.claim(), range(2)))
        require(sum(c is not None for c in claims) == 1, "duplicate_batch_claim")
        claim = next(c for c in claims if c is not None)
        valid_output = execute(claim.run, profile, compose=True)
        invalid_raw = valid_output.model_dump(mode="json")
        invalid_raw["predictions"] = invalid_raw["predictions"][:-1]
        invalid_raw["artifact_id"] = "predictions-sha256-" + canonical_sha256(
            {k: v for k, v in invalid_raw.items() if k != "artifact_id"}
        )
        from retailops_ai.forecast_jobs.contracts import MechanicsOutput

        invalid_output = MechanicsOutput.model_validate_json(json.dumps(invalid_raw))
        expect_error(lambda: queue.complete(claim, invalid_output), ValueError)
        with engine.connect() as connection:
            require(
                connection.scalar(
                    text("SELECT count(*) FROM ai.forecast_mechanics_outputs WHERE run_id=:id"),
                    {"id": race.run_id},
                )
                == 0,
                "partial_output_was_published",
            )
        checks.append("incomplete_grain_is_rejected_without_a_persisted_output")
        queue.heartbeat(claim)
        time.sleep(1.1)
        queue.heartbeat(claim)
        time.sleep(1.1)
        require(queue.claim() is None, "heartbeat_did_not_extend_lease")
        queue.cancel(race.run_id)
        expect_error(
            lambda: queue.complete(claim, execute(claim.run, profile, compose=True)), LeaseLost
        )
        checks.extend(
            [
                "concurrent_workers_only_one_lease",
                "heartbeat_extends_lease_and_cancellation_fences_completion",
            ]
        )

        # SIGKILL after a partial calculation. No partial result is stored.
        crashed = queue.submit(request, actor, "crash")
        crash_code = """import os, sys, time
from sqlalchemy import create_engine
from retailops_ai.config import load_settings
from retailops_ai.forecast_jobs.queue import PostgresBatchQueue
q=PostgresBatchQueue(create_engine(load_settings().database_url.get_secret_value()), "test")
c=q.claim()
assert c is not None
partial=c.profile.rows[0].value+1
print(c.run.run_id, flush=True)
time.sleep(60)
"""
        child = subprocess.Popen(  # noqa: S603 - fixed fault injection child
            [sys.executable, "-c", crash_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            if child.stdout is None:
                raise ValueError("crash_child_stdout_missing")
            require(
                child.stdout.readline().decode().strip() == crashed.run_id,
                "crash_child_did_not_claim",
            )
            with engine.connect() as connection:
                saved = connection.execute(
                    text("SELECT record,lease_token FROM ai.forecast_batch_runs WHERE run_id=:id"),
                    {"id": crashed.run_id},
                ).one()
            old_claim = Claim(
                BatchRun.model_validate_json(json.dumps(saved.record)),
                profile,
                str(saved.lease_token),
            )
            child.kill()
            child.communicate(timeout=5)
            require(child.returncode == -signal.SIGKILL, "worker_sigkill_not_observed")
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)
        time.sleep(2.2)
        recovered = queue.claim()
        require(
            recovered is not None
            and recovered.run.run_id == crashed.run_id
            and recovered.run.attempt == 2,
            "batch_not_reclaimed",
        )
        if recovered is None:
            raise ValueError("recovered_claim_missing")
        expect_error(lambda: queue.heartbeat(old_claim), LeaseLost)
        expect_error(
            lambda: queue.complete(old_claim, execute(old_claim.run, profile, compose=True)),
            LeaseLost,
        )
        from retailops_ai.forecast_jobs.worker import run_attempt

        require(
            run_attempt(queue, recovered, compose=True) == "succeeded", "recovered_attempt_failed"
        )
        history = queue.attempts(crashed.run_id, actor)
        require(
            [r.status for r in history] == ["failed", "succeeded"]
            and all(
                r.input_ref == crashed.input_ref and r.resolved_model == crashed.resolved_model
                for r in history
            ),
            "retry_history_or_pins_changed",
        )
        checks.append(
            "sigkill_after_partial_computation_recovers_same_run_and_pins_with_new_fenced_attempt"
        )

        # Bounded automatic retries retain every failure, then become terminal.
        exhausted = queue.submit(request, actor, "exhausted")
        for attempt in range(1, 4):
            current = queue.claim()
            require(current is not None and current.run.attempt == attempt, "retry_attempt_missing")
            if current is None:
                raise ValueError("retry_claim_missing")
            queue.fail(current, reason="executor_failed", retryable=True)
        terminal = queue.get(exhausted.run_id, actor)
        require(
            terminal.status == "failed"
            and terminal.error is not None
            and not terminal.error.retryable
            and len(queue.attempts(exhausted.run_id, actor)) == 3,
            "retry_budget_not_enforced",
        )
        expect_error(
            lambda: queue.get(exhausted.run_id, principal("outsider", product="p-202")),
            BatchError,
            "forecast-run-not-found",
        )
        checks.append("retry_budget_exhaustion_is_terminal_and_preserves_all_attempts")

        first = queue.submit(request, actor, "quota-one")
        second = queue.submit(request, actor, "quota-two")
        expect_error(lambda: queue.submit(request, actor, "quota-three"), BatchError, "queue-full")
        other = queue.submit(request, principal("other-pipeline"), "quota-one")
        require(other.run_id != first.run_id, "idempotency_key_not_principal_scoped")
        expect_error(
            lambda: queue.submit(request, principal("third-pipeline"), "quota-four"),
            BatchError,
            "queue-full",
        )
        for run in (first, second, other):
            queue.cancel(run.run_id)
        checks.append("global_and_per_principal_backpressure_and_key_namespaces")

        timeout_policy = QueuePolicy(
            lease_seconds=2,
            heartbeat_seconds=0.2,
            attempt_timeout_seconds=2,
            run_timeout_seconds=2,
            max_attempts=1,
            retry_backoff_seconds=0,
        )
        tiny = PostgresBatchQueue(engine, "test", timeout_policy)
        deadline_run = tiny.submit(request, actor, "deadline")
        deadline_claim = tiny.claim()
        require(deadline_claim is not None, "deadline_claim_missing")
        time.sleep(2.2)
        tiny.claim()
        require(
            tiny.get(deadline_run.run_id, actor).status == "failed", "attempt_timeout_not_terminal"
        )
        queued_timeout = tiny.submit(request, actor, "queued-timeout")
        time.sleep(2.2)
        tiny.claim()
        require(
            tiny.get(queued_timeout.run_id, actor).status == "cancelled",
            "queued_timeout_not_cancelled",
        )
        checks.append("attempt_and_overall_deadlines_prevent_unbounded_work")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "purpose": "lifecycle_mechanics_only",
                    "forecast_quality_approved": False,
                    "release_id": lifecycle["release_id"],
                    "profile_id": profile.profile_id,
                    "checks": checks,
                }
            )
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
