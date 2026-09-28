"""Persist requests before work; session ownership and fencing make interrupted work resumable."""

import hashlib
import json
import re
import secrets
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.pool import NullPool

from retailops_ai.adapters.index_lifecycle import read_current
from retailops_ai.adapters.vector_store import _boundary, store_candidate
from retailops_ai.data_contracts.run import RunRecord, transition_run
from retailops_ai.knowledge.jobs import (
    BUILD_PROFILE_ADAPTER,
    RUN_REPORT_ADAPTER,
    BuildProfile,
    CurrentKnowledgeIndex,
    GoldenIndexRunReport,
    IndexErrorCode,
    KnowledgeIndexRequest,
    KnowledgeRunInput,
    KnowledgeRunOutput,
    RunReport,
    index_config_id,
)
from retailops_ai.knowledge.releases import Lane
from retailops_ai.pipelines.index_builds import build_run_report, check_build_profile
from retailops_ai.pipelines.indexes import build_index
from retailops_ai.pipelines.releases import validate_candidate

QUEUE_LOCK = 384790013


class IndexJobError(ValueError):
    def __init__(self, status: int, code: IndexErrorCode) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class IndexAdministration(Protocol):
    def submit(self, request: KnowledgeIndexRequest, principal: str, key: str) -> RunRecord: ...
    def get(self, run_id: str) -> RunRecord: ...
    def current(self) -> CurrentKnowledgeIndex | None: ...


def _transaction(connection: Connection) -> None:
    connection.execute(text("SET LOCAL statement_timeout='15s'"))
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    _boundary(connection)


def _record(value: object) -> RunRecord:
    run = RunRecord.model_validate_json(json.dumps(value))
    if run.run_type != "knowledge_index" or not isinstance(run.input_ref, KnowledgeRunInput):
        raise ValueError("knowledge_run_binding_mismatch")
    return run


def register_profile(engine: Engine, profile: BuildProfile) -> bool:
    profile = BUILD_PROFILE_ADAPTER.validate_json(profile.model_dump_json())
    check_build_profile(profile)
    with engine.begin() as connection:
        _transaction(connection)
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK})
        existing = connection.scalar(
            text(
                "SELECT profile FROM ai.rag_build_profiles WHERE environment=:env AND request_hash=:hash"
            ),
            {"env": profile.environment, "hash": profile.request().request_hash()},
        )
        if existing is not None:
            if existing != profile.model_dump(mode="json"):
                raise ValueError("build_profile_registration_conflict")
            return False
        connection.execute(
            text("""INSERT INTO ai.rag_build_profiles(profile_id,environment,request_hash,profile)
            VALUES (:id,:env,:hash,CAST(:profile AS jsonb))"""),
            {
                "id": profile.profile_id,
                "env": profile.environment,
                "hash": profile.request().request_hash(),
                "profile": profile.model_dump_json(),
            },
        )
    return True


class PostgresIndexAdministration:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine = engine
        self.environment = environment

    def submit(self, request: KnowledgeIndexRequest, principal: str, key: str) -> RunRecord:
        request = KnowledgeIndexRequest.model_validate_json(request.model_dump_json())
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) is None:
            raise ValueError("invalid_idempotency_key")
        key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
        request_hash = request.request_hash()
        with self.engine.begin() as connection:
            _transaction(connection)
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK})
            existing = connection.scalar(
                text("""SELECT record FROM ai.knowledge_index_runs
                WHERE environment=:env AND principal_id=:principal AND key_hash=:key"""),
                {"env": self.environment, "principal": principal, "key": key_hash},
            )
            if existing is not None:
                run = _record(existing)
                if (
                    not isinstance(run.input_ref, KnowledgeRunInput)
                    or run.input_ref.request_hash != request_hash
                ):
                    raise IndexJobError(409, "idempotency-conflict")
                return run
            raw = connection.scalar(
                text(
                    "SELECT profile FROM ai.rag_build_profiles WHERE environment=:env AND request_hash=:hash"
                ),
                {"env": self.environment, "hash": request_hash},
            )
            if raw is None:
                raise IndexJobError(422, "configuration-not-approved")
            profile = BUILD_PROFILE_ADAPTER.validate_json(json.dumps(raw))
            if profile.request().request_hash() != request_hash:
                raise ValueError("stored_build_profile_mismatch")
            pending = connection.scalar(
                text(
                    "SELECT count(*) FROM ai.knowledge_index_runs WHERE environment=:env AND status IN ('queued','running')"
                ),
                {"env": self.environment},
            )
            if pending is None or pending >= 100:
                raise IndexJobError(429, "queue-full")
            run = RunRecord(
                schema_version="1.0",
                contract_type="run",
                run_id="run-" + secrets.token_hex(16),
                run_type="knowledge_index",
                status="queued",
                attempt=1,
                requested_at=datetime.now(UTC),
                requested_by=principal,
                started_at=None,
                completed_at=None,
                resolved_model=None,
                output_ref=None,
                error=None,
                input_ref=KnowledgeRunInput(
                    request=request,
                    request_hash=request_hash,
                    profile_id=profile.profile_id,
                    environment=profile.environment,
                ),
            )
            connection.execute(
                text("""INSERT INTO ai.knowledge_index_runs
                (run_id,environment,principal_id,key_hash,request_hash,profile_id,record)
                VALUES (:id,:env,:principal,:key,:hash,:profile,CAST(:record AS jsonb))"""),
                {
                    "id": run.run_id,
                    "env": self.environment,
                    "principal": principal,
                    "key": key_hash,
                    "hash": request_hash,
                    "profile": profile.profile_id,
                    "record": run.model_dump_json(),
                },
            )
        return run

    def get(self, run_id: str) -> RunRecord:
        with self.engine.connect() as connection:
            _boundary(connection)
            value = connection.scalar(
                text(
                    "SELECT record FROM ai.knowledge_index_runs WHERE run_id=:id AND environment=:env"
                ),
                {"id": run_id, "env": self.environment},
            )
            if value is None:
                raise IndexJobError(404, "index-run-not-found")
            return _record(value)

    def current(self) -> CurrentKnowledgeIndex | None:
        with self.engine.connect() as connection:
            _boundary(connection)
            lane: Lane = "offline_test" if self.environment == "test" else "retrieval"
            pin = read_current(connection, self.environment, lane)
            if pin is None:
                return None
            row = connection.execute(
                text("""SELECT e.recorded_at,i.chunk_manifest
                FROM ai.rag_index_changes e JOIN ai.rag_indexes i USING(index_id)
                WHERE e.request_id=:request"""),
                {"request": pin.request_id},
            ).one()
            # The second read uses the immutable event from the first pointer snapshot.
            from retailops_ai.knowledge.chunks import ChunkManifest

            chunks = ChunkManifest.model_validate_json(json.dumps(row.chunk_manifest))
            manifest = pin.manifest
            return CurrentKnowledgeIndex(
                index_id=manifest.index_id,
                environment=pin.environment,
                lane=pin.lane,
                purpose=pin.purpose,
                manifest_ref="db:ai.rag_indexes:" + manifest.index_id,
                corpus_manifest_id=manifest.corpus_id,
                chunk_manifest_id=manifest.chunk_manifest_id,
                index_config_id=index_config_id(chunks, manifest.embedding_config),
                embedding_config_id=manifest.space_id,
                dimension=manifest.embedding_config.dimension,
                document_count=len(chunks.corpus.documents),
                chunk_count=manifest.chunk_count,
                activated_at=row.recorded_at,
                evaluation_report_ref="db:ai.rag_qualifications:" + pin.validation_id,
            )


def worker_lock_key(environment: str, run_id: str) -> int:
    digest = hashlib.sha256(f"knowledge-index-worker:{environment}:{run_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & (2**63 - 1)


def read_run_report(engine: Engine, environment: str, run_id: str) -> RunReport:
    with engine.connect() as connection:
        _boundary(connection)
        row = connection.execute(
            text("""SELECT k.record,r.report FROM ai.knowledge_index_runs k
            LEFT JOIN ai.rag_index_reports r ON r.report_id=k.report_id AND r.profile_id=k.profile_id
            WHERE k.run_id=:id AND k.environment=:env"""),
            {"id": run_id, "env": environment},
        ).first()
        if row is None:
            raise IndexJobError(404, "index-run-not-found")
        if row.report is None:
            raise IndexJobError(404, "index-report-not-found")
        run = _record(row.record)
        report = RUN_REPORT_ADAPTER.validate_json(json.dumps(row.report))
        if (
            not isinstance(run.input_ref, KnowledgeRunInput)
            or report.profile_id != run.input_ref.profile_id
            or report.validation.environment != environment
        ):
            raise ValueError("stored_run_report_binding_mismatch")
        return report


def cancel_run(engine: Engine, environment: str, run_id: str) -> RunRecord:
    with engine.begin() as connection:
        _transaction(connection)
        row = connection.scalar(
            text(
                "SELECT record FROM ai.knowledge_index_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
            ),
            {"id": run_id, "env": environment},
        )
        if row is None:
            raise IndexJobError(404, "index-run-not-found")
        current = _record(row)
        if current.status in {"succeeded", "failed", "cancelled"}:
            return current
        # Fencing and transition are in the same statement.
        value = current.model_dump(mode="json")
        value.update(
            status="cancelled",
            completed_at=datetime.now(UTC).isoformat(),
            error={"code": "cancelled", "retryable": False},
        )
        following = transition_run(current, RunRecord.model_validate_json(json.dumps(value)))
        connection.execute(
            text(
                "UPDATE ai.knowledge_index_runs SET claim_token=NULL,record=CAST(:record AS jsonb) WHERE run_id=:id"
            ),
            {"id": run_id, "record": following.model_dump_json()},
        )
        return following


def execute_run(engine: Engine, environment: Literal["local", "test"], run_id: str) -> RunRecord:
    """Process one explicitly selected run; resume the same attempt after worker death."""
    administration = PostgresIndexAdministration(engine, environment)
    # A separate non-pooled connection holds the session lock; DB operations use the bounded pool.
    lock_engine = create_engine(engine.url, connect_args={"connect_timeout": 3}, poolclass=NullPool)
    key = worker_lock_key(environment, run_id)
    try:
        with lock_engine.connect() as owner:
            _transaction(owner)
            acquired = owner.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
            owner.commit()
            if not acquired:
                raise IndexJobError(409, "worker-busy")
            try:
                claim = str(uuid4())
                with engine.begin() as connection:
                    _transaction(connection)
                    raw = connection.scalar(
                        text(
                            "SELECT record FROM ai.knowledge_index_runs WHERE run_id=:id AND environment=:env FOR UPDATE"
                        ),
                        {"id": run_id, "env": environment},
                    )
                    if raw is None:
                        raise IndexJobError(404, "index-run-not-found")
                    current = _record(raw)
                    if current.status not in {"queued", "running"}:
                        return current
                    if current.status == "queued":
                        value = current.model_dump(mode="json")
                        value.update(status="running", started_at=datetime.now(UTC).isoformat())
                        current = transition_run(
                            current, RunRecord.model_validate_json(json.dumps(value))
                        )
                    connection.execute(
                        text(
                            "UPDATE ai.knowledge_index_runs SET claim_token=CAST(:claim AS uuid),record=CAST(:record AS jsonb) WHERE run_id=:id"
                        ),
                        {"id": run_id, "claim": claim, "record": current.model_dump_json()},
                    )
                    if not isinstance(current.input_ref, KnowledgeRunInput):
                        raise ValueError("worker_input_mismatch")
                    profile_raw = connection.scalar(
                        text("SELECT profile FROM ai.rag_build_profiles WHERE profile_id=:profile"),
                        {"profile": current.input_ref.profile_id},
                    )
                report = None
                output = None
                failure = None
                try:
                    profile = BUILD_PROFILE_ADAPTER.validate_json(json.dumps(profile_raw))
                    if (
                        not isinstance(current.input_ref, KnowledgeRunInput)
                        or profile.request().request_hash() != current.input_ref.request_hash
                    ):
                        raise ValueError("worker_profile_binding_mismatch")
                    candidate = build_index(profile.chunks, profile.embedding_config)
                    validation = validate_candidate(candidate)
                    report = build_run_report(profile, candidate, validation)
                    store_candidate(engine, candidate)
                    if validation.result != "passed" or (
                        isinstance(report, GoldenIndexRunReport) and not report.quality_gate_passed
                    ):
                        failure = {"code": "gate_failed", "retryable": False}
                    else:
                        output = KnowledgeRunOutput(
                            kind="knowledge_index",
                            complete=True,
                            index_id=candidate.manifest.index_id,
                            manifest_ref="db:ai.rag_indexes:" + candidate.manifest.index_id,
                            evaluation_report_ref="db:ai.rag_index_reports:" + report.report_id,
                            activation_status="candidate",
                        )
                except ValueError:
                    failure = {"code": "invalid_input", "retryable": False}
                    output = None
                    report = None
                # Dependency outages leave a resumable running record, never a false success.
                owner.execute(text("SELECT 1"))
                owner.commit()
                with engine.begin() as connection:
                    _transaction(connection)
                    row = connection.execute(
                        text(
                            "SELECT record,claim_token::text AS claim FROM ai.knowledge_index_runs WHERE run_id=:id FOR UPDATE"
                        ),
                        {"id": run_id},
                    ).one()
                    if row.claim != claim:
                        raise IndexJobError(409, "claim-lost")
                    latest = _record(row.record)
                    if report is not None:
                        connection.execute(
                            text("""INSERT INTO ai.rag_index_reports(report_id,profile_id,index_id,report)
                            VALUES (:id,:profile,:index,CAST(:report AS jsonb)) ON CONFLICT DO NOTHING"""),
                            {
                                "id": report.report_id,
                                "profile": report.profile_id,
                                "index": report.validation.index_id,
                                "report": report.model_dump_json(),
                            },
                        )
                        stored = connection.scalar(
                            text("SELECT report FROM ai.rag_index_reports WHERE report_id=:id"),
                            {"id": report.report_id},
                        )
                        if stored != report.model_dump(mode="json"):
                            raise ValueError("index_report_replay_mismatch")
                    final = latest.model_dump(mode="json")
                    final.update(
                        status="succeeded" if output is not None else "failed",
                        completed_at=datetime.now(UTC).isoformat(),
                        output_ref=output.model_dump(mode="json") if output else None,
                        error=failure
                        or (None if output else {"code": "execution_failed", "retryable": False}),
                    )
                    following = transition_run(
                        latest, RunRecord.model_validate_json(json.dumps(final))
                    )
                    connection.execute(
                        text("""UPDATE ai.knowledge_index_runs SET record=CAST(:record AS jsonb),claim_token=NULL,
                        output_index_id=:index,report_id=:report WHERE run_id=:id"""),
                        {
                            "id": run_id,
                            "record": following.model_dump_json(),
                            "index": output.index_id if output else None,
                            "report": report.report_id if report else None,
                        },
                    )
                return administration.get(run_id)
            finally:
                owner.rollback()
                owner.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                owner.commit()
    finally:
        lock_engine.dispose()
