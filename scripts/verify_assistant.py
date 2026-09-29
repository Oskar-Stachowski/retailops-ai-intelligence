"""Real PostgreSQL/HTTP mechanics with explicitly scripted evidence; no LLM quality claim."""

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import uvicorn
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from retailops_ai.adapters.assistant_store import PostgresAssistantStore
from retailops_ai.adapters.database import database_engine
from retailops_ai.agent.chat_contracts import AnswerDraft
from retailops_ai.agent.graph_contracts import GraphRequest, GraphResult, SafeTrace
from retailops_ai.agent.suggestions import SuggestionCandidate
from retailops_ai.api.app import create_app
from retailops_ai.assistant.contracts import AssistantQuery, AssistantRun, TraceUsage
from retailops_ai.assistant.service import AdmissionPolicy, AssistantError, RunLease
from retailops_ai.config import Settings
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.security.local import token_fingerprint

PRODUCT = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"
CONFIG = "agent-graph-config-sha256-" + "a" * 64
INDEX = "index-sha256-" + "b" * 64
PORT = 18081


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


class ScriptedBackend:
    source_kind: Literal["fixture", "runtime"] = "fixture"
    config_version = CONFIG
    index_id = INDEX
    deadline_seconds = 45.0
    reserved_tokens = 13500
    reserved_cost = "0.01"

    async def prepare(self, query: AssistantQuery, principal: Principal) -> GraphRequest:
        return GraphRequest.model_validate_json(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "question": query.question,
                    "intent": "operations",
                    "scope": {
                        "product_ids": [str(x) for x in query.scope.product_ids],
                        "selling_location_ids": [str(x) for x in query.scope.store_ids],
                        "channel": "store",
                    },
                    "as_of": datetime.now(UTC).isoformat(),
                    "window": {
                        "start": query.scope.from_.isoformat(),
                        "end": query.scope.to.isoformat(),
                    },
                    "comparison_window": None,
                }
            )
        )

    async def run(self, request: GraphRequest, authorization: str | None) -> GraphResult:
        now = datetime.now(UTC)
        raw = {
            "recommendation_type": "refresh_source_data",
            "product_id": PRODUCT,
            "selling_location_id": STORE,
            "stock_location_id": None,
            "channel": "store",
            "action": "Review data freshness.",
            "priority": "medium",
            "rationale": "The scripted stream has elevated lag.",
            "evidence_refs": ["fixture-operations-proof"],
            "model_release_refs": [],
            "policy_version": "read-only-review-v1",
            "policy_sha256": "c" * 64,
            "source_as_of": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(seconds=300)).isoformat().replace("+00:00", "Z"),
            "freshness_status": "current",
            "requires_human_review": True,
            "status": "proposed",
        }
        candidate = SuggestionCandidate.model_validate_json(
            json.dumps(raw | {"candidate_id": "candidate-sha256-" + canonical_sha256(raw)})
        )
        answer = AnswerDraft.model_validate_json(
            json.dumps(
                {
                    "kind": "answer",
                    "outcome": "answered",
                    "summary": "The scripted operations source has elevated lag.",
                    "evidence": [
                        {
                            "claim": "Stream lag is 120 seconds.",
                            "source_type": "tool",
                            "source_ref": "fixture-operations-proof",
                            "as_of": now.isoformat(),
                            "supporting_refs": [],
                        }
                    ],
                    "recommended_actions": [candidate.draft_action().model_dump(mode="json")],
                    "confidence": "medium",
                    "data_freshness": {
                        key: {"as_of": None, "status": "not_requested"}
                        for key in ("sales", "inventory", "predictions")
                    },
                    "citations": [],
                    "limitations": ["Scripted acceptance evidence only."],
                }
            )
        )
        trace = SafeTrace(
            schema_version="1.0",
            trace_id="trace-" + uuid4().hex,
            correlation_id="correlation-" + uuid4().hex,
            owner_id="acceptance-http",
            scope=request.scope,
            config_id=CONFIG,
            chat_config_id="agent-chat-config-sha256-" + "d" * 64,
            request_sha256=canonical_sha256(request.model_dump(mode="json")),
            index_id=None,
            release_refs=[],
            source_refs=["fixture-operations-proof"],
            release_refs_total=0,
            source_refs_total=1,
            status="succeeded",
            error_code=None,
            nodes=[],
            tools=[],
            tool_calls=1,
            model_calls=0,
            extra_evidence_rounds=0,
            repairs=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost="0",
            fixture_only=True,
            created_at=now,
        )
        return GraphResult(
            status="succeeded", error_code=None, answer=answer, trace=trace, suggestions=[candidate]
        )


def settings(path: Path | None = None) -> Settings:
    return Settings(
        APP_ENV="test",
        ARTIFACT_ROOT="./artifacts",
        API_AUTH_FILE=path,
        DATABASE_URL=os.environ["DATABASE_URL"],
        NETWORK_MODE="compose",
    )


def principal(owner: str) -> Principal:
    return Principal(
        owner,
        frozenset({"operator"}),
        frozenset({"assistant:query", "operations:read"}),
        frozenset({PRODUCT}),
        frozenset({STORE}),
        frozenset({"store"}),
    )


def lease(owner: str, *, tokens: int = 13500, cost: str = "0.01") -> RunLease:
    now = datetime.now(UTC)
    run = AssistantRun(
        trace_id=uuid4(),
        answer_id=None,
        status="running",
        outcome=None,
        requested_at=now,
        completed_at=None,
        agent_config_version=CONFIG,
        index_id=INDEX,
        nodes=[],
        tools=[],
        usage=TraceUsage(input_tokens=0, output_tokens=0, duration_ms=0.0, estimated_cost="0"),
        error_code=None,
    )
    return RunLease(
        run=run,
        claim=uuid4(),
        owner_id=owner,
        scope_json=json.dumps(
            {"product_ids": [PRODUCT], "selling_location_ids": [STORE], "channel": "store"}
        ),
        request_sha256="e" * 64,
        reserved_tokens=tokens,
        reserved_cost=cost,
    )


async def database_checks() -> dict[str, Any]:
    engine = database_engine(settings())
    other = database_engine(settings())
    store = PostgresAssistantStore(engine, "test")
    replica = PostgresAssistantStore(other, "test")
    checks = []
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM ai.assistant_runs WHERE owner_id LIKE 'acceptance-%'")
            )
        owners = ["acceptance-parallel"] * 3
        results = await asyncio.gather(*(admit_process(lease(owner)) for owner in owners))
        accepted = [value for value in results if isinstance(value, RunLease)]
        require(
            len(accepted) == 2
            and sum(isinstance(value, AssistantError) and value.status == 429 for value in results)
            == 1,
            "shared_parallel_admission",
        )
        active = accepted[0]
        require(
            await replica.get(active.run.trace_id, principal("acceptance-parallel")) is not None,
            "replica_running_visibility",
        )
        require(
            await replica.get(active.run.trace_id, principal("acceptance-foreign")) is None,
            "foreign_trace_hidden",
        )
        require(
            await store.get(
                active.run.trace_id, replace(principal(active.owner_id), product_ids=frozenset())
            )
            is None,
            "scope_revocation",
        )
        final = AssistantRun(
            **{
                **active.run.model_dump(),
                "status": "failed",
                "completed_at": datetime.now(UTC),
                "error_code": "cancelled",
            }
        )
        await replica.finish(active, final, None, [])
        reclaimed = await store.admit(lease(active.owner_id), AdmissionPolicy(), 45.0)
        try:
            await store.finish(active, final, None, [])
        except AssistantError as exc:
            require(exc.status == 504, "fencing_status")
        else:
            raise RuntimeError("fencing_failed")
        checks.append("two_replicas_parallel_limit_owner_scope_fenced_completion")
        for item in (accepted[1], reclaimed):
            final = AssistantRun(
                **{
                    **item.run.model_dump(),
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "error_code": "cancelled",
                }
            )
            await store.finish(item, final, None, [])
        mismatch = AdmissionPolicy(capacity=50)
        try:
            await replica.admit(lease("acceptance-mismatch"), mismatch, 45.0)
        except AssistantError as exc:
            require(exc.status == 503, "policy_mismatch_status")
        else:
            raise RuntimeError("policy_mismatch_accepted")
        timed = await store.admit(lease("acceptance-expiry"), AdmissionPolicy(), 0.03)
        await asyncio.sleep(0.04)
        expired = await replica.get(timed.run.trace_id, principal(timed.owner_id))
        require(
            expired is not None and expired.error_code == "deadline_exceeded",
            "crash_lease_not_expired",
        )
        try:
            await store.finish(timed, final, None, [])
        except (AssistantError, ValueError):
            pass
        else:
            raise RuntimeError("expired_lease_finished")
        checks.append("policy_binding_expired_crash_lease_not_resumable")
        budget = PostgresAssistantStore(engine, "local")
        policy = AdmissionPolicy(
            tokens_per_principal_window=20000, cost_per_principal_window="0.025"
        )
        for owner, tokens, cost in [
            ("acceptance-tokens", 13500, "0.01"),
            ("acceptance-cost", 100, "0.02"),
        ]:
            first = await budget.admit(lease(owner, tokens=tokens, cost=cost), policy, 45.0)
            final = AssistantRun(
                **{
                    **first.run.model_dump(),
                    "status": "failed",
                    "completed_at": datetime.now(UTC),
                    "error_code": "cancelled",
                }
            )
            await budget.finish(first, final, None, [])
            try:
                await budget.admit(lease(owner, tokens=tokens, cost=cost), policy, 45.0)
            except AssistantError as exc:
                require(exc.status == 429, "reservation_limit_status")
            else:
                raise RuntimeError("reservation_limit_failed")
        checks.append("tokens_cost_window_debit_survives_failed_run")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM ai.assistant_runs WHERE environment='local' AND owner_id LIKE 'acceptance-%'"
                )
            )
            await connection.execute(
                text("DELETE FROM ai.assistant_admission_policy WHERE environment='local'")
            )
        return {"checks": checks, "result": "passed"}
    finally:
        await engine.dispose()
        await other.dispose()


def request(
    path: str, token: str, body: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}" + path,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 - fixed loopback
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def start(path: Path, token: str) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(  # noqa: S603 - fixed child verifier, owned private policy
        [sys.executable, str(Path(__file__).resolve()), "--http", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )  # noqa: S603 - fixed child verifier
    for _ in range(200):
        if process.poll() is not None:
            break
        try:
            if request("/health", token)[0] == 200:
                return process
        except OSError:
            pass
        time.sleep(0.1)
    if process.poll() is None:
        process.kill()
    _, stderr = process.communicate(timeout=5)
    for line in (stderr or b"").decode(errors="replace").splitlines():
        try:
            error = json.loads(line)
        except ValueError:
            continue
        if isinstance(error, dict) and error.get("error") == "assistant_acceptance_failed":
            kind = error.get("type")
            if isinstance(kind, str) and re.fullmatch(r"[A-Za-z]+", kind):
                raise RuntimeError("assistant_http_child_" + kind.lower())
    raise RuntimeError("assistant_http_start_failed")


async def http_checks() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="assistant-http-") as directory:
        path = Path(directory) / "policy.json"
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        grant = {
            "principal_id": "acceptance-http",
            "roles": ["operator"],
            "capabilities": ["assistant:query", "operations:read"],
            "scope": {
                "product_ids": [PRODUCT],
                "selling_location_ids": [STORE],
                "channels": ["store"],
            },
        }
        path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "policy_id": "acceptance-http",
                    "grants": [grant],
                    "credentials": [
                        {
                            "principal_id": "acceptance-http",
                            "token_sha256": token_fingerprint(token),
                            "not_before": (now - timedelta(seconds=1)).isoformat(),
                            "expires_at": (now + timedelta(hours=1)).isoformat(),
                            "revoked": False,
                        }
                    ],
                }
            )
        )
        path.chmod(0o600)
        process = start(path, token)
        engine = database_engine(settings())
        try:
            body: dict[str, Any] = {
                "question": "Scripted acceptance query.",
                "scope": {
                    "product_ids": [PRODUCT],
                    "store_ids": [STORE],
                    "from": now.date().isoformat(),
                    "to": now.date().isoformat(),
                },
                "conversation_id": None,
            }
            code, answer = request("/api/v1/assistant/queries", token, body)
            require(code == 200, "assistant_http_query_failed")
            trace_id = UUID(answer["trace_id"])
            async with engine.connect() as connection:
                counts = (
                    await connection.execute(
                        text(
                            "SELECT (SELECT count(*) FROM ai.assistant_answers WHERE trace_id=:id), (SELECT count(*) FROM ai.assistant_suggestions WHERE trace_id=:id)"
                        ),
                        {"id": trace_id},
                    )
                ).one()
                require(tuple(counts) == (1, 1), "assistant_atomic_outcome_missing")
            code, trace = request("/api/v1/assistant/runs/" + str(trace_id), token)
            require(
                code == 200
                and trace["answer_id"] == answer["answer_id"]
                and body["question"] not in json.dumps(trace),
                "assistant_safe_trace_failed",
            )
            process.kill()
            process.wait(timeout=5)
            process = start(path, token)
            require(
                request("/api/v1/assistant/runs/" + str(trace_id), token) == (200, trace),
                "assistant_restart_trace_changed",
            )
            # Duplicate persisted identities are rejected by PostgreSQL.
            async with engine.connect() as connection:
                item: dict[str, Any] = (
                    await connection.execute(
                        text("SELECT record FROM ai.assistant_suggestions WHERE trace_id=:id"),
                        {"id": trace_id},
                    )
                ).scalar_one()
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO ai.assistant_suggestions(recommendation_id,answer_id,trace_id,expires_at,record) SELECT recommendation_id,answer_id,trace_id,expires_at,record FROM ai.assistant_suggestions WHERE trace_id=:id"
                        ),
                        {"id": trace_id},
                    )
            except IntegrityError:
                pass
            else:
                raise RuntimeError("assistant_duplicate_identity_accepted")
            require(
                item["requires_human_review"] is True and item["status"] == "proposed",
                "assistant_review_metadata_missing",
            )
            async with engine.begin() as connection:
                await connection.execute(
                    text("""CREATE FUNCTION ai.assistant_acceptance_fault() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'scripted_write_failure' USING ERRCODE='23514'; END; $$""")
                )
                await connection.execute(
                    text("""CREATE TRIGGER assistant_acceptance_fault BEFORE INSERT ON ai.assistant_suggestions
                    FOR EACH ROW EXECUTE FUNCTION ai.assistant_acceptance_fault()""")
                )
            try:
                require(
                    request("/api/v1/assistant/queries", token, body)[0] == 503,
                    "assistant_storage_fault_not_sanitized",
                )
                async with engine.connect() as connection:
                    counts = (
                        await connection.execute(
                            text("""SELECT
                        (SELECT count(*) FROM ai.assistant_answers a JOIN ai.assistant_runs r USING(trace_id) WHERE r.owner_id='acceptance-http'),
                        (SELECT count(*) FROM ai.assistant_suggestions s JOIN ai.assistant_runs r USING(trace_id) WHERE r.owner_id='acceptance-http')""")
                        )
                    ).one()
                    require(tuple(counts) == (1, 1), "assistant_partial_answer_committed")
            finally:
                async with engine.begin() as connection:
                    await connection.execute(
                        text("DROP TRIGGER assistant_acceptance_fault ON ai.assistant_suggestions")
                    )
                    await connection.execute(text("DROP FUNCTION ai.assistant_acceptance_fault()"))
            return {
                "result": "passed",
                "trace_id": str(trace_id),
                "answer_id": answer["answer_id"],
                "recommendation_id": item["recommendation_id"],
                "checks": [
                    "real_http_postgres_answer_trace_suggestion_atomic_binding",
                    "sigkill_api_restart_identical_safe_trace",
                    "unique_recommendation_identity_human_review_only",
                    "storage_failure_rolls_back_answer_and_suggestions",
                ],
                "fixture_only": True,
            }
        finally:
            process.kill()
            process.wait(timeout=5)
            await engine.dispose()


async def verify() -> dict[str, Any]:
    result = await database_checks()
    result["http"] = await http_checks()
    return result


async def admit_process(value: RunLease) -> RunLease | AssistantError:
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        "--admission-once",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, _ = await asyncio.wait_for(
        child.communicate(value.model_dump_json().encode()), timeout=25
    )
    require(child.returncode == 0, "admission_process_failed")
    decoded = json.loads(output)
    return (
        AssistantError(decoded["status"])
        if "status" in decoded
        else RunLease.model_validate_json(json.dumps(decoded["lease"]))
    )


async def admit_once(value: RunLease) -> dict[str, Any]:
    engine = database_engine(settings())
    try:
        try:
            result = await PostgresAssistantStore(engine, "test").admit(
                value, AdmissionPolicy(), 45.0
            )
            return {"lease": result.model_dump(mode="json")}
        except AssistantError as exc:
            return {"status": exc.status}
    finally:
        await engine.dispose()


async def retention(expected: dict[str, Any]) -> dict[str, Any]:
    engine = database_engine(settings())
    try:
        trace = UUID(expected["trace_id"])
        async with engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        text("""SELECT status,
                (SELECT answer_id FROM ai.assistant_answers WHERE trace_id=:id) AS answer_id,
                (SELECT recommendation_id FROM ai.assistant_suggestions WHERE trace_id=:id) AS recommendation_id
                FROM ai.assistant_runs WHERE trace_id=:id"""),
                        {"id": trace},
                    )
                )
                .mappings()
                .one()
            )
            require(
                row["status"] == "succeeded"
                and str(row["answer_id"]) == expected["answer_id"]
                and str(row["recommendation_id"]) == expected["recommendation_id"],
                "assistant_restart_outcomes_lost",
            )
        return {"result": "passed", "trace_id": str(trace)}
    finally:
        await engine.dispose()


def compose_check(
    command: Callable[..., str], *, expected: dict[str, Any] | None = None
) -> dict[str, Any]:
    output = command(
        "run",
        "--rm",
        "-T",
        "--entrypoint",
        "python",
        "-v",
        f"{Path(__file__).resolve().parent}:/opt/retailops-verification:ro",
        "api-migrate",
        "/opt/retailops-verification/verify_assistant.py",
        "--retention-check" if expected else "--database-checks",
        stdin=json.dumps(expected) if expected else None,
    )
    result: dict[str, Any] = json.loads(output)
    require(result.get("result") == "passed", "assistant_compose_checks_failed")
    return result


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--http":
            app = create_app(settings(Path(sys.argv[2])), assistant_backend=ScriptedBackend())
            uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="critical", access_log=False)
        elif sys.argv[1:] == ["--database-checks"]:
            print(json.dumps(asyncio.run(verify())))
        elif sys.argv[1:] == ["--retention-check"]:
            print(json.dumps(asyncio.run(retention(json.loads(sys.stdin.read(16384))))))
        elif sys.argv[1:] == ["--admission-once"]:
            print(
                json.dumps(
                    asyncio.run(admit_once(RunLease.model_validate_json(sys.stdin.read(16384))))
                )
            )
        else:
            raise RuntimeError("unsupported_verification_mode")
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": "assistant_acceptance_failed",
                    "type": type(exc).__name__,
                    "reason": str(exc)
                    if isinstance(exc, RuntimeError) and re.fullmatch(r"[a-z_]{1,100}", str(exc))
                    else "internal_failure",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
