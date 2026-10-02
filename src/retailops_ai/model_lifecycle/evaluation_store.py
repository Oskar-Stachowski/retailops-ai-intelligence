"""Immutable evaluation evidence with whole-scope authorization before counts/pages."""

import json
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import checked
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReadError, ReadScope, resolve_scope
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationDetail,
    EvaluationErrorCode,
    EvaluationEvidence,
    EvaluationFreshness,
    EvaluationPage,
    EvaluationQuery,
    EvaluationSummary,
)
from retailops_ai.model_lifecycle.read_contracts import CatalogPagination, CatalogScope

MAX_RECORDS = 256
MAX_BYTES = 64 * 1024


class EvaluationError(ValueError):
    def __init__(self, status: int, code: EvaluationErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class EvaluationReader(Protocol):
    def evaluations(self, query: EvaluationQuery, actor: Principal) -> EvaluationPage: ...
    def evaluation(
        self, identity: str, scope: CatalogScope, actor: Principal
    ) -> EvaluationDetail: ...


def authorized(scope: CatalogScope, actor: Principal, *, campaign: bool = False) -> ReadScope:
    try:
        return resolve_scope(
            ForecastQuery(
                product_id=scope.product_id,
                selling_location_id=scope.selling_location_id,
                channel=scope.channel,
            ),
            actor,
            max_products=200 if campaign else 20,
            max_locations=100 if campaign else 5,
        )
    except ForecastReadError as exc:
        codes: dict[str, EvaluationErrorCode] = {
            "forecast-read-denied": "evaluation-read-denied",
            "forecast-scope-invalid": "evaluation-scope-invalid",
            "forecast-scope-limit": "evaluation-scope-limit",
        }
        raise EvaluationError(exc.status, codes[exc.code]) from None


def visible(evidence: EvaluationEvidence, scope: ReadScope) -> bool:
    s = evidence.descriptor.scope
    return (
        set(s.product_ids) <= set(scope.products)
        and set(s.selling_location_ids) <= set(scope.locations)
        and set(s.channels) <= set(scope.channels)
    )


def summary(
    evidence: EvaluationEvidence, now: datetime, *, detail: bool = False
) -> EvaluationSummary:
    cls = EvaluationDetail if detail else EvaluationSummary
    raw = evidence.descriptor.model_dump(mode="json", include=set(cls.model_fields))
    raw.update(
        evidence_sha256=evidence.evidence_sha256,
        freshness=EvaluationFreshness(evaluated_at=now).model_dump(mode="json"),
    )
    return cls.model_validate_json(json.dumps(raw))


def projection(
    evidence: tuple[EvaluationEvidence, ...],
    query: EvaluationQuery,
    actor: Principal,
    now: datetime,
) -> EvaluationPage:
    scope = authorized(query, actor)
    selected = sorted(
        (
            e
            for e in evidence
            if visible(e, scope)
            and (
                query.quality_status is None or e.descriptor.quality_status == query.quality_status
            )
        ),
        key=lambda e: (e.descriptor.generated_at, e.descriptor.evaluation_id),
        reverse=True,
    )
    if len(selected) > MAX_RECORDS:
        raise EvaluationError(429, "evaluation-read-budget")
    view = canonical_sha256(
        dict(
            projection="evaluation-read-v1",
            principal_id=actor.principal_id,
            scope=dict(products=scope.products, locations=scope.locations, channels=scope.channels),
            quality_status=query.quality_status,
            evidence=[e.evidence_sha256 for e in selected],
        )
    )
    if query.offset and query.view_sha256 is None:
        raise EvaluationError(409, "evaluation-view-required")
    if query.view_sha256 is not None and query.view_sha256 != view:
        raise EvaluationError(409, "evaluation-view-changed")
    end = query.offset + query.limit
    return EvaluationPage(
        items=tuple(summary(e, now) for e in selected[query.offset : end]),
        pagination=CatalogPagination(
            limit=query.limit,
            offset=query.offset,
            total=len(selected),
            next_offset=end if end < len(selected) else None,
        ),
        generated_at=now,
        data_status="available" if selected else "no_data",
        view_sha256=view,
    )


class PostgresEvaluations:
    def __init__(self, engine: Engine, environment: Literal["local", "test"]) -> None:
        self.engine, self.environment = engine, environment

    def register(self, evidence: EvaluationEvidence) -> EvaluationEvidence:
        evidence = EvaluationEvidence.model_validate_json(evidence.model_dump_json())
        if len(canonical_bytes(evidence.model_dump(mode="json"))) > MAX_BYTES:
            raise ValueError("evaluation_evidence_byte_limit")
        if (
            evidence.descriptor.purpose == "synthetic_acceptance_only"
            and self.environment != "test"
        ):
            raise ValueError("synthetic_evaluation_requires_test_environment")
        with self.engine.begin() as conn:
            now = checked(conn)
            conn.execute(text("SELECT pg_advisory_xact_lock(505051)"))
            if evidence.descriptor.generated_at > now:
                raise ValueError("evaluation_evidence_from_future")
            old = conn.scalar(
                text(
                    "SELECT evidence FROM ai.forecast_evaluations WHERE environment=:env AND evaluation_id=:id"
                ),
                {"env": self.environment, "id": evidence.descriptor.evaluation_id},
            )
            if old is not None:
                previous = EvaluationEvidence.model_validate_json(json.dumps(old))
                if previous != evidence:
                    raise ValueError("evaluation_registration_conflict")
                return previous
            if (
                conn.scalar(
                    text("SELECT count(*) FROM ai.forecast_evaluations WHERE environment=:env"),
                    {"env": self.environment},
                )
                >= MAX_RECORDS
            ):
                raise ValueError("evaluation_registration_capacity")
            conn.execute(
                text(
                    "INSERT INTO ai.forecast_evaluations(environment,evaluation_id,evidence_sha256,evidence) VALUES(:env,:id,:sha,CAST(:body AS jsonb))"
                ),
                {
                    "env": self.environment,
                    "id": evidence.descriptor.evaluation_id,
                    "sha": evidence.evidence_sha256,
                    "body": evidence.model_dump_json(),
                },
            )
        return evidence

    def _read(
        self,
        scope: CatalogScope,
        actor: Principal,
        *,
        identity: str | None = None,
        status: str | None = None,
    ) -> tuple[tuple[EvaluationEvidence, ...], datetime]:
        resolved = authorized(scope, actor)
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                rows = conn.execute(
                    text("""
                    SELECT evaluation_id,evidence_sha256,registered_at,
                      CASE WHEN octet_length(evidence::text)<=131072 THEN evidence END evidence
                    FROM ai.forecast_evaluations
                    WHERE environment=:env AND (CAST(:id AS text) IS NULL OR evaluation_id=:id)
                      AND (CAST(:status AS text) IS NULL OR evidence->'descriptor'->>'quality_status'=:status)
                      AND (evidence->'descriptor'->'scope'->'product_ids') <@ CAST(:products AS jsonb)
                      AND (evidence->'descriptor'->'scope'->'selling_location_ids') <@ CAST(:locations AS jsonb)
                      AND (evidence->'descriptor'->'scope'->'channels') <@ CAST(:channels AS jsonb)
                    ORDER BY (evidence->'descriptor'->>'generated_at')::timestamptz DESC,evaluation_id DESC LIMIT :cap
                """),
                    {
                        "env": self.environment,
                        "id": identity,
                        "status": status,
                        "products": json.dumps(resolved.products),
                        "locations": json.dumps(resolved.locations),
                        "channels": json.dumps(resolved.channels),
                        "cap": MAX_RECORDS + 1,
                    },
                ).all()
                if len(rows) > MAX_RECORDS:
                    raise EvaluationError(429, "evaluation-read-budget")
                results = []
                for row in rows:
                    e = EvaluationEvidence.model_validate_json(json.dumps(row.evidence))
                    if (
                        e.descriptor.evaluation_id != row.evaluation_id
                        or e.evidence_sha256 != row.evidence_sha256
                        or not e.descriptor.generated_at <= row.registered_at <= now
                        or not visible(e, resolved)
                        or len(canonical_bytes(e.model_dump(mode="json"))) > MAX_BYTES
                        or e.descriptor.purpose == "synthetic_acceptance_only"
                        and self.environment != "test"
                    ):
                        raise ValueError("evaluation_stored_evidence_mismatch")
                    results.append(e)
                return tuple(results), now

    def evaluations(self, query: EvaluationQuery, actor: Principal) -> EvaluationPage:
        rows, now = self._read(query, actor, status=query.quality_status)
        return projection(rows, query, actor, now)

    def evaluation(self, identity: str, scope: CatalogScope, actor: Principal) -> EvaluationDetail:
        rows, now = self._read(scope, actor, identity=identity)
        if not rows:
            raise EvaluationError(404, "evaluation-not-found")
        result = summary(rows[0], now, detail=True)
        if not isinstance(result, EvaluationDetail):
            raise ValueError("evaluation_detail_projection_type")
        return result
