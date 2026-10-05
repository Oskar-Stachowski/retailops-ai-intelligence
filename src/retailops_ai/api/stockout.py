"""Authenticated stockout intake and scoped reads; 202 follows a committed job."""

from collections.abc import Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.middleware import single_header
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Contract, RunID
from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.domain.access import Principal
from retailops_ai.stockout_jobs.errors import StockoutError
from retailops_ai.stockout_jobs.ports import StockoutAdministration, StockoutReader
from retailops_ai.stockout_jobs.public_contracts import StockoutJobRun, StockoutRequest
from retailops_ai.stockout_jobs.read_contracts import (
    RiskID,
    StockoutAttempts,
    StockoutQuery,
    StockoutRisk,
    StockoutRiskPage,
)

T = TypeVar("T", bound=Contract)


def add_stockout_routes(
    router: APIRouter,
    verified: Callable[..., object],
    administration: StockoutAdministration | None,
    reader: StockoutReader | None,
) -> None:
    async def pipeline(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "pipeline" not in principal.roles or "stockout:run" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    async def jobs(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if not {"stockout:read", "stockout:run"} & principal.capabilities:
            raise HTTPException(403)
        return principal

    async def risks(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "stockout:read" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    def required_admin(request: Request) -> StockoutAdministration:
        if request.query_params:
            raise HTTPException(422)
        if administration is None:
            raise HTTPException(503)
        return administration

    def required_reader() -> StockoutReader:
        if reader is None:
            raise HTTPException(503)
        return reader

    def call(action: Callable[[], T]) -> T | JSONResponse:
        try:
            return action()
        except StockoutError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            return problem_response(503)

    @router.post(
        "/stockout-runs",
        response_model=StockoutJobRun,
        status_code=202,
        responses={
            202: {"headers": {"Location": {"schema": {"type": "string"}}}},
            409: {"model": Problem},
            429: {"model": Problem},
            503: {"model": Problem},
        },
    )
    def submit(
        body: StockoutRequest,
        request: Request,
        response: Response,
        principal: Annotated[Principal, Depends(pipeline)],
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            ),
        ],
    ) -> StockoutJobRun | JSONResponse:
        if single_header(request.headers, "idempotency-key") != idempotency_key:
            raise HTTPException(422)
        result = call(lambda: required_admin(request).submit(body, principal, idempotency_key))
        if isinstance(result, StockoutJobRun):
            response.headers["Location"] = "/api/v1/stockout-runs/" + result.run_id
        return result

    @router.get(
        "/stockout-runs/{run_id}",
        response_model=StockoutJobRun,
        responses={503: {"model": Problem}},
    )
    def get_run(
        run_id: RunID, request: Request, principal: Annotated[Principal, Depends(jobs)]
    ) -> StockoutJobRun | JSONResponse:
        return call(lambda: required_admin(request).get(run_id, principal))

    @router.get(
        "/stockout-runs/{run_id}/attempts",
        response_model=StockoutAttempts,
        responses={503: {"model": Problem}},
    )
    def attempts(
        run_id: RunID, request: Request, principal: Annotated[Principal, Depends(jobs)]
    ) -> StockoutAttempts | JSONResponse:
        return call(lambda: required_admin(request).attempts(run_id, principal))

    @router.get(
        "/stockout-risks",
        response_model=StockoutRiskPage,
        responses={409: {"model": Problem}, 503: {"model": Problem}},
    )
    def list_risks(
        request: Request, principal: Annotated[Principal, Depends(risks)]
    ) -> StockoutRiskPage | JSONResponse:
        raw: dict[str, str | int] = {}
        allowed = set(StockoutQuery.model_fields)
        for name, value in request.query_params.multi_items():
            if name not in allowed or name in raw:
                raise HTTPException(422)
            if name in {"limit", "offset"}:
                if not value.isascii() or not value.isdigit() or len(value) > 5:
                    raise HTTPException(422)
                raw[name] = int(value)
            else:
                raw[name] = value
        try:
            query = StockoutQuery.model_validate_json(canonical_bytes(raw))
        except ValueError:
            raise HTTPException(422) from None
        return call(lambda: required_reader().list(query, principal))

    @router.get(
        "/stockout-risks/{risk_id}",
        response_model=StockoutRisk,
        responses={503: {"model": Problem}},
    )
    def get_risk(
        risk_id: RiskID, request: Request, principal: Annotated[Principal, Depends(risks)]
    ) -> StockoutRisk | JSONResponse:
        if request.query_params:
            raise HTTPException(422)
        return call(lambda: required_reader().get(risk_id, principal))
