"""Durable physical stockout queue using AI05 lease, retry and atomic completion semantics."""

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import QueuePolicy
from retailops_ai.forecast_jobs.queue import LeaseLost, clock
from retailops_ai.stockout_jobs.batch import StockoutOutput, check_inputs, verify_output
from retailops_ai.stockout_jobs.contracts import StockoutInputRef, StockoutRequest, StockoutRun
from retailops_ai.stockout_jobs.input_store import (
    StockoutError,
    authorize_scope,
    checked,
    load_inputs,
)
from retailops_ai.stockout_lifecycle.contract import MODEL, TEST_MODEL, StockoutModelRelease
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

QUEUE_LOCK = 508081


@dataclass(frozen=True)
class StockoutClaim:
    run: StockoutRun
    profile: PreparedStockoutInputs
    release: StockoutModelRelease
    token: str = field(repr=False)
    deadline: datetime


def record(value: Any) -> StockoutRun:
    return StockoutRun.model_validate_json(json.dumps(value))


def authorize_read(run: StockoutRun, principal: Principal) -> None:
    authorize_scope(
        run.input_ref.request.scope,
        principal,
        reading=True,
        owned=principal.principal_id == run.requested_by,
    )


class PostgresStockoutQueue:
    def __init__(
        self,
        engine: Engine,
        environment: Literal["local", "test"],
        policy: QueuePolicy | None = None,
        *,
        mechanics: bool = False,
    ) -> None:
        if environment not in {"local", "test"} or (mechanics and environment != "test"):
            raise ValueError("stockout_queue_environment")
        self.engine, self.environment = engine, environment
        self.policy = QueuePolicy.model_validate_json((policy or QueuePolicy()).model_dump_json())
        self.model = TEST_MODEL if mechanics else MODEL

    def _guard(
        self, connection: Connection, release: StockoutModelRelease | None = None
    ) -> StockoutModelRelease:
        key = int.from_bytes(hashlib.sha256(self.model.encode()).digest()[:8], signed=True)
        if not connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), dict(key=key)):
            raise StockoutError(409, "stockout-model-decision-incomplete")
        if connection.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM ai.stockout_model_decisions d WHERE model_name=:model AND NOT EXISTS(SELECT 1 FROM ai.stockout_model_steps s WHERE s.decision_id=d.decision_id AND phase='completed'))"
            ),
            dict(model=self.model),
        ):
            raise StockoutError(409, "stockout-model-decision-incomplete")
        if release is None:
            raw = connection.scalar(
                text(
                    "SELECT r.release FROM ai.stockout_model_heads h JOIN ai.stockout_model_releases r USING(release_id) WHERE h.model_name=:model"
                ),
                dict(model=self.model),
            )
            if raw is None:
                raise StockoutError(409, "stockout-model-not-approved")
            release = StockoutModelRelease.model_validate_json(json.dumps(raw))
        now = clock(connection)
        approval = release.binding.approval
        if (
            release.binding.model_name != self.model
            or not approval.reviewed_at <= now < approval.qualification.valid_until
            or connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM ai.stockout_model_decisions d JOIN ai.stockout_model_steps s USING(decision_id) WHERE d.model_name=:model AND d.record->'request'->>'model_version'=:version AND d.record->'request'->>'action'='reject' AND s.phase='completed')"
                ),
                dict(model=self.model, version=release.binding.model_version),
            )
        ):
            raise StockoutError(409, "stockout-model-not-approved")
        return release

    def _profile(self, connection: Connection, profile_id: str) -> PreparedStockoutInputs:
        return load_inputs(connection, self.environment, profile_id)

    def _release(self, connection: Connection, release_id: str) -> StockoutModelRelease:
        raw = connection.scalar(
            text("SELECT release FROM ai.stockout_model_releases WHERE release_id=:id"),
            dict(id=release_id),
        )
        if raw is None:
            raise ValueError("stockout_queue_missing_release")
        return StockoutModelRelease.model_validate_json(json.dumps(raw))

    def submit(self, request: StockoutRequest, principal: Principal, key: str) -> StockoutRun:
        if "pipeline" not in principal.roles or "stockout:run" not in principal.capabilities:
            raise StockoutError(403, "stockout-run-denied")
        request = StockoutRequest.model_validate_json(request.model_dump_json())
        authorize_scope(request.scope, principal)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) is None:
            raise StockoutError(422, "stockout-invalid-idempotency-key")
        key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
        with self.engine.begin() as connection:
            checked(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), dict(key=QUEUE_LOCK))
            old = connection.scalar(
                text(
                    "SELECT record FROM ai.stockout_batch_runs WHERE environment=:env AND principal_id=:principal AND key_hash=:key"
                ),
                dict(env=self.environment, principal=principal.principal_id, key=key_hash),
            )
            if old is not None:
                run = record(old)
                if (
                    run.input_ref.request_sha256 != request.request_hash()
                    or run.release.binding.model_name != self.model
                ):
                    raise StockoutError(409, "stockout-idempotency-conflict")
                authorize_read(run, principal)
                return run
            release = self._guard(connection)
            profile = self._profile(connection, request.profile_id)
            now = clock(connection)
            if request.as_of > now:
                raise StockoutError(422, "stockout-input-from-future")
            if request.as_of != profile.as_of:
                raise StockoutError(422, "stockout-input-origin-mismatch")
            if set(request.scope.product_ids) != set(profile.scope.product_ids) or set(
                request.scope.stock_location_ids
            ) != set(profile.scope.stock_location_ids):
                raise StockoutError(422, "stockout-input-coverage-mismatch")
            if request.as_of < release.binding.approval.qualification.recipe.pin.selection_known_at:
                raise StockoutError(422, "stockout-input-runtime-incompatible")
            counts = connection.execute(
                text(
                    "SELECT count(*) AS total,count(*) FILTER(WHERE principal_id=:principal) AS owned FROM ai.stockout_batch_runs WHERE environment=:env AND status IN ('queued','running')"
                ),
                dict(env=self.environment, principal=principal.principal_id),
            ).one()
            if (
                counts.total >= self.policy.max_pending
                or counts.owned >= self.policy.max_pending_per_principal
            ):
                raise StockoutError(429, "stockout-queue-full")
            run = StockoutRun(
                run_id="run-" + secrets.token_hex(16),
                status="queued",
                attempt=1,
                requested_at=now,
                requested_by=principal.principal_id,
                input_ref=StockoutInputRef(
                    request=request, request_sha256=request.request_hash(), lineage=profile.lineage
                ),
                release=release,
                environment=self.environment,
                policy=self.policy,
            )
            connection.execute(
                text(
                    "INSERT INTO ai.stockout_batch_runs(run_id,environment,principal_id,key_hash,request_hash,profile_id,release_id,record,available_at,run_deadline) VALUES (:id,:env,:principal,:key,:hash,:profile,:release,CAST(:record AS jsonb),:now,:deadline)"
                ),
                dict(
                    id=run.run_id,
                    env=self.environment,
                    principal=principal.principal_id,
                    key=key_hash,
                    hash=request.request_hash(),
                    profile=profile.inputs_id,
                    release=release.release_id,
                    record=run.model_dump_json(),
                    now=now,
                    deadline=now + timedelta(seconds=run.policy.run_timeout_seconds),
                ),
            )
            return run

    def get(self, run_id: str, principal: Principal) -> StockoutRun:
        with self.engine.begin() as connection:
            checked(connection)
            raw = connection.scalar(
                text(
                    "SELECT record FROM ai.stockout_batch_runs WHERE environment=:env AND run_id=:id"
                ),
                dict(env=self.environment, id=run_id),
            )
            if raw is None:
                raise StockoutError(404, "stockout-run-not-found")
            run = record(raw)
            if run.release.binding.model_name != self.model:
                raise StockoutError(404, "stockout-run-not-found")
            authorize_read(run, principal)
            return run

    def attempts(self, run_id: str, principal: Principal) -> list[StockoutRun]:
        self.get(run_id, principal)
        with self.engine.begin() as connection:
            checked(connection)
            return [
                record(raw)
                for raw in connection.scalars(
                    text(
                        "SELECT record FROM ai.stockout_batch_attempts WHERE run_id=:id ORDER BY attempt"
                    ),
                    dict(id=run_id),
                )
            ]

    def _write(
        self,
        connection: Connection,
        run: StockoutRun,
        *,
        token: str | None = None,
        lease: datetime | None = None,
        deadline: datetime | None = None,
        available: datetime | None = None,
    ) -> None:
        connection.execute(
            text("""UPDATE ai.stockout_batch_runs SET record=CAST(:record AS jsonb),lease_token=CAST(:token AS uuid),
          lease_expires=:lease,attempt_deadline=:deadline,available_at=coalesce(:available,available_at) WHERE run_id=:id"""),
            {
                "record": run.model_dump_json(),
                "token": token,
                "lease": lease,
                "deadline": deadline,
                "available": available,
                "id": run.run_id,
            },
        )

    def _close(
        self,
        connection: Connection,
        run: StockoutRun,
        now: datetime,
        status: Literal["failed", "cancelled"],
        reason: str,
        retryable: bool,
    ) -> StockoutRun:
        raw = run.model_dump(mode="json")
        raw.update(
            status=status,
            completed_at=now.isoformat(),
            error={
                "code": "cancelled" if status == "cancelled" else "execution_failed",
                "retryable": retryable,
            },
        )
        closed = record(raw)
        transition(run, closed)
        self._write(connection, closed)
        self._history(connection, closed, reason)
        return closed

    def _history(self, connection: Connection, run: StockoutRun, reason: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            raise ValueError("unsafe_attempt_reason")
        connection.execute(
            text(
                "INSERT INTO ai.stockout_batch_attempts(run_id,attempt,reason,record) VALUES (:id,:attempt,:reason,CAST(:record AS jsonb))"
            ),
            {
                "id": run.run_id,
                "attempt": run.attempt,
                "reason": reason,
                "record": run.model_dump_json(),
            },
        )

    def _requeue(self, connection: Connection, run: StockoutRun, now: datetime) -> None:
        raw = run.model_dump(mode="json")
        raw.update(
            status="queued",
            attempt=run.attempt + 1,
            started_at=None,
            completed_at=None,
            output_id=None,
            error=None,
        )
        self._write(
            connection,
            record(raw),
            available=now + timedelta(seconds=run.policy.retry_backoff_seconds),
        )

    def _leased(self, connection: Connection, claim: StockoutClaim) -> tuple[Any, datetime]:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM ai.stockout_batch_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
                ),
                {"id": claim.run.run_id, "env": self.environment},
            )
            .mappings()
            .first()
        )
        now = clock(connection)
        if (
            row is None
            or row["status"] != "running"
            or str(row["lease_token"]) != claim.token
            or row["lease_expires"] <= now
            or row["attempt_deadline"] <= now
            or row["run_deadline"] <= now
            or record(row["record"]) != claim.run
            or claim.run.release.binding.model_name != self.model
        ):
            raise LeaseLost("batch_lease_lost")
        return row, now

    def heartbeat(self, claim: StockoutClaim) -> None:
        with self.engine.begin() as connection:
            checked(connection)
            row, now = self._leased(connection, claim)
            self._guard(connection, claim.release)
            self._write(
                connection,
                claim.run,
                token=claim.token,
                lease=min(
                    now + timedelta(seconds=claim.run.policy.lease_seconds), row["attempt_deadline"]
                ),
                deadline=row["attempt_deadline"],
            )

    def execution_budget(self, claim: StockoutClaim) -> float:
        with self.engine.begin() as connection:
            checked(connection)
            row, now = self._leased(connection, claim)
            return float(min(120.0, (row["attempt_deadline"] - now).total_seconds()))

    def fail(self, claim: StockoutClaim, *, reason: str, retryable: bool) -> None:
        with self.engine.begin() as connection:
            checked(connection)
            row, now = self._leased(connection, claim)
            retryable = (
                retryable
                and claim.run.attempt < claim.run.policy.max_attempts
                and now + timedelta(seconds=claim.run.policy.retry_backoff_seconds)
                < row["run_deadline"]
            )
            closed = self._close(connection, claim.run, now, "failed", reason, retryable)
            if retryable:
                self._requeue(connection, closed, now)

    def cancel(self, run_id: str) -> None:
        with self.engine.begin() as connection:
            checked(connection)
            row = connection.execute(
                text(
                    "SELECT record FROM ai.stockout_batch_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
                ),
                {"id": run_id, "env": self.environment},
            ).first()
            if row is None:
                raise StockoutError(404, "stockout-run-not-found")
            now = clock(connection)
            run = record(row[0])
            if run.release.binding.model_name != self.model:
                raise StockoutError(404, "stockout-run-not-found")
            if run.status not in {"queued", "running"}:
                raise StockoutError(409, "stockout-run-not-active")
            self._close(connection, run, now, "cancelled", "internal_cancel", False)

    def claim(self, *, release_id: str) -> StockoutClaim | None:
        with self.engine.begin() as connection:
            checked(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), dict(key=QUEUE_LOCK))
            now = clock(connection)
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.stockout_batch_runs WHERE environment=:env AND record->'release'->'binding'->>'model_name'=:model AND ((status='queued' AND run_deadline<=:now) OR (status='running' AND (lease_expires<=:now OR attempt_deadline<=:now))) ORDER BY run_id FOR UPDATE"
                    ),
                    dict(env=self.environment, model=self.model, now=now),
                )
                .mappings()
                .all()
            )
            for row in rows:
                now = clock(connection)
                run = record(row["record"])
                if run.status == "queued":
                    self._close(connection, run, now, "cancelled", "queue_deadline", False)
                else:
                    retryable = (
                        run.attempt < run.policy.max_attempts
                        and now + timedelta(seconds=run.policy.retry_backoff_seconds)
                        < row["run_deadline"]
                    )
                    closed = self._close(connection, run, now, "failed", "lease_expired", retryable)
                    if retryable:
                        self._requeue(connection, closed, now)
            chosen = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.stockout_batch_runs WHERE environment=:env AND release_id=:release AND record->'release'->'binding'->>'model_name'=:model AND status='queued' AND available_at<=:now AND run_deadline>:now ORDER BY available_at,run_id LIMIT 1 FOR UPDATE SKIP LOCKED"
                    ),
                    dict(
                        env=self.environment,
                        release=release_id,
                        model=self.model,
                        now=clock(connection),
                    ),
                )
                .mappings()
                .first()
            )
            if chosen is None:
                return None
            run = record(chosen["record"])
            release = self._release(connection, run.release.release_id)
            try:
                self._guard(connection, release)
            except StockoutError as error:
                if error.code == "stockout-model-not-approved":
                    self._close(
                        connection, run, clock(connection), "cancelled", "model_unavailable", False
                    )
                return None
            profile = self._profile(connection, run.input_ref.request.profile_id)
            if release != run.release:
                raise ValueError("stockout_claim_release_pin")
            check_inputs(run, profile, release)
            now = clock(connection)
            if now >= chosen["run_deadline"]:
                self._close(connection, run, now, "cancelled", "queue_deadline", False)
                return None
            raw = run.model_dump(mode="json")
            raw.update(status="running", started_at=now.isoformat())
            running = record(raw)
            transition(run, running)
            token = str(uuid4())
            deadline = min(
                now + timedelta(seconds=run.policy.attempt_timeout_seconds), chosen["run_deadline"]
            )
            self._write(
                connection,
                running,
                token=token,
                lease=min(now + timedelta(seconds=run.policy.lease_seconds), deadline),
                deadline=deadline,
            )
            return StockoutClaim(running, profile, release, token, deadline)

    def complete(
        self,
        claim: StockoutClaim,
        output: StockoutOutput,
        *,
        tick: Callable[[], None] | None = None,
    ) -> None:
        verify_output(claim.run, claim.profile, claim.release, output, tick=tick)
        with self.engine.begin() as connection:
            checked(connection)
            self._leased(connection, claim)
            self._guard(connection, claim.release)
            connection.execute(
                text(
                    "INSERT INTO ai.stockout_batch_outputs(output_id,run_id,output) VALUES (:id,:run,CAST(:receipt AS jsonb))"
                ),
                dict(id=output.output_id, run=claim.run.run_id, receipt=output.model_dump_json()),
            )
            _, now = self._leased(connection, claim)
            if output.generated_at > now:
                raise ValueError("stockout_batch_receipt_from_future")
            raw = claim.run.model_dump(mode="json")
            raw.update(status="succeeded", completed_at=now.isoformat(), output_id=output.output_id)
            done = record(raw)
            transition(claim.run, done)
            self._write(connection, done)
            self._history(connection, done, "stockout_computation_completed")

    def output(self, run_id: str, principal: Principal) -> StockoutOutput:
        run = self.get(run_id, principal)
        if run.status != "succeeded" or run.output_id is None:
            raise ValueError("stockout_complete_receipt_required")
        with self.engine.begin() as connection:
            checked(connection)
            raw = connection.scalar(
                text("SELECT output FROM ai.stockout_batch_outputs WHERE run_id=:id"),
                dict(id=run_id),
            )
            output = StockoutOutput.model_validate_json(json.dumps(raw))
            replay = record(
                {
                    **run.model_dump(mode="json"),
                    "status": "running",
                    "completed_at": None,
                    "output_id": None,
                }
            )
            verify_output(
                replay,
                self._profile(connection, run.input_ref.request.profile_id),
                self._release(connection, run.release.release_id),
                output,
            )
            if output.output_id != run.output_id:
                raise ValueError("stockout_receipt_pointer_mismatch")
            return output


def transition(before: StockoutRun, after: StockoutRun) -> None:
    pins = (
        "run_id",
        "version",
        "run_type",
        "attempt",
        "requested_at",
        "requested_by",
        "input_ref",
        "release",
        "environment",
        "policy",
    )
    if any(getattr(before, key) != getattr(after, key) for key in pins):
        raise ValueError("stockout_batch_transition_changed_pin")
    allowed = {"queued": {"running", "cancelled"}, "running": {"failed", "cancelled", "succeeded"}}
    if after.status not in allowed.get(before.status, set()) or (
        before.started_at is not None and before.started_at != after.started_at
    ):
        raise ValueError("stockout_batch_illegal_transition")
