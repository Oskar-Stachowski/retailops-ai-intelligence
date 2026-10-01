"""Authenticated v12 intake/status; only a separate worker computes and publishes."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.middleware import single_header
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import RunID
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.contracts import BatchRequest
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.forecast_jobs.reader import ForecastReadError
from retailops_ai.forecast_jobs.v12_administration import V12JobAdministration
from retailops_ai.forecast_jobs.v12_job_contracts import V12JobAttempts, V12JobRun

PATH = "/forecast-runs/v12"


def add_v12_forecast_routes(
    router: APIRouter, verified: Callable[..., object], backend: V12JobAdministration | None
) -> None:
    def required(request: Request) -> V12JobAdministration:
        if request.query_params:
            raise HTTPException(422)
        if backend is None:
            raise HTTPException(503)
        return backend

    async def pipeline(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "pipeline" not in principal.roles or "forecast:run" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if not {"forecast:read", "forecast:run"} & principal.capabilities:
            raise HTTPException(403)
        return principal

    def call(
        action: Callable[[], V12JobRun | V12JobAttempts],
    ) -> V12JobRun | V12JobAttempts | JSONResponse:
        try:
            return action()
        except (BatchError, ForecastReadError) as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            return problem_response(503)

    @router.post(
        PATH,
        response_model=V12JobRun,
        status_code=202,
        responses={
            202: {"headers": {"Location": {"schema": {"type": "string"}}}},
            409: {"model": Problem},
            429: {"model": Problem},
            503: {"model": Problem},
        },
    )
    def submit(
        body: BatchRequest,
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
    ) -> V12JobRun | V12JobAttempts | JSONResponse:
        if single_header(request.headers, "idempotency-key") != idempotency_key:
            raise HTTPException(422)
        result = call(lambda: required(request).submit(body, principal, idempotency_key))
        if isinstance(result, V12JobRun):
            response.headers["Location"] = "/api/v1" + PATH + "/" + result.run_id
        return result

    @router.get(
        PATH + "/{run_id}",
        response_model=V12JobRun,
        responses={503: {"model": Problem}, 429: {"model": Problem}},
    )
    def get_run(
        run_id: RunID, request: Request, principal: Annotated[Principal, Depends(reader)]
    ) -> V12JobRun | V12JobAttempts | JSONResponse:
        return call(lambda: required(request).get(run_id, principal))

    @router.get(
        PATH + "/{run_id}/attempts",
        response_model=V12JobAttempts,
        responses={503: {"model": Problem}, 429: {"model": Problem}},
    )
    def attempts(
        run_id: RunID, request: Request, principal: Annotated[Principal, Depends(reader)]
    ) -> V12JobRun | V12JobAttempts | JSONResponse:
        return call(lambda: required(request).attempts(run_id, principal))
