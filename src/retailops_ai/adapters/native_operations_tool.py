"""Read only the Producer's scoped, persisted event processing observations.

This reader deliberately does not translate a last event or a persisted running
flag into Kafka health. Consumer heartbeat and Kafka lag are not observed here.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.adapters.native_read_tools import environment, scoped_actor
from retailops_ai.agent.execution import ToolFailure
from retailops_ai.agent.tools import (
    NativeOperationsEvidence,
    NativeOperationsItem,
    OperationsRequest,
    OperationsResult,
    ToolInput,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.releases import IndexPin

MAX_EVENTS = 10000


class NativeOperationsReader(Protocol):
    environment: Literal["local", "test"]

    def read(self, request: OperationsRequest, actor: Principal) -> NativeOperationsEvidence: ...


class PostgresNativeOperationsReader:
    """Server-owned Producer read connection; no imports of Producer runtime code."""

    def __init__(self, engine: Engine, env: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment(env)

    def read(self, request: OperationsRequest, actor: Principal) -> NativeOperationsEvidence:
        actor = scoped_actor(request, actor, {"operations:read"})
        scope = request.scope
        if scope is None:
            raise ToolFailure("invalid_scope")
        params = dict(
            products=sorted(actor.product_ids),
            stores=sorted(actor.selling_location_ids),
            channel=scope.channel,
            first=request.as_of - timedelta(minutes=15),
            cutoff=request.as_of,
        )
        # Native v1 sales events use payload.product_id/store_id/channel. Other
        # event types lacking all three identifiers cannot support this grain.
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                now: datetime = conn.execute(text("SELECT clock_timestamp()")).scalar_one()
                rows = (
                    conn.execute(
                        text("""
                    SELECT event_id::text event_id, payload->>'product_id' product_id,
                      payload->>'store_id' selling_location_id, payload->>'channel' channel,
                      status, ingested_at, processed_at, updated_at
                    FROM realtime_event_log
                    WHERE schema_version='1.0' AND event_type IN ('sale_completed','return_completed')
                      AND payload->>'product_id'=ANY(:products)
                      AND payload->>'store_id'=ANY(:stores) AND payload->>'channel'=:channel
                      AND ingested_at BETWEEN :first AND :cutoff
                    ORDER BY ingested_at,event_id LIMIT 10001
                """),
                        params,
                    )
                    .mappings()
                    .all()
                )
        if len(rows) > MAX_EVENTS:
            raise ToolFailure("budget_exceeded")
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        ids: set[str] = set()
        safe_rows = []
        for row in rows:
            if (
                row["event_id"] in ids
                or len(row["event_id"]) > 128
                or row["product_id"] not in actor.product_ids
                or row["selling_location_id"] not in actor.selling_location_ids
                or row["channel"] != scope.channel
                or not params["first"] <= row["ingested_at"] <= request.as_of
                or row["updated_at"] < row["ingested_at"]
                or row["updated_at"] > now
                or row["status"]
                not in {"received", "processed", "failed_dead_lettered", "ignored_duplicate"}
                or (row["status"] == "processed" and row["processed_at"] is None)
                or (
                    row["processed_at"] is not None
                    and not row["ingested_at"] <= row["processed_at"] <= row["updated_at"]
                )
            ):
                raise ValueError("native_operations_record_invalid")
            ids.add(row["event_id"])
            groups.setdefault(
                (row["product_id"], row["selling_location_id"], row["channel"]), []
            ).append(dict(row))
            safe_rows.append(
                {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in row.items()}
            )
        points = []
        for product in sorted(actor.product_ids):
            for location in sorted(actor.selling_location_ids):
                selected = groups.get((product, location, scope.channel), [])
                causal = [r for r in selected if r["updated_at"] <= request.as_of]
                processed = [r for r in causal if r["status"] == "processed"]
                points.append(
                    NativeOperationsItem(
                        product_id=product,
                        selling_location_id=location,
                        channel=scope.channel,
                        received=sum(r["status"] == "received" for r in causal),
                        processed=len(processed),
                        failed_dead_lettered=sum(
                            r["status"] == "failed_dead_lettered" for r in causal
                        ),
                        ignored_duplicate=sum(r["status"] == "ignored_duplicate" for r in causal),
                        newer_state_not_evaluable=len(selected) - len(causal),
                        latest_ingested_at=max((r["ingested_at"] for r in causal), default=None),
                        latest_processed_at=max(
                            (r["processed_at"] for r in processed), default=None
                        ),
                        max_processing_latency_seconds=max(
                            (
                                (r["processed_at"] - r["ingested_at"]).total_seconds()
                                for r in processed
                            ),
                            default=None,
                        ),
                    )
                )
        return NativeOperationsEvidence(
            environment=self.environment,
            request=request,
            observed_at=now,
            source_view_sha256=canonical_sha256(
                {
                    "request": request.model_dump(mode="json"),
                    "principal": actor.principal_id,
                    "rows": safe_rows,
                }
            ),
            points=tuple(points),
        )


class NativeOperationsTool:
    source_kind: Literal["runtime"] = "runtime"

    def __init__(self, reader: NativeOperationsReader, env: Literal["local", "test"]) -> None:
        self.environment = environment(env)
        if reader.environment != env:
            raise ValueError("native_operations_environment_invalid")
        self.reader = reader

    async def execute(
        self, request: ToolInput, principal: Principal, pin: IndexPin | None
    ) -> OperationsResult:
        if not isinstance(request, OperationsRequest):
            raise ToolFailure("invalid_scope")
        request = OperationsRequest.model_validate_json(request.model_dump_json())
        actor = scoped_actor(request, principal, {"operations:read"})
        if self.reader.environment != self.environment:
            raise ToolFailure("unavailable")
        try:
            proof = await asyncio.to_thread(self.reader.read, request, actor)
            proof = NativeOperationsEvidence.model_validate_json(proof.model_dump_json())
            if proof.environment != self.environment or proof.request != request:
                raise ToolFailure("unavailable")
            return OperationsResult(
                schema_version="1.0",
                contract_type="agent_tool_result",
                tool=request.tool,
                status="ok" if proof.complete else "no_data",
                as_of=request.as_of,
                freshness_status="current" if proof.complete else "missing",
                source_ref=proof.view_ref,
                items=[*proof.result_items()],
                error=None,
                source_kind="runtime",
                native_view=proof,
            )
        except ToolFailure:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ToolFailure("unavailable") from None
