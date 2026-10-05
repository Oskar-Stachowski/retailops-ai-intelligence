"""Immutable public input registration; HTTP accepts existing IDs, never rows."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import Connection, Engine, text

from retailops_ai.adapters.database import EXPECTED_REVISION
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import clock
from retailops_ai.stockout_jobs.contracts import StockoutErrorCode
from retailops_ai.stockout_runtime.inputs import (
    MAX_INPUT_BYTES,
    PhysicalScope,
    PreparedStockoutInputs,
    prepare_inputs,
)


class StockoutError(ValueError):
    def __init__(self, status: int, code: StockoutErrorCode) -> None:
        super().__init__(code)
        self.status, self.code = status, code


def checked(connection: Connection) -> datetime:
    connection.execute(text("SET LOCAL statement_timeout='10s'"))
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    if connection.scalar(text("SELECT version_num FROM ai.alembic_version")) != EXPECTED_REVISION:
        raise StockoutError(503, "stockout-database-not-ready")
    return clock(connection)


def authorize_scope(
    scope: PhysicalScope, principal: Principal, *, reading: bool = False, owned: bool = False
) -> None:
    access = principal.stockout
    permitted = (
        "stockout:read" in principal.capabilities
        if reading
        else "pipeline" in principal.roles and "stockout:run" in principal.capabilities
    )
    if reading and owned and "stockout:run" in principal.capabilities:
        permitted = True
    if (
        not permitted
        or access is None
        or not set(scope.product_ids) <= access.product_ids
        or not set(scope.stock_location_ids) <= access.stock_location_ids
    ):
        raise StockoutError(
            404 if reading else 403,
            "stockout-run-not-found" if reading else "stockout-scope-denied",
        )


def load_inputs(
    connection: Connection, environment: str, profile_id: str
) -> PreparedStockoutInputs:
    raw = connection.scalar(
        text(
            "SELECT inputs FROM ai.stockout_prepared_inputs WHERE environment=:env AND inputs_id=:id"
        ),
        dict(env=environment, id=profile_id),
    )
    if raw is None:
        raise StockoutError(422, "stockout-input-not-prepared")
    return PreparedStockoutInputs.model_validate_json(json.dumps(raw))


def register_public_inputs(
    engine: Engine,
    environment: Literal["local", "test"],
    *,
    curated: Path,
    features: Path,
    upstream: Path,
    scope: PhysicalScope,
    as_of: datetime,
    principal: Principal,
) -> PreparedStockoutInputs:
    if environment not in {"local", "test"}:
        raise ValueError("stockout_input_environment")
    scope = PhysicalScope.model_validate_json(scope.model_dump_json())
    authorize_scope(scope, principal)
    inputs = prepare_inputs(curated, features, upstream, scope=scope, as_of=as_of)
    raw = canonical_bytes(inputs.model_dump(mode="json"))
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("stockout_input_byte_limit")
    with engine.begin() as connection:
        now = checked(connection)
        if inputs.as_of > now:
            raise StockoutError(422, "stockout-input-from-future")
        # Fixed bounded registry capacity, shared between own registrations.
        connection.execute(text("SELECT pg_advisory_xact_lock(508080)"))
        old = connection.scalar(
            text(
                "SELECT inputs FROM ai.stockout_prepared_inputs WHERE environment=:env AND inputs_id=:id"
            ),
            dict(env=environment, id=inputs.inputs_id),
        )
        if old is not None:
            if PreparedStockoutInputs.model_validate_json(json.dumps(old)) != inputs:
                raise ValueError("stockout_input_identity_conflict")
            return inputs
        size = connection.execute(
            text(
                "SELECT count(*) AS count,coalesce(sum(octet_length(inputs::text)),0) AS bytes FROM ai.stockout_prepared_inputs"
            )
        ).one()
        if size[0] >= 32 or size[1] + len(raw) > 128 * 1024**2:
            raise ValueError("stockout_input_registry_capacity")
        connection.execute(
            text(
                "INSERT INTO ai.stockout_prepared_inputs(environment,inputs_id,registered_by,inputs) VALUES (:env,:id,:principal,CAST(:inputs AS jsonb))"
            ),
            dict(
                env=environment,
                id=inputs.inputs_id,
                principal=principal.principal_id,
                inputs=raw.decode(),
            ),
        )
    return inputs
