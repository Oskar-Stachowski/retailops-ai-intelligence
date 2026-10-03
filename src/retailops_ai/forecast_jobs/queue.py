"""Database queue: atomic idempotency, immutable pins, fenced attempts and bounded recovery."""

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import Connection, Engine, text

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.data_contracts.run import RunOutput, transition_run
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import (
    BatchErrorCode,
    BatchInput,
    BatchRequest,
    BatchRun,
    BatchScope,
    MechanicsInput,
    MechanicsOutput,
    MechanicsProfile,
    QueuePolicy,
)
from retailops_ai.forecast_jobs.execution_contracts import RuntimeResult
from retailops_ai.forecast_jobs.inputs import PreparedInputs, scoped_inputs
from retailops_ai.forecast_jobs.publication import publication, receipt, scope_key
from retailops_ai.model_lifecycle.contracts import MODEL, TEST_MODEL, Release

QUEUE_LOCK = 505040


class BatchError(ValueError):
    def __init__(self, status: int, code: BatchErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class LeaseLost(ValueError):
    pass


class BatchAdministration(Protocol):
    def submit(self, request: BatchRequest, principal: Principal, key: str) -> BatchRun: ...
    def get(self, run_id: str, principal: Principal) -> BatchRun: ...
    def attempts(self, run_id: str, principal: Principal) -> list[BatchRun]: ...


@dataclass(frozen=True)
class Claim:
    run: BatchRun
    profile: MechanicsProfile | PreparedInputs
    token: str = field(repr=False)
    release: Release | None = None
    deadline: datetime | None = None


def checked(connection: Connection) -> datetime:
    connection.execute(text("SET LOCAL statement_timeout='10s'"))
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    if connection.scalar(text("SELECT version_num FROM ai.alembic_version")) != EXPECTED_REVISION:
        raise BatchError(503, "database-not-ready")
    return clock(connection)


def clock(connection: Connection) -> datetime:
    value = connection.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime):
        raise ValueError("database_clock_missing")
    return value


def record(value: Any) -> BatchRun:
    return BatchRun.model_validate_json(json.dumps(value))


def scope_for(request: BatchRequest, actor: Principal) -> BatchScope:
    products = set(request.product_ids) or set(actor.product_ids)
    locations = set(request.selling_location_ids) or set(actor.selling_location_ids)
    if (
        not products
        or not locations
        or not products <= actor.product_ids
        or not locations <= actor.selling_location_ids
        or request.channel not in actor.channels
    ):
        raise BatchError(403, "scope-denied")
    if len(products) > 20 or len(locations) > 5:
        raise BatchError(422, "batch-scope-limit")
    return BatchScope(
        product_ids=tuple(sorted(products)),
        selling_location_ids=tuple(sorted(locations)),
        channel=request.channel,
    )


def selected(profile: MechanicsProfile, run: BatchRun) -> tuple[MechanicsInput, ...]:
    scope = run.input_ref.scope
    horizon = max(run.input_ref.request.horizons_days)
    return tuple(
        r
        for r in profile.rows
        if r.product_id in scope.product_ids
        and r.selling_location_id in scope.selling_location_ids
        and r.channel == scope.channel
        and r.horizon_days <= horizon
    )


def authorize_read(run: BatchRun, principal: Principal) -> None:
    scope = run.input_ref.scope
    readable = "forecast:read" in principal.capabilities or (
        "forecast:run" in principal.capabilities and run.requested_by == principal.principal_id
    )
    if (
        not readable
        or not set(scope.product_ids) <= principal.product_ids
        or not set(scope.selling_location_ids) <= principal.selling_location_ids
        or scope.channel not in principal.channels
    ):
        raise BatchError(404, "forecast-run-not-found")


def register_mechanics_profile(
    engine: Engine, profile: MechanicsProfile, *, environment: str
) -> None:
    profile = MechanicsProfile.model_validate_json(profile.model_dump_json())
    if environment != "test":
        raise ValueError("mechanics_profile_requires_test_environment")
    with engine.begin() as connection:
        checked(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK})
        old = connection.scalar(
            text("SELECT profile FROM ai.forecast_batch_profiles WHERE profile_id=:id"),
            {"id": profile.profile_id},
        )
        if old is not None:
            if old != profile.model_dump(mode="json"):
                raise ValueError("batch_profile_conflict")
            return
        connection.execute(
            text(
                "INSERT INTO ai.forecast_batch_profiles(profile_id,profile) VALUES (:id,CAST(:profile AS jsonb))"
            ),
            {"id": profile.profile_id, "profile": profile.model_dump_json()},
        )


class PostgresBatchQueue:
    def __init__(
        self,
        engine: Engine,
        environment: Literal["local", "test"],
        policy: QueuePolicy | None = None,
        *,
        mechanics: bool | None = None,
    ) -> None:
        self.engine, self.environment = engine, environment
        self.policy = policy or QueuePolicy()
        self.mechanics = environment == "test" if mechanics is None else mechanics
        if self.mechanics and environment != "test":
            raise ValueError("mechanics_queue_requires_test_environment")
        self.purpose = "lifecycle_mechanics_only" if self.mechanics else "qualified_forecast"

    def submit(self, request: BatchRequest, principal: Principal, key: str) -> BatchRun:
        if "pipeline" not in principal.roles or "forecast:run" not in principal.capabilities:
            raise BatchError(403, "forecast-run-denied")
        request = BatchRequest.model_validate_json(request.model_dump_json())
        scope = scope_for(request, principal)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) is None:
            raise BatchError(422, "invalid-idempotency-key")
        key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
        request_hash = request.request_hash()
        with self.engine.begin() as connection:
            checked(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK})
            now = clock(connection)
            old = connection.scalar(
                text(
                    "SELECT record FROM ai.forecast_batch_runs WHERE environment=:env AND principal_id=:principal AND key_hash=:key"
                ),
                {"env": self.environment, "principal": principal.principal_id, "key": key_hash},
            )
            if old is not None:
                run = record(old)
                if run.input_ref.request_hash != request_hash or run.purpose != self.purpose:
                    raise BatchError(409, "idempotency-conflict")
                authorize_read(run, principal)
                return run
            if request.as_of > now:
                raise BatchError(422, "input-from-future")
            name = TEST_MODEL if self.mechanics else MODEL
            raw_release = connection.scalar(
                text(
                    "SELECT r.release FROM ai.model_heads h JOIN ai.model_releases r USING(release_id) WHERE h.model_name=:name"
                ),
                {"name": name},
            )
            if raw_release is None:
                raise BatchError(409, "model-not-approved")
            if connection.scalar(
                text(
                    "SELECT count(*) FROM ai.model_decisions d WHERE model_name=:name AND NOT EXISTS(SELECT 1 FROM ai.model_steps s WHERE s.decision_id=d.decision_id AND s.phase='completed')"
                ),
                {"name": name},
            ):
                raise BatchError(409, "model-decision-incomplete")
            release = Release.model_validate_json(json.dumps(raw_release))
            if (
                release.binding.model_name != name
                or release.binding.qualification.purpose != self.purpose
                or any(g.status != "passed" for g in release.binding.qualification.gates.values())
            ):
                raise BatchError(409, "model-not-approved")
            profile: MechanicsProfile | PreparedInputs
            if self.mechanics:
                raw_profile = connection.scalar(
                    text("SELECT profile FROM ai.forecast_batch_profiles WHERE profile_id=:id"),
                    {"id": request.profile_id},
                )
                if raw_profile is None:
                    raise BatchError(422, "input-not-prepared")
                profile = MechanicsProfile.model_validate_json(json.dumps(raw_profile))
                source_id, curated_id, feature_id = (
                    profile.source_dataset_id,
                    profile.curated_dataset_id,
                    profile.feature_set_id,
                )
            else:
                from retailops_ai.forecast_jobs.input_store import registration

                row = (
                    connection.execute(
                        text(
                            "SELECT * FROM ai.forecast_prepared_inputs WHERE environment=:env AND profile_id=:id"
                        ),
                        {"env": self.environment, "id": request.profile_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise BatchError(422, "input-not-prepared")
                profile = registration(row).inputs
                descriptor = profile.feature_manifest.descriptor
                source_id, curated_id, feature_id = (
                    descriptor.parent.source_dataset_id,
                    descriptor.parent.curated_dataset_id,
                    profile.feature_manifest.feature_set_id,
                )
                if (
                    descriptor.code.dependency_lock_sha256
                    != release.binding.qualification.dependency_lock_sha256
                ):
                    raise BatchError(422, "input-runtime-incompatible")
                try:
                    scoped_inputs(profile, scope, max(request.horizons_days))
                except ValueError:
                    raise BatchError(422, "input-coverage-mismatch") from None
            if profile.as_of_time != request.as_of:
                raise BatchError(422, "input-origin-mismatch")
            counts = connection.execute(
                text(
                    "SELECT count(*) AS total, count(*) FILTER(WHERE principal_id=:principal) AS owned FROM ai.forecast_batch_runs WHERE environment=:env AND status IN ('queued','running')"
                ),
                {"env": self.environment, "principal": principal.principal_id},
            ).one()
            if (
                counts.total >= self.policy.max_pending
                or counts.owned >= self.policy.max_pending_per_principal
            ):
                raise BatchError(429, "queue-full")
            run = BatchRun(
                schema_version="1.0",
                contract_type="run",
                run_id="run-" + secrets.token_hex(16),
                status="queued",
                attempt=1,
                requested_at=now,
                requested_by=principal.principal_id,
                started_at=None,
                completed_at=None,
                output_ref=None,
                error=None,
                resolved_model=release.binding,
                release_id=release.release_id,
                image_digest=release.image_digest,
                environment=self.environment,
                purpose=release.binding.qualification.purpose,
                policy=self.policy,
                input_ref=BatchInput(
                    source_dataset_id=source_id,
                    curated_dataset_id=curated_id,
                    feature_set_id=feature_id,
                    as_of_time=profile.as_of_time,
                    profile_id=profile.profile_id,
                    request_hash=request_hash,
                    request=request,
                    scope=scope,
                ),
            )
            rows = (
                selected(profile, run)
                if isinstance(profile, MechanicsProfile)
                else scoped_inputs(profile, scope, max(request.horizons_days)).rows
            )
            expected = {
                (p, loc, h)
                for p in scope.product_ids
                for loc in scope.selling_location_ids
                for h in range(1, max(request.horizons_days) + 1)
            }
            if {(r.product_id, r.selling_location_id, r.horizon_days) for r in rows} != expected:
                raise BatchError(422, "input-coverage-mismatch")
            connection.execute(
                text("""INSERT INTO ai.forecast_batch_runs
             (run_id,environment,principal_id,key_hash,request_hash,profile_id,release_id,record,available_at,run_deadline)
             VALUES (:id,:env,:principal,:key,:hash,:profile,:release,CAST(:record AS jsonb),:now,:deadline)"""),
                {
                    "id": run.run_id,
                    "env": self.environment,
                    "principal": principal.principal_id,
                    "key": key_hash,
                    "hash": request_hash,
                    "profile": profile.profile_id,
                    "release": release.release_id,
                    "record": run.model_dump_json(),
                    "now": now,
                    "deadline": now + timedelta(seconds=self.policy.run_timeout_seconds),
                },
            )
            return run

    def get(self, run_id: str, principal: Principal) -> BatchRun:
        with self.engine.begin() as connection:
            checked(connection)
            raw = connection.scalar(
                text(
                    "SELECT record FROM ai.forecast_batch_runs WHERE environment=:env AND run_id=:id"
                ),
                {"env": self.environment, "id": run_id},
            )
            if raw is None:
                raise BatchError(404, "forecast-run-not-found")
            run = record(raw)
            authorize_read(run, principal)
            return run

    def attempts(self, run_id: str, principal: Principal) -> list[BatchRun]:
        self.get(run_id, principal)
        with self.engine.begin() as connection:
            checked(connection)
            return [
                record(v)
                for v in connection.scalars(
                    text(
                        "SELECT record FROM ai.forecast_batch_attempts WHERE run_id=:id ORDER BY attempt"
                    ),
                    {"id": run_id},
                )
            ]

    def _write(
        self,
        connection: Connection,
        run: BatchRun,
        *,
        token: str | None = None,
        lease: datetime | None = None,
        deadline: datetime | None = None,
        available: datetime | None = None,
    ) -> None:
        connection.execute(
            text("""UPDATE ai.forecast_batch_runs SET record=CAST(:record AS jsonb),lease_token=CAST(:token AS uuid),
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
        run: BatchRun,
        now: datetime,
        status: Literal["failed", "cancelled"],
        reason: str,
        retryable: bool,
    ) -> BatchRun:
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
        transition_run(run, closed)
        self._write(connection, closed)
        self._history(connection, closed, reason)
        return closed

    def _history(self, connection: Connection, run: BatchRun, reason: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason) is None:
            raise ValueError("unsafe_attempt_reason")
        connection.execute(
            text(
                "INSERT INTO ai.forecast_batch_attempts(run_id,attempt,reason,record) VALUES (:id,:attempt,:reason,CAST(:record AS jsonb))"
            ),
            {
                "id": run.run_id,
                "attempt": run.attempt,
                "reason": reason,
                "record": run.model_dump_json(),
            },
        )

    def _requeue(self, connection: Connection, run: BatchRun, now: datetime) -> None:
        raw = run.model_dump(mode="json")
        raw.update(
            status="queued",
            attempt=run.attempt + 1,
            started_at=None,
            completed_at=None,
            output_ref=None,
            error=None,
        )
        self._write(
            connection,
            record(raw),
            available=now + timedelta(seconds=run.policy.retry_backoff_seconds),
        )

    def claim(self) -> Claim | None:
        with self.engine.begin() as connection:
            checked(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK})
            now = clock(connection)
            expired = (
                connection.execute(
                    text("""SELECT * FROM ai.forecast_batch_runs WHERE environment=:env AND record->>'purpose'=:purpose
             AND ((status='queued' AND run_deadline<=:now) OR (status='running' AND (lease_expires<=:now OR attempt_deadline<=:now)))
             ORDER BY run_id FOR UPDATE"""),
                    {"env": self.environment, "now": now, "purpose": self.purpose},
                )
                .mappings()
                .all()
            )
            for row in expired:
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
                    closed = self._close(
                        connection,
                        run,
                        now,
                        "failed",
                        "attempt_timeout" if row["attempt_deadline"] <= now else "lease_expired",
                        retryable,
                    )
                    if retryable:
                        self._requeue(connection, closed, now)
            chosen = (
                connection.execute(
                    text(
                        "SELECT * FROM ai.forecast_batch_runs WHERE environment=:env AND record->>'purpose'=:purpose AND status='queued' AND available_at<=:now AND run_deadline>:now ORDER BY available_at,run_id LIMIT 1 FOR UPDATE SKIP LOCKED"
                    ),
                    {"env": self.environment, "now": now, "purpose": self.purpose},
                )
                .mappings()
                .first()
            )
            if chosen is None:
                return None
            now = clock(connection)
            if chosen["run_deadline"] <= now:
                self._close(
                    connection, record(chosen["record"]), now, "cancelled", "queue_deadline", False
                )
                return None
            run = record(chosen["record"])
            raw = run.model_dump(mode="json")
            raw.update(status="running", started_at=now.isoformat())
            running = record(raw)
            transition_run(run, running)
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
            profile: MechanicsProfile | PreparedInputs
            if self.mechanics:
                profile = MechanicsProfile.model_validate_json(
                    json.dumps(
                        connection.scalar(
                            text(
                                "SELECT profile FROM ai.forecast_batch_profiles WHERE profile_id=:id"
                            ),
                            {"id": run.input_ref.profile_id},
                        )
                    )
                )
            else:
                from retailops_ai.forecast_jobs.input_store import registration

                stored = (
                    connection.execute(
                        text(
                            "SELECT * FROM ai.forecast_prepared_inputs WHERE environment=:env AND profile_id=:id"
                        ),
                        {"env": self.environment, "id": run.input_ref.profile_id},
                    )
                    .mappings()
                    .one()
                )
                profile = registration(stored).inputs
            release = Release.model_validate_json(
                json.dumps(
                    connection.scalar(
                        text("SELECT release FROM ai.model_releases WHERE release_id=:id"),
                        {"id": run.release_id},
                    )
                )
            )
            if release.binding != run.resolved_model or release.image_digest != run.image_digest:
                raise ValueError("claimed_release_pin_mismatch")
            return Claim(running, profile, token, release, deadline)

    def _leased(self, connection: Connection, claim: Claim) -> tuple[Any, datetime]:
        row = (
            connection.execute(
                text(
                    "SELECT * FROM ai.forecast_batch_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
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
            or claim.run.purpose != self.purpose
        ):
            raise LeaseLost("batch_lease_lost")
        return row, now

    def heartbeat(self, claim: Claim) -> None:
        with self.engine.begin() as connection:
            checked(connection)
            row, now = self._leased(connection, claim)
            self._write(
                connection,
                claim.run,
                token=claim.token,
                lease=min(
                    now + timedelta(seconds=claim.run.policy.lease_seconds), row["attempt_deadline"]
                ),
                deadline=row["attempt_deadline"],
            )

    def execution_budget(self, claim: Claim) -> float:
        with self.engine.begin() as connection:
            checked(connection)
            row, now = self._leased(connection, claim)
            return float(min(120.0, (row["attempt_deadline"] - now).total_seconds()))

    def fail(self, claim: Claim, *, reason: str, retryable: bool) -> None:
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
                    "SELECT record FROM ai.forecast_batch_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
                ),
                {"id": run_id, "env": self.environment},
            ).first()
            if row is None:
                raise BatchError(404, "forecast-run-not-found")
            now = clock(connection)
            run = record(row[0])
            if run.status not in {"queued", "running"}:
                raise BatchError(409, "run-not-active")
            self._close(connection, run, now, "cancelled", "internal_cancel", False)

    def complete(self, claim: Claim, output: MechanicsOutput) -> None:
        output = MechanicsOutput.model_validate_json(output.model_dump_json())
        run = claim.run
        if (
            self.environment != "test"
            or run.purpose != "lifecycle_mechanics_only"
            or (output.run_id, output.release_id, output.profile_id)
            != (run.run_id, run.release_id, run.input_ref.profile_id)
        ):
            raise ValueError("mechanics_output_binding_mismatch")
        if not isinstance(claim.profile, MechanicsProfile):
            raise ValueError("mechanics_profile_required")
        expected = {
            tuple(r.model_dump(mode="json", exclude={"value"}).items())
            for r in selected(claim.profile, run)
        }
        actual = [
            tuple(r.model_dump(mode="json", exclude={"predicted_units"}).items())
            for r in output.predictions
        ]
        if len(actual) != len(expected) or set(actual) != expected:
            raise ValueError("mechanics_output_grain_or_count_mismatch")
        with self.engine.begin() as connection:
            checked(connection)
            _, now = self._leased(connection, claim)
            connection.execute(
                text(
                    "INSERT INTO ai.forecast_mechanics_outputs(artifact_id,run_id,output) VALUES (:id,:run,CAST(:output AS jsonb))"
                ),
                {"id": output.artifact_id, "run": run.run_id, "output": output.model_dump_json()},
            )
            raw = run.model_dump(mode="json")
            raw.update(
                status="succeeded",
                completed_at=now.isoformat(),
                output_ref=RunOutput(
                    kind="predictions", artifact_id=output.artifact_id, complete=True
                ).model_dump(mode="json"),
            )
            done = record(raw)
            transition_run(run, done)
            self._write(connection, done)
            self._history(connection, done, "mechanics_completed")

    def complete_forecast(self, claim: Claim, result: RuntimeResult) -> None:
        """All partitions, manifest, current pointer, run and history commit together."""
        if self.mechanics or not isinstance(claim.profile, PreparedInputs):
            raise ValueError("qualified_profile_required")
        with self.engine.begin() as connection:
            checked(connection)
            _, now = self._leased(connection, claim)
            output = publication(claim.run, claim.profile, result, now)
            m = output.manifest
            key = scope_key(m.scope, m.horizon_days)
            connection.execute(
                text("""INSERT INTO ai.forecast_output_manifests(artifact_id,run_id,environment,scope_key,manifest)
                VALUES (:id,:run,:env,:scope,CAST(:manifest AS jsonb))"""),
                {
                    "id": m.artifact_id,
                    "run": m.run_id,
                    "env": self.environment,
                    "scope": key,
                    "manifest": m.model_dump_json(),
                },
            )
            for partition in output.partitions:
                connection.execute(
                    text("""INSERT INTO ai.forecast_output_partitions(artifact_id,ordinal,partition,sha256)
                    VALUES (:id,:ordinal,CAST(:partition AS jsonb),:sha)"""),
                    {
                        "id": m.artifact_id,
                        "ordinal": partition.ordinal,
                        "partition": partition.model_dump_json(),
                        "sha": receipt(partition).sha256,
                    },
                )
            # Recheck DB time after the last write; the lock alone does not extend a lease.
            _, now = self._leased(connection, claim)
            raw = claim.run.model_dump(mode="json")
            raw.update(
                status="succeeded",
                completed_at=now.isoformat(),
                output_ref=RunOutput(
                    kind="predictions", artifact_id=m.artifact_id, complete=True
                ).model_dump(mode="json"),
            )
            done = record(raw)
            transition_run(claim.run, done)
            self._write(connection, done)
            self._history(connection, done, "forecast_completed")
            # A slower old origin cannot displace a more recent successful forecast.
            connection.execute(
                text("""INSERT INTO ai.forecast_output_heads(environment,scope_key,artifact_id)
                VALUES (:env,:scope,:id) ON CONFLICT(environment,scope_key) DO UPDATE SET artifact_id=EXCLUDED.artifact_id
                WHERE (SELECT ((manifest->>'as_of_time')::timestamptz, (r.record->>'requested_at')::timestamptz, m.run_id)
                       FROM ai.forecast_output_manifests m JOIN ai.forecast_batch_runs r USING(run_id) WHERE m.artifact_id=EXCLUDED.artifact_id)
                    > (SELECT ((manifest->>'as_of_time')::timestamptz, (r.record->>'requested_at')::timestamptz, m.run_id)
                       FROM ai.forecast_output_manifests m JOIN ai.forecast_batch_runs r USING(run_id) WHERE m.artifact_id=ai.forecast_output_heads.artifact_id)"""),
                {"env": self.environment, "scope": key, "id": m.artifact_id},
            )
