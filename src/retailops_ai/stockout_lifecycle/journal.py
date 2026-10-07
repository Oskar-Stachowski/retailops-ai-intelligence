"""Append-only stockout history, using shared AI 05 advisory serialization."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.model_lifecycle.journal import PostgresLock
from retailops_ai.stockout_lifecycle.contract import (
    MODEL,
    TEST_MODEL,
    StockoutBinding,
    StockoutModelRelease,
)


class PostgresStockoutJournal(PostgresLock):
    @contextmanager
    def locked(self, model: str) -> Iterator[None]:
        if model not in {MODEL, TEST_MODEL}:
            raise ValueError("stockout_journal_namespace")
        with super().locked(model):
            revision = self.query("SELECT version_num FROM ai.alembic_version").scalar_one()
            if revision != EXPECTED_REVISION:
                raise ValueError("stockout_journal_migration_required")
            self.commit()
            yield

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        value = self.query(
            "SELECT record FROM ai.stockout_model_decisions WHERE decision_id=:id", id=decision_id
        ).scalar_one_or_none()
        return dict(value) if value is not None else None

    def pending(self, model: str) -> list[str]:
        return list(
            self.query(
                "SELECT decision_id FROM ai.stockout_model_decisions d WHERE model_name=:model AND NOT EXISTS "
                "(SELECT 1 FROM ai.stockout_model_steps s WHERE s.decision_id=d.decision_id AND phase='completed')",
                model=model,
            ).scalars()
        )

    def prepare(self, record: dict[str, Any]) -> None:
        self.query(
            "INSERT INTO ai.stockout_model_decisions(decision_id,model_name,request_sha256,record) "
            "VALUES (:id,:model,:sha,CAST(:record AS jsonb))",
            id=record["request"]["decision_id"],
            model=record["request"]["model_name"],
            sha=record["request_sha256"],
            record=json.dumps(record),
        )
        self.commit()

    def step(self, decision_id: str, phase: str) -> dict[str, Any] | None:
        value = self.query(
            "SELECT record FROM ai.stockout_model_steps WHERE decision_id=:id AND phase=:phase",
            id=decision_id,
            phase=phase,
        ).scalar_one_or_none()
        return dict(value) if value is not None else None

    def append(self, decision_id: str, phase: str, record: dict[str, Any]) -> None:
        previous = self.step(decision_id, phase)
        if previous is not None:
            if previous != record:
                raise ValueError("stockout_journal_phase_conflict")
            return
        self.query(
            "INSERT INTO ai.stockout_model_steps(decision_id,phase,record) VALUES (:id,:phase,CAST(:record AS jsonb))",
            id=decision_id,
            phase=phase,
            record=json.dumps(record),
        )
        self.commit()

    def binding(self, model: str, version: str) -> StockoutBinding:
        value = self.query(
            "SELECT binding FROM ai.stockout_model_versions WHERE model_name=:model AND model_version=:version",
            model=model,
            version=version,
        ).scalar_one_or_none()
        if value is None:
            raise ValueError("stockout_journal_version_not_enrolled")
        return StockoutBinding.model_validate_json(json.dumps(value))

    def bind(self, decision_id: str, binding: StockoutBinding) -> None:
        value = (
            self.query(
                "SELECT binding,decision_id FROM ai.stockout_model_versions WHERE model_name=:model AND model_version=:version",
                model=binding.model_name,
                version=binding.model_version,
            )
            .mappings()
            .one_or_none()
        )
        if value is not None:
            if (
                value["binding"] != binding.model_dump(mode="json")
                or value["decision_id"] != decision_id
            ):
                raise ValueError("stockout_journal_binding_conflict")
            return
        self.query(
            "INSERT INTO ai.stockout_model_versions(model_name,model_version,binding,decision_id) "
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
                "SELECT EXISTS(SELECT 1 FROM ai.stockout_model_decisions d JOIN ai.stockout_model_steps s USING(decision_id) "
                "WHERE d.model_name=:model AND d.record->'request'->>'model_version'=:version "
                "AND d.record->'request'->>'action'='reject' AND s.phase='completed')",
                model=model,
                version=version,
            ).scalar_one()
        )

    def release(self, release_id: str) -> StockoutModelRelease:
        value = self.query(
            "SELECT release FROM ai.stockout_model_releases WHERE release_id=:id", id=release_id
        ).scalar_one()
        return StockoutModelRelease.model_validate_json(json.dumps(value))

    def active(self, model: str) -> StockoutModelRelease | None:
        value = self.query(
            "SELECT release_id FROM ai.stockout_model_heads WHERE model_name=:model", model=model
        ).scalar_one_or_none()
        return self.release(value) if value is not None else None

    def activate(self, release: StockoutModelRelease) -> None:
        self.query(
            "INSERT INTO ai.stockout_model_releases(release_id,model_name,model_version,release,decision_id) "
            "VALUES (:release,:model,:version,CAST(:record AS jsonb),:decision) ON CONFLICT DO NOTHING",
            release=release.release_id,
            model=release.binding.model_name,
            version=release.binding.model_version,
            record=release.model_dump_json(),
            decision=release.decision_id,
        )
        if self.release(release.release_id) != release:
            raise ValueError("stockout_journal_release_conflict")
        active = self.active(release.binding.model_name)
        if active is None:
            self.query(
                "INSERT INTO ai.stockout_model_heads(model_name,release_id) VALUES (:model,:release)",
                model=release.binding.model_name,
                release=release.release_id,
            )
        elif active.release_id != release.release_id:
            self.query(
                "UPDATE ai.stockout_model_heads SET release_id=:release WHERE model_name=:model",
                model=release.binding.model_name,
                release=release.release_id,
            )
        # No commit: lifecycle appends completion before committing head + release together.
