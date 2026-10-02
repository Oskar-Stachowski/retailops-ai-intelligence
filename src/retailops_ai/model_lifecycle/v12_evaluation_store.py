"""Immutable whole-campaign reports; scope is filtered in SQL before counts and byte limits."""

import json
from datetime import datetime
from typing import Literal, Protocol

from sqlalchemy import Engine, text

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import checked
from retailops_ai.model_lifecycle.evaluation_contracts import EvaluationFreshness
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError, authorized
from retailops_ai.model_lifecycle.read_contracts import CatalogPagination, CatalogScope
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import model_namespace
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    V12EvaluationDetail,
    V12EvaluationEvidence,
    V12EvaluationPage,
    V12EvaluationQuery,
    V12EvaluationSummary,
)

MAX_RECORDS = 256
MAX_READ_BYTES = 16 * 1024**2


class V12EvaluationReader(Protocol):
    def evaluations(self, query: V12EvaluationQuery, actor: Principal) -> V12EvaluationPage: ...
    def evaluation(
        self, identity: str, scope: CatalogScope, actor: Principal
    ) -> V12EvaluationDetail: ...


def summary(
    evidence: V12EvaluationEvidence, now: datetime, *, detail: bool = False
) -> V12EvaluationSummary:
    raw = evidence.descriptor.model_dump(mode="json")
    raw.update(
        evaluation_id=evidence.evaluation_id,
        evidence_sha256=evidence.evidence_sha256,
        freshness=EvaluationFreshness(evaluated_at=now).model_dump(mode="json"),
    )
    if detail:
        raw["segments"] = evidence.original_metrics["segments"]
    cls = V12EvaluationDetail if detail else V12EvaluationSummary
    return cls.model_validate_json(json.dumps(raw))


def projection(
    evidence: tuple[V12EvaluationEvidence, ...],
    query: V12EvaluationQuery,
    actor: Principal,
    now: datetime,
) -> V12EvaluationPage:
    scope = authorized(query, actor)
    selected = sorted(
        (
            e
            for e in evidence
            if set(e.descriptor.scope.product_ids) <= set(scope.products)
            and set(e.descriptor.scope.selling_location_ids) <= set(scope.locations)
            and set(e.descriptor.scope.channels) <= set(scope.channels)
            and (
                query.quality_status is None or e.descriptor.quality_status == query.quality_status
            )
        ),
        key=lambda e: (e.descriptor.exported_at, e.evaluation_id),
        reverse=True,
    )
    if len(selected) > MAX_RECORDS:
        raise EvaluationError(429, "evaluation-read-budget")
    view = canonical_sha256(
        dict(
            projection="v12-evaluations-v1",
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
    return V12EvaluationPage(
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


class PostgresV12Evaluations:
    def __init__(
        self,
        engine: Engine,
        environment: Literal["local", "test"],
        *,
        mechanics: bool = False,
        development: bool = False,
    ) -> None:
        if environment not in {"local", "test"} or (mechanics and environment != "test"):
            raise ValueError("v12_evaluation_environment")
        self.engine, self.environment = engine, environment
        self.model = model_namespace(environment, mechanics=mechanics, development=development)

    def register(self, evidence: V12EvaluationEvidence, actor: Principal) -> V12EvaluationEvidence:
        if "promoter" not in actor.roles or "model:decide" not in actor.capabilities:
            raise ValueError("v12_evaluation_operator_required")
        evidence = V12EvaluationEvidence.model_validate_json(evidence.model_dump_json())
        if evidence.descriptor.model_name != self.model:
            raise ValueError("v12_evaluation_namespace")
        with self.engine.begin() as conn:
            now = checked(conn)
            conn.execute(text("SELECT pg_advisory_xact_lock(505080)"))
            if evidence.descriptor.exported_at > now:
                raise ValueError("v12_evaluation_from_future")
            old = conn.scalar(
                text(
                    "SELECT evidence FROM ai.v12_evaluations WHERE environment=:env AND evaluation_id=:id"
                ),
                dict(env=self.environment, id=evidence.evaluation_id),
            )
            if old is not None:
                previous = V12EvaluationEvidence.model_validate_json(json.dumps(old))
                if previous != evidence:
                    raise ValueError("v12_evaluation_registration_conflict")
                return previous
            if (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM ai.v12_evaluations WHERE environment=:env AND model_name=:model"
                    ),
                    dict(env=self.environment, model=self.model),
                )
                >= MAX_RECORDS
            ):
                raise ValueError("v12_evaluation_store_capacity")
            conn.execute(
                text(
                    "INSERT INTO ai.v12_evaluations(environment,model_name,evaluation_id,evidence_sha256,evidence) VALUES(:env,:model,:id,:sha,CAST(:body AS jsonb))"
                ),
                dict(
                    env=self.environment,
                    model=self.model,
                    id=evidence.evaluation_id,
                    sha=evidence.evidence_sha256,
                    body=evidence.model_dump_json(),
                ),
            )
        return evidence

    def _read(
        self,
        query: CatalogScope,
        actor: Principal,
        *,
        identity: str | None = None,
        status: str | None = None,
    ) -> tuple[tuple[V12EvaluationEvidence, ...], datetime]:
        scope = authorized(query, actor)
        params = dict(
            env=self.environment,
            model=self.model,
            id=identity,
            status=status,
            products=json.dumps(scope.products),
            locations=json.dumps(scope.locations),
            channels=json.dumps(scope.channels),
            cap=MAX_RECORDS + 1,
        )
        with self.engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                now = checked(conn)
                conn.execute(text("SET LOCAL statement_timeout='3s'"))
                headers = conn.execute(
                    text("""
SELECT evaluation_id,evidence_sha256,registered_at,octet_length(evidence::text) bytes
FROM ai.v12_evaluations WHERE environment=:env AND model_name=:model
 AND (CAST(:id AS text) IS NULL OR evaluation_id=:id)
 AND (CAST(:status AS text) IS NULL OR evidence->'descriptor'->>'quality_status'=:status)
 AND evidence->'descriptor'->'scope'->'product_ids' <@ CAST(:products AS jsonb)
 AND evidence->'descriptor'->'scope'->'selling_location_ids' <@ CAST(:locations AS jsonb)
 AND evidence->'descriptor'->'scope'->'channels' <@ CAST(:channels AS jsonb)
ORDER BY (evidence->'descriptor'->>'exported_at')::timestamptz DESC,evaluation_id DESC LIMIT :cap
"""),
                    params,
                ).all()
                if len(headers) > MAX_RECORDS or sum(h.bytes for h in headers) > MAX_READ_BYTES:
                    raise EvaluationError(429, "evaluation-read-budget")
                values = []
                for header in headers:
                    raw = conn.scalar(
                        text(
                            "SELECT evidence FROM ai.v12_evaluations WHERE environment=:env AND evaluation_id=:id"
                        ),
                        dict(env=self.environment, id=header.evaluation_id),
                    )
                    e = V12EvaluationEvidence.model_validate_json(json.dumps(raw))
                    if (
                        e.evaluation_id != header.evaluation_id
                        or e.evidence_sha256 != header.evidence_sha256
                        or e.descriptor.model_name != self.model
                        or not e.descriptor.exported_at <= header.registered_at <= now
                        or not set(e.descriptor.scope.product_ids) <= set(scope.products)
                        or not set(e.descriptor.scope.selling_location_ids) <= set(scope.locations)
                        or not set(e.descriptor.scope.channels) <= set(scope.channels)
                    ):
                        raise ValueError("v12_evaluation_stored_binding")
                    values.append(e)
                return tuple(values), now

    def evaluations(self, query: V12EvaluationQuery, actor: Principal) -> V12EvaluationPage:
        values, now = self._read(query, actor, status=query.quality_status)
        return projection(values, query, actor, now)

    def evaluation(
        self, identity: str, scope: CatalogScope, actor: Principal
    ) -> V12EvaluationDetail:
        values, now = self._read(scope, actor, identity=identity)
        if not values:
            raise EvaluationError(404, "evaluation-not-found")
        result = summary(values[0], now, detail=True)
        if not isinstance(result, V12EvaluationDetail):
            raise ValueError("v12_evaluation_detail_type")
        return result
