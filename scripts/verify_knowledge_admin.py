"""Real PostgreSQL/HTTP run acceptance, process death and immutable report retention."""

import json
import os
import secrets
import selectors
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from verify_rag_lifecycle import require, synthetic_approval

from retailops_ai.adapters import index_jobs as jobs
from retailops_ai.adapters.vector_store import index_engine, read_candidate
from retailops_ai.config import load_settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.data_contracts.run import RunRecord
from retailops_ai.knowledge.indexes import IndexCandidate
from retailops_ai.knowledge.jobs import IndexBuildProfile
from retailops_ai.knowledge.releases import IndexValidation
from retailops_ai.pipelines.releases import validate_candidate
from retailops_ai.security.local import token_fingerprint


def build_profile(candidate: IndexCandidate) -> IndexBuildProfile:
    value = {
        "schema_version": "1.0",
        "environment": "test",
        "approval": synthetic_approval(candidate).model_dump(mode="json"),
        "chunks": candidate.chunks.model_dump(mode="json"),
        "embedding_config": candidate.manifest.embedding_config.model_dump(mode="json"),
        "evaluation_set_id": "offline-index-mechanics-v1",
        "purpose": "offline_build_mechanics_only",
    }
    value["profile_id"] = "index-build-profile-sha256-" + canonical_sha256(value)
    return IndexBuildProfile.model_validate_json(json.dumps(value))


def verify_administration(candidate: IndexCandidate) -> dict[str, object]:
    engine = index_engine(load_settings())
    admin = jobs.PostgresIndexAdministration(engine, "test")
    local = jobs.PostgresIndexAdministration(engine, "local")
    checks = []
    profile = build_profile(candidate)
    request = profile.request()
    suffix = secrets.token_hex(8)
    try:
        before = admin.current()
        if before is None:
            raise RuntimeError("admin_test_current_missing")
        require(local.current() is None, "actual_index_unexpectedly_active")
        require(jobs.register_profile(engine, profile), "admin_profile_not_registered")
        require(not jobs.register_profile(engine, profile), "admin_profile_replay_changed")

        def submit(_: int) -> RunRecord:
            separate = index_engine(load_settings())
            try:
                return jobs.PostgresIndexAdministration(separate, "test").submit(
                    request, "fixture-index-admin", "concurrent-" + suffix
                )
            finally:
                separate.dispose()

        with ThreadPoolExecutor(max_workers=6) as pool:
            runs = list(pool.map(submit, range(6)))
        run = runs[0]
        require(all(r == run for r in runs), "admin_concurrent_duplicate_run")
        reversed_request = request.model_copy(update={"sources": tuple(reversed(request.sources))})
        require(
            admin.submit(reversed_request, "fixture-index-admin", "concurrent-" + suffix) == run,
            "admin_source_order_changed_request_hash",
        )
        changed = request.model_copy(update={"evaluation_set_id": "unapproved-fixture"})
        for boundary, config_body, key, expected_status, expected_code in [
            (admin, changed, "concurrent-" + suffix, 409, "idempotency-conflict"),
            (admin, changed, "unapproved-" + suffix, 422, "configuration-not-approved"),
            (local, request, "local-" + suffix, 422, "configuration-not-approved"),
        ]:
            try:
                boundary.submit(config_body, "fixture-index-admin", key)
            except jobs.IndexJobError as error:
                require(
                    (error.status, error.code) == (expected_status, expected_code),
                    "admin_wrong_rejection",
                )
            else:
                raise RuntimeError("admin_unapproved_request_persisted")
        second_principal = admin.submit(request, "fixture-other-admin", "concurrent-" + suffix)
        require(second_principal.run_id != run.run_id, "admin_key_not_principal_scoped")
        jobs.cancel_run(engine, "test", second_principal.run_id)
        checks.append("concurrent_idempotency_principal_environment_and_approved_config_boundaries")

        worker_code = """
import json,sys
from retailops_ai.adapters import index_jobs as j
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.config import load_settings
original=j.build_index
def paused(chunks,config):
    print('worker_claimed',flush=True)
    sys.stdin.readline()
    return original(chunks,config)
j.build_index=paused
engine=index_engine(load_settings())
try:
    result=j.execute_run(engine,'test',sys.argv[1])
    print(json.dumps({'status':result.status}))
except j.IndexJobError as error:
    print(json.dumps({'error':error.code}))
finally:
    engine.dispose()
"""

        def paused_worker(run_id: str) -> subprocess.Popen[str]:
            child = subprocess.Popen(  # noqa: S603 - fixed fixture worker
                [sys.executable, "-c", worker_code, run_id],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )  # noqa: S603 - fixed fixture worker
            try:
                if child.stdout is None:
                    raise RuntimeError("admin_worker_stdout_missing")
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    require(bool(selector.select(timeout=10)), "admin_worker_claim_timeout")
                require(
                    child.stdout.readline().strip() == "worker_claimed", "admin_worker_not_claimed"
                )
            except Exception:
                child.kill()
                child.wait(timeout=5)
                raise
            return child

        child = paused_worker(run.run_id)
        try:
            running = admin.get(run.run_id)
            require(running.status == "running", "admin_running_not_durable")
            try:
                jobs.execute_run(engine, "test", run.run_id)
            except jobs.IndexJobError as error:
                require(error.code == "worker-busy", "admin_wrong_worker_contention")
            else:
                raise RuntimeError("admin_second_worker_entered")
        finally:
            child.kill()
            child.wait(timeout=5)
        cli = shutil.which("retailops-ai")
        require(cli is not None, "admin_cli_missing")
        result = subprocess.run(  # noqa: S603 - controlled CLI and fixture run
            [str(cli), "knowledge-index-work", "--run-id", run.run_id],
            env={**os.environ, "APP_ENV": "test"},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
            text=True,
        )  # noqa: S603 - controlled CLI and fixture run
        require(
            result.returncode == 0 and json.loads(result.stdout)["status"] == "succeeded",
            "admin_worker_resume_failed",
        )
        succeeded = admin.get(run.run_id)
        require(
            succeeded.started_at == running.started_at
            and succeeded.input_ref == run.input_ref
            and succeeded.attempt == 1,
            "admin_resume_changed_pins",
        )
        require(
            jobs.execute_run(engine, "test", run.run_id) == succeeded,
            "admin_completed_retry_changed",
        )
        require(
            admin.submit(request, "fixture-index-admin", "concurrent-" + suffix) == succeeded,
            "admin_terminal_idempotency_changed",
        )
        with engine.connect() as connection:
            require(
                read_candidate(connection, candidate.manifest.index_id) == candidate,
                "admin_worker_candidate_not_complete",
            )
        require(admin.current() == before, "admin_worker_activated_candidate")
        checks.append("real_worker_sigkill_resumes_same_pins_session_exclusion_and_terminal_replay")

        cancel = admin.submit(request, "fixture-index-admin", "cancel-" + suffix)
        child = paused_worker(cancel.run_id)
        try:
            cancelled = jobs.cancel_run(engine, "test", cancel.run_id)
            require(cancelled.status == "cancelled", "admin_cancellation_failed")
            if child.stdin is None:
                raise RuntimeError("admin_worker_stdin_missing")
            child.stdin.write("continue\n")
            child.stdin.flush()
            child.wait(timeout=15)
            require(
                child.stdout is not None
                and json.loads(child.stdout.read())["error"] == "claim-lost",
                "admin_cancelled_worker_published",
            )
            require(
                admin.get(cancel.run_id) == cancelled
                and jobs.execute_run(engine, "test", cancel.run_id) == cancelled,
                "admin_cancelled_run_reopened",
            )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        require(admin.current() == before, "admin_cancel_changed_pointer")
        checks.append("cancellation_fences_running_worker_and_keeps_active_pointer")

        failed = admin.submit(request, "fixture-index-admin", "gate-" + suffix)
        original_validation = validate_candidate

        def failed_gate(value: IndexCandidate) -> IndexValidation:
            report = original_validation(value).model_dump(mode="json")
            report["checks"]["deterministic_fake_vectors"] = False
            report["result"] = "failed"
            report["validation_id"] = "index-validation-sha256-" + canonical_sha256(
                {k: v for k, v in report.items() if k != "validation_id"}
            )
            return IndexValidation.model_validate_json(json.dumps(report))

        with patch.object(jobs, "validate_candidate", failed_gate):
            failed = jobs.execute_run(engine, "test", failed.run_id)
        require(
            failed.status == "failed"
            and failed.error is not None
            and failed.error.code == "gate_failed"
            and failed.output_ref is None,
            "admin_failed_gate_claimed_success",
        )
        require(
            jobs.execute_run(engine, "test", failed.run_id) == failed, "admin_failed_run_reopened"
        )
        checks.append("failed_required_gate_is_terminal_with_safe_error_and_no_output")

        def rejected(sql: str, parameters: dict[str, object]) -> None:
            try:
                with engine.begin() as connection:
                    connection.execute(text(sql), parameters)
            except DBAPIError as error:
                require(
                    getattr(error.orig, "sqlstate", None) == "23514", "admin_wrong_constraint_error"
                )
            else:
                raise RuntimeError("admin_immutable_state_modified")

        rejected(
            "UPDATE ai.knowledge_index_runs SET record=jsonb_set(record,'{requested_by}',to_jsonb('other-admin'::text)) WHERE run_id=:id",
            {"id": run.run_id},
        )
        rejected(
            "UPDATE ai.knowledge_index_runs SET record=jsonb_set(record,'{status}',to_jsonb('queued'::text)) WHERE run_id=:id",
            {"id": run.run_id},
        )
        rejected("DELETE FROM ai.knowledge_index_runs WHERE run_id=:id", {"id": run.run_id})
        rejected(
            "UPDATE ai.rag_build_profiles SET profile=profile WHERE profile_id=:id",
            {"id": profile.profile_id},
        )
        rejected(
            "DELETE FROM ai.rag_index_reports WHERE profile_id=:id", {"id": profile.profile_id}
        )
        # A SQL writer cannot convert failed check booleans into success by changing only result.
        probe = admin.submit(request, "fixture-index-admin", "sql-gate-" + suffix)
        with engine.begin() as connection:
            bad_report = connection.scalar(
                text(
                    "SELECT report FROM ai.rag_index_reports WHERE profile_id=:id AND report->'validation'->>'result'='failed'"
                ),
                {"id": profile.profile_id},
            )
            bad_report = json.loads(json.dumps(bad_report))
            bad_report["validation"]["result"] = "passed"
            bad_report["validation"]["validation_id"] = (
                "index-validation-sha256-"
                + canonical_sha256(
                    {k: v for k, v in bad_report["validation"].items() if k != "validation_id"}
                )
            )
            bad_report["report_id"] = "index-run-report-sha256-" + canonical_sha256(
                {k: v for k, v in bad_report.items() if k != "report_id"}
            )
            connection.execute(
                text(
                    "INSERT INTO ai.rag_index_reports(report_id,profile_id,index_id,report) VALUES (:id,:profile,:index,CAST(:report AS jsonb))"
                ),
                {
                    "id": bad_report["report_id"],
                    "profile": profile.profile_id,
                    "index": candidate.manifest.index_id,
                    "report": json.dumps(bad_report),
                },
            )
            raw = probe.model_dump(mode="json")
            raw.update(status="running", started_at=datetime.now(UTC).isoformat())
            raw = RunRecord.model_validate_json(json.dumps(raw)).model_dump(mode="json")
            connection.execute(
                text(
                    "UPDATE ai.knowledge_index_runs SET record=CAST(:record AS jsonb),claim_token=CAST(:claim AS uuid) WHERE run_id=:id"
                ),
                {
                    "id": probe.run_id,
                    "record": json.dumps(raw),
                    "claim": "00000000-0000-4000-8000-000000000001",
                },
            )
        raw.update(
            status="succeeded",
            completed_at=datetime.now(UTC).isoformat(),
            output_ref={
                "kind": "knowledge_index",
                "complete": True,
                "index_id": candidate.manifest.index_id,
                "manifest_ref": "db:ai.rag_indexes:" + candidate.manifest.index_id,
                "evaluation_report_ref": "db:ai.rag_index_reports:" + bad_report["report_id"],
                "activation_status": "candidate",
            },
        )
        raw = RunRecord.model_validate_json(json.dumps(raw)).model_dump(mode="json")
        rejected(
            "UPDATE ai.knowledge_index_runs SET record=CAST(:record AS jsonb),claim_token=NULL,output_index_id=:index,report_id=:report WHERE run_id=:id",
            {
                "id": probe.run_id,
                "record": json.dumps(raw),
                "index": candidate.manifest.index_id,
                "report": bad_report["report_id"],
            },
        )
        require(admin.get(probe.run_id).status == "running", "admin_false_success_committed")
        jobs.cancel_run(engine, "test", probe.run_id)
        checks.append("database_rejects_pinned_input_terminal_state_profile_and_report_mutations")
        checks.append("database_success_requires_all_check_booleans_not_only_passed_label")

        with tempfile.TemporaryDirectory(prefix="retailops-admin-http-") as directory:
            folder = Path(directory)
            tokens = {
                "fixture-index-admin": secrets.token_urlsafe(32),
                "fixture-reader": secrets.token_urlsafe(32),
                "fixture-access-admin": secrets.token_urlsafe(32),
            }
            now = datetime.now(UTC)
            policy = {
                "schema_version": "1.0",
                "policy_id": "index-admin-loopback-fixture",
                "grants": [
                    {
                        "principal_id": principal,
                        "roles": ["operator" if principal == "fixture-reader" else "admin"],
                        "capabilities": [
                            "forecast:read"
                            if principal == "fixture-reader"
                            else "access:admin"
                            if principal == "fixture-access-admin"
                            else "knowledge:index"
                        ],
                        "scope": {
                            "product_ids": ["fixture-product"],
                            "selling_location_ids": ["fixture-store"],
                            "channels": ["store"],
                        }
                        if principal == "fixture-reader"
                        else None,
                    }
                    for principal in tokens
                ],
                "credentials": [
                    {
                        "principal_id": principal,
                        "token_sha256": token_fingerprint(token),
                        "not_before": (now - timedelta(seconds=30)).isoformat(),
                        "expires_at": (now + timedelta(hours=1)).isoformat(),
                        "revoked": False,
                    }
                    for principal, token in tokens.items()
                ],
            }
            path = folder / "policy.json"
            path.write_text(json.dumps(policy))
            path.chmod(0o600)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            child = subprocess.Popen(  # noqa: S603 - fixed loopback service
                [str(cli), "serve"],
                env={
                    **os.environ,
                    "APP_ENV": "test",
                    "ARTIFACT_ROOT": str(folder),
                    "API_AUTH_FILE": str(path),
                    "NETWORK_MODE": "local",
                    "HTTP_HOST": "127.0.0.1",
                    "HTTP_PORT": str(port),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )  # noqa: S603 - fixed loopback service

            def http(
                route: str,
                body: dict[str, Any] | None = None,
                principal: str | None = "fixture-index-admin",
                key: str | None = None,
            ) -> tuple[int, dict[str, Any], Any]:
                headers = {"Content-Type": "application/json"}
                if principal:
                    headers["Authorization"] = "Bearer " + tokens[principal]
                if key:
                    headers["Idempotency-Key"] = key
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/v1/" + route,
                    data=json.dumps(body).encode() if body is not None else None,
                    headers=headers,
                    method="POST" if body is not None else "GET",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 - fixed loopback HTTP
                        return response.status, json.loads(response.read()), response.headers
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read()), error.headers

            try:
                for _ in range(80):
                    try:
                        code, current, headers = http("knowledge-indexes/current")
                        break
                    except urllib.error.URLError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("admin_http_not_started")
                require(
                    code == 200
                    and current == before.model_dump(mode="json")
                    and headers.get("Cache-Control") == "no-store",
                    "admin_http_current_unbound",
                )
                body = request.model_dump(mode="json")
                for principal, status in [
                    (None, 401),
                    ("fixture-reader", 403),
                    ("fixture-access-admin", 403),
                ]:
                    for route, payload in [
                        ("knowledge-index-runs", body),
                        ("knowledge-index-runs/" + run.run_id, None),
                        ("knowledge-indexes/current", None),
                    ]:
                        code, error_body, headers = http(
                            route, payload, principal, "http-denied-" + suffix
                        )
                        require(
                            code == status
                            and headers.get("Cache-Control") == "no-store"
                            and all(
                                token not in json.dumps(error_body) for token in tokens.values()
                            ),
                            "admin_http_auth_failed",
                        )
                code, accepted, headers = http(
                    "knowledge-index-runs", body, key="concurrent-" + suffix
                )
                require(
                    code == 202
                    and accepted == succeeded.model_dump(mode="json")
                    and headers.get("Location") == "/api/v1/knowledge-index-runs/" + run.run_id,
                    "admin_http_replay_location_failed",
                )
                require(
                    http("knowledge-index-runs/" + run.run_id)[1] == accepted,
                    "admin_http_run_get_failed",
                )
                code, error_body, _ = http(
                    "knowledge-index-runs",
                    changed.model_dump(mode="json"),
                    key="concurrent-" + suffix,
                )
                require(
                    code == 409 and error_body.get("code") == "idempotency-conflict",
                    "admin_http_conflict_failed",
                )
                require(http("knowledge-index-runs", body)[0] == 422, "admin_http_key_not_required")
                require(
                    http(
                        "knowledge-index-runs",
                        {**body, "prompt": "private-marker"},
                        key="http-invalid-" + suffix,
                    )[0]
                    == 422,
                    "admin_http_untrusted_config_accepted",
                )
                unknown = "run-" + "0" * 32
                code, error_body, _ = http("knowledge-index-runs/" + unknown)
                require(
                    code == 404 and error_body.get("code") == "index-run-not-found",
                    "admin_http_missing_run_failed",
                )
            finally:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        checks.append("real_http_admin_capability_401_403_422_409_location_run_and_current")

        pending = []
        try:
            for ordinal in range(101):
                try:
                    pending.append(
                        admin.submit(request, "fixture-index-admin", f"budget-{suffix}-{ordinal}")
                    )
                except jobs.IndexJobError as error:
                    require(
                        error.code == "queue-full" and error.status == 429,
                        "admin_queue_wrong_limit",
                    )
                    break
            else:
                raise RuntimeError("admin_queue_unbounded")
            with engine.connect() as connection:
                require(
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM ai.knowledge_index_runs WHERE status IN ('queued','running')"
                        )
                    )
                    == 100,
                    "admin_queue_budget_raced",
                )
        finally:
            for item in pending:
                jobs.cancel_run(engine, "test", item.run_id)
        checks.append("pending_queue_budget_is_one_hundred_before_any_build")
        queued = admin.submit(request, "fixture-index-admin", "retained-queued-" + suffix)
        require(
            admin.current() == before and local.current() is None, "admin_jobs_changed_active_index"
        )
        return {
            "result": "passed",
            "checks": checks,
            "actual_corpus_activated": False,
            "test_only": True,
            "retained_runs": [
                r.model_dump(mode="json") for r in (succeeded, failed, cancelled, queued)
            ],
            "profile_id": profile.profile_id,
        }
    finally:
        engine.dispose()


def verify_retention(
    expected: list[dict[str, Any]], *, cleanup: bool = False
) -> list[dict[str, Any]]:
    engine = index_engine(load_settings())
    try:
        admin = jobs.PostgresIndexAdministration(engine, "test")
        snapshots = []
        for raw in expected:
            run = RunRecord.model_validate_json(json.dumps(raw))
            require(admin.get(run.run_id) == run, "admin_run_changed_after_restart")
            with engine.connect() as connection:
                profile = connection.scalar(
                    text("SELECT profile FROM ai.rag_build_profiles WHERE profile_id=:id"),
                    {"id": raw["input_ref"]["profile_id"]},
                )
                require(
                    profile is not None
                    and IndexBuildProfile.model_validate_json(json.dumps(profile))
                    .request()
                    .request_hash()
                    == raw["input_ref"]["request_hash"],
                    "admin_profile_lost_after_restart",
                )
                if run.status == "succeeded":
                    row = connection.execute(
                        text(
                            "SELECT r.report,k.output_index_id FROM ai.knowledge_index_runs k JOIN ai.rag_index_reports r USING(report_id) WHERE k.run_id=:id"
                        ),
                        {"id": run.run_id},
                    ).one()
                    require(
                        row.report["validation"]["result"] == "passed"
                        and read_candidate(connection, row.output_index_id) is not None,
                        "admin_output_lost_after_restart",
                    )
            if cleanup and run.status == "queued":
                require(run.requested_by == "fixture-index-admin", "admin_cleanup_outside_fixture")
                run = jobs.cancel_run(engine, "test", run.run_id)
            snapshots.append(run.model_dump(mode="json"))
        return snapshots
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        if sys.argv[1:] not in (["--retention-check"], ["--retention-check-and-cleanup"]):
            raise RuntimeError("unsupported_admin_verification")
        snapshots = verify_retention(
            json.loads(sys.stdin.read(100_001)),
            cleanup=sys.argv[1:] == ["--retention-check-and-cleanup"],
        )
        print(json.dumps({"result": "passed", "retained_runs": snapshots}))
    except Exception:
        print('{"error":"index_run_retention_failed"}', file=sys.stderr)
        raise SystemExit(1) from None
