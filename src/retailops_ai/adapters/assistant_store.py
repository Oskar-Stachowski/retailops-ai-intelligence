"""AI-owned PostgreSQL transactions; shared admission, expiring leases and fenced completion."""

import json
from datetime import timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from retailops_ai.assistant.contracts import AssistantAnswer, AssistantRun, PersistedSuggestion
from retailops_ai.assistant.service import AdmissionPolicy, AssistantError, RunLease, readable
from retailops_ai.domain.access import Principal
from retailops_ai.security.models import KnowledgeResourceScope

ADMISSION_LOCK = 384790017


async def boundary(connection: AsyncConnection) -> None:
    await connection.execute(text("SET LOCAL statement_timeout='2s'"))
    await connection.execute(text("SET LOCAL lock_timeout='1s'"))


async def expire(connection: AsyncConnection) -> None:
    await connection.execute(
        text("DELETE FROM ai.assistant_runs WHERE retain_until <= clock_timestamp()")
    )
    await connection.execute(
        text("""UPDATE ai.assistant_runs SET status='failed', claim=NULL,
        record=jsonb_set(jsonb_set(jsonb_set(record,'{status}','"failed"'),
            '{completed_at}',to_jsonb(clock_timestamp())), '{error_code}','"deadline_exceeded"')
        WHERE status='running' AND lease_until <= clock_timestamp()""")
    )


def run_record(value: object) -> AssistantRun:
    return AssistantRun.model_validate_json(json.dumps(value))


class PostgresAssistantStore:
    def __init__(self, engine: AsyncEngine, environment: str) -> None:
        if environment not in {"local", "test"}:
            raise ValueError("assistant_environment_invalid")
        self.engine, self.environment = engine, environment

    async def admit(
        self, lease: RunLease, policy: AdmissionPolicy, deadline_seconds: float
    ) -> RunLease:
        lease = RunLease.model_validate_json(lease.model_dump_json())
        if not 0 < deadline_seconds <= 45:
            raise ValueError("assistant_deadline_outside_profile")
        async with self.engine.begin() as connection:
            await boundary(connection)
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMISSION_LOCK}
            )
            await expire(connection)
            policy_json = policy.model_dump_json()
            await connection.execute(
                text("""INSERT INTO ai.assistant_admission_policy(environment,policy)
                VALUES (:env,CAST(:policy AS jsonb)) ON CONFLICT DO NOTHING"""),
                {"env": self.environment, "policy": policy_json},
            )
            recorded = await connection.scalar(
                text("SELECT policy FROM ai.assistant_admission_policy WHERE environment=:env"),
                {"env": self.environment},
            )
            if AdmissionPolicy.model_validate_json(json.dumps(recorded)) != policy:
                raise AssistantError(503)
            stats = (
                (
                    await connection.execute(
                        text("""SELECT count(*) AS retained,
                count(*) FILTER(WHERE status='running') AS active,
                count(*) FILTER(WHERE status='running' AND owner_id=:owner) AS own_active,
                count(*) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window)) AS recent,
                count(*) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window) AND owner_id=:owner) AS own_recent,
                COALESCE(sum(reserved_tokens) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window)),0) AS tokens,
                COALESCE(sum(reserved_tokens) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window) AND owner_id=:owner),0) AS own_tokens,
                COALESCE(sum(reserved_cost) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window)),0) AS cost,
                COALESCE(sum(reserved_cost) FILTER(WHERE requested_at > clock_timestamp()-make_interval(secs=>:window) AND owner_id=:owner),0) AS own_cost
                FROM ai.assistant_runs WHERE environment=:env"""),
                        {
                            "env": self.environment,
                            "owner": lease.owner_id,
                            "window": policy.window_seconds,
                        },
                    )
                )
                .mappings()
                .one()
            )
            from decimal import Decimal

            if (
                stats["retained"] >= policy.capacity
                or stats["active"] >= policy.parallel_total
                or stats["own_active"] >= policy.parallel_per_principal
                or stats["recent"] >= policy.runs_total_window
                or stats["own_recent"] >= policy.runs_per_principal_window
                or stats["tokens"] + lease.reserved_tokens > policy.tokens_total_window
                or stats["own_tokens"] + lease.reserved_tokens > policy.tokens_per_principal_window
                or stats["cost"] + Decimal(lease.reserved_cost) > Decimal(policy.cost_total_window)
                or stats["own_cost"] + Decimal(lease.reserved_cost)
                > Decimal(policy.cost_per_principal_window)
            ):
                raise AssistantError(429)
            now = await connection.scalar(text("SELECT clock_timestamp()"))
            run = AssistantRun(**{**lease.run.model_dump(), "requested_at": now})
            lease = RunLease(**{**lease.model_dump(), "run": run})
            await connection.execute(
                text("""INSERT INTO ai.assistant_runs
                (trace_id,environment,owner_id,scope,request_sha256,claim,requested_at,lease_until,retain_until,
                 reserved_tokens,reserved_cost,status,record,access_context,correlation_id)
                VALUES (:id,:env,:owner,CAST(:scope AS jsonb),:hash,:claim,:now,:until,:retain,:tokens,:cost,'running',CAST(:record AS jsonb),CAST(:access AS jsonb),:correlation)"""),
                {
                    "id": run.trace_id,
                    "env": self.environment,
                    "owner": lease.owner_id,
                    "scope": lease.scope_json,
                    "hash": lease.request_sha256,
                    "claim": lease.claim,
                    "now": now,
                    "until": now + timedelta(seconds=deadline_seconds),
                    "retain": now + timedelta(seconds=policy.retention_seconds),
                    "tokens": lease.reserved_tokens,
                    "cost": lease.reserved_cost,
                    "record": run.model_dump_json(),
                    "correlation": lease.correlation_id,
                    "access": json.dumps(
                        {
                            "required_capabilities": lease.required_capabilities,
                            "knowledge_scope": lease.knowledge_scope.model_dump(mode="json")
                            if lease.knowledge_scope
                            else None,
                        }
                    ),
                },
            )
        return lease

    async def finish(
        self,
        lease: RunLease,
        run: AssistantRun,
        answer: AssistantAnswer | None,
        suggestions: list[PersistedSuggestion],
    ) -> None:
        # Re-validate detached records and all cross-record identities before entering SQL.
        run = AssistantRun.model_validate_json(run.model_dump_json())
        if (
            run.trace_id != lease.run.trace_id
            or run.requested_at != lease.run.requested_at
            or run.agent_config_version != lease.run.agent_config_version
            or run.index_id != lease.run.index_id
        ):
            raise ValueError("assistant_run_binding")
        if run.status == "running" or (answer is not None) != (run.status == "succeeded"):
            raise ValueError("assistant_terminal_record_required")
        if answer is None and suggestions:
            raise ValueError("assistant_failed_suggestions")
        if answer is not None:
            answer = AssistantAnswer.model_validate_json(answer.model_dump_json())
            if (
                answer.trace_id != run.trace_id
                or answer.answer_id != run.answer_id
                or answer.outcome != run.outcome
                or answer.agent_config_version != run.agent_config_version
                or answer.index_id != run.index_id
            ):
                raise ValueError("assistant_answer_binding")
            if [a.recommendation_id for a in answer.recommended_actions] != [
                s.recommendation_id for s in suggestions
            ]:
                raise ValueError("assistant_suggestion_binding")
            suggestions = [
                PersistedSuggestion.model_validate_json(item.model_dump_json())
                for item in suggestions
            ]
            for suggestion in suggestions:
                if suggestion.trace_id != run.trace_id or suggestion.answer_id != run.answer_id:
                    raise ValueError("assistant_suggestion_run_binding")
        async with self.engine.begin() as connection:
            await boundary(connection)
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT status,claim,record,lease_until,clock_timestamp() AS now FROM ai.assistant_runs WHERE trace_id=:id AND environment=:env FOR UPDATE"
                        ),
                        {"id": run.trace_id, "env": self.environment},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["status"] != "running"
                or row["claim"] != lease.claim
                or row["lease_until"] <= row["now"]
            ):
                raise AssistantError(504)
            run = AssistantRun(**{**run.model_dump(), "completed_at": row["now"]})
            if any(item.expires_at <= row["now"] for item in suggestions):
                raise AssistantError(502)
            if answer is not None:
                await connection.execute(
                    text(
                        "INSERT INTO ai.assistant_answers(answer_id,trace_id,record) VALUES(:answer,:trace,CAST(:record AS jsonb))"
                    ),
                    {
                        "answer": answer.answer_id,
                        "trace": run.trace_id,
                        "record": answer.model_dump_json(),
                    },
                )
            for suggestion in suggestions:
                await connection.execute(
                    text("""INSERT INTO ai.assistant_suggestions(recommendation_id,answer_id,trace_id,expires_at,record)
                    VALUES(:id,:answer,:trace,:expires,CAST(:record AS jsonb))"""),
                    {
                        "id": suggestion.recommendation_id,
                        "answer": suggestion.answer_id,
                        "trace": run.trace_id,
                        "expires": suggestion.expires_at,
                        "record": suggestion.model_dump_json(),
                    },
                )
            await connection.execute(
                text(
                    "UPDATE ai.assistant_runs SET status=:status,record=CAST(:record AS jsonb),claim=NULL WHERE trace_id=:id"
                ),
                {"id": run.trace_id, "status": run.status, "record": run.model_dump_json()},
            )

    async def get(self, trace_id: UUID, principal: Principal) -> AssistantRun | None:
        async with self.engine.begin() as connection:
            await boundary(connection)
            await expire(connection)
            row = (
                (
                    await connection.execute(
                        text(
                            "SELECT owner_id,scope,record,access_context FROM ai.assistant_runs WHERE trace_id=:id AND environment=:env"
                        ),
                        {"id": trace_id, "env": self.environment},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            access = row["access_context"]
            knowledge = (
                KnowledgeResourceScope.model_validate_json(json.dumps(access["knowledge_scope"]))
                if access["knowledge_scope"]
                else None
            )
            if not readable(
                principal,
                row["owner_id"],
                json.dumps(row["scope"]),
                access["required_capabilities"],
                knowledge,
            ):
                return None
            return run_record(row["record"])
