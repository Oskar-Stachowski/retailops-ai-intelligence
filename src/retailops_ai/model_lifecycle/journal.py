"""PostgreSQL advisory serialization and immutable, independently persisted history."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from retailops_ai.model_lifecycle.contracts import Binding, Release


class PostgresLock:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.connection: Connection | None = None

    @contextmanager
    def locked(self, model: str) -> Iterator[None]:
        if self.connection is not None:
            raise ValueError("nested_lifecycle_lock")
        # Stable signed bigint; all lifecycle processes share this database lock.
        lock_id = int.from_bytes(hashlib.sha256(model.encode()).digest()[:8], signed=True)
        with self.engine.connect() as connection:
            self.connection = connection
            try:
                acquired = connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}
                )
                connection.commit()
                if not acquired:
                    raise ValueError("model_lifecycle_busy")
                try:
                    yield
                finally:
                    connection.rollback()
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                    connection.commit()
            finally:
                self.connection = None

    def query(self, sql: str, **params: object) -> Any:
        if self.connection is None:
            raise ValueError("model_lock_required")
        return self.connection.execute(text(sql), params)

    def commit(self) -> None:
        if self.connection is None:
            raise ValueError("model_lock_required")
        self.connection.commit()


class PostgresJournal(PostgresLock):
    def decision(self, decision_id: str) -> dict[str, Any] | None:
        value = self.query(
            "SELECT record FROM ai.model_decisions WHERE decision_id=:id", id=decision_id
        ).scalar_one_or_none()
        return dict(value) if value is not None else None

    def pending(self, model: str) -> list[str]:
        return list(
            self.query(
                "SELECT decision_id FROM ai.model_decisions d WHERE model_name=:model AND NOT EXISTS "
                "(SELECT 1 FROM ai.model_steps s WHERE s.decision_id=d.decision_id AND phase='completed')",
                model=model,
            ).scalars()
        )

    def prepare(self, record: dict[str, Any]) -> None:
        self.query(
            "INSERT INTO ai.model_decisions(decision_id,model_name,request_sha256,record) "
            "VALUES (:id,:model,:sha,CAST(:record AS jsonb))",
            id=record["request"]["decision_id"],
            model=record["request"]["model_name"],
            sha=record["request_sha256"],
            record=json.dumps(record),
        )
        self.commit()

    def step(self, decision_id: str, phase: str) -> dict[str, Any] | None:
        value = self.query(
            "SELECT record FROM ai.model_steps WHERE decision_id=:id AND phase=:phase",
            id=decision_id,
            phase=phase,
        ).scalar_one_or_none()
        return dict(value) if value is not None else None

    def append(self, decision_id: str, phase: str, record: dict[str, Any]) -> None:
        previous = self.step(decision_id, phase)
        if previous is not None:
            if previous != record:
                raise ValueError("model_audit_phase_conflict")
            return
        self.query(
            "INSERT INTO ai.model_steps(decision_id,phase,record) VALUES (:id,:phase,CAST(:record AS jsonb))",
            id=decision_id,
            phase=phase,
            record=json.dumps(record),
        )
        self.commit()

    def binding(self, model: str, version: str) -> Binding:
        value = self.query(
            "SELECT binding FROM ai.model_versions WHERE model_name=:model AND model_version=:version",
            model=model,
            version=version,
        ).scalar_one_or_none()
        if value is None:
            raise ValueError("model_version_not_enrolled")
        return Binding.model_validate_json(json.dumps(value))

    def bind(self, decision_id: str, binding: Binding) -> None:
        value = self.query(
            "SELECT binding FROM ai.model_versions WHERE model_name=:model AND model_version=:version",
            model=binding.model_name,
            version=binding.model_version,
        ).scalar_one_or_none()
        if value is not None:
            if value != binding.model_dump(mode="json"):
                raise ValueError("immutable_model_binding_conflict")
            return
        self.query(
            "INSERT INTO ai.model_versions(model_name,model_version,binding,decision_id) "
            "VALUES (:model,:version,CAST(:binding AS jsonb),:id)",
            model=binding.model_name,
            version=binding.model_version,
            binding=binding.model_dump_json(),
            id=decision_id,
        )
        self.commit()

    def rejected(self, model: str, version: str) -> bool:
        return bool(
            self.query(
                "SELECT EXISTS(SELECT 1 FROM ai.model_decisions d JOIN ai.model_steps s USING(decision_id) "
                "WHERE d.model_name=:model AND d.record->'request'->>'model_version'=:version "
                "AND d.record->'request'->>'action'='reject' AND s.phase='completed')",
                model=model,
                version=version,
            ).scalar_one()
        )

    def release(self, release_id: str) -> Release:
        value = self.query(
            "SELECT release FROM ai.model_releases WHERE release_id=:id", id=release_id
        ).scalar_one()
        return Release.model_validate_json(json.dumps(value))

    def active(self, model: str) -> Release | None:
        value = self.query(
            "SELECT release_id FROM ai.model_heads WHERE model_name=:model", model=model
        ).scalar_one_or_none()
        return self.release(value) if value is not None else None

    def activate(self, release: Release) -> None:
        # The release, approved pointer and completion audit commit together.
        self.query(
            "INSERT INTO ai.model_releases(release_id,model_name,model_version,release,decision_id) "
            "VALUES (:release,:model,:version,CAST(:record AS jsonb),:decision) ON CONFLICT DO NOTHING",
            release=release.release_id,
            model=release.binding.model_name,
            version=release.binding.model_version,
            record=release.model_dump_json(),
            decision=release.decision_id,
        )
        if self.release(release.release_id) != release:
            raise ValueError("immutable_release_conflict")
        self.query(
            "INSERT INTO ai.model_heads(model_name,release_id) VALUES (:model,:release) "
            "ON CONFLICT(model_name) DO UPDATE SET release_id=EXCLUDED.release_id",
            model=release.binding.model_name,
            release=release.release_id,
        )
