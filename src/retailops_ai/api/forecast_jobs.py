"""Durable intake and scoped status; private workers own execution and retries."""

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
from retailops_ai.forecast_jobs.contracts import BatchRequest, BatchRun
from retailops_ai.forecast_jobs.queue import BatchAdministration, BatchError


def add_forecast_routes(
    router: APIRouter, verified: Callable[..., object], backend: BatchAdministration | None
) -> None:
    def required() -> BatchAdministration:
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

    @router.post(
        "/forecast-runs",
        response_model=BatchRun,
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
    ) -> BatchRun | JSONResponse:
        if single_header(request.headers, "idempotency-key") != idempotency_key:
            raise HTTPException(422)
        try:
            run = required().submit(body, principal, idempotency_key)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None
        response.headers["Location"] = "/api/v1/forecast-runs/" + run.run_id
        return run

    @router.get(
        "/forecast-runs/{run_id}", response_model=BatchRun, responses={503: {"model": Problem}}
    )
    def get_run(
        run_id: RunID, principal: Annotated[Principal, Depends(reader)]
    ) -> BatchRun | JSONResponse:
        try:
            return required().get(run_id, principal)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None

    @router.get(
        "/forecast-runs/{run_id}/attempts",
        response_model=list[BatchRun],
        responses={503: {"model": Problem}},
    )
    def attempts(
        run_id: RunID, principal: Annotated[Principal, Depends(reader)]
    ) -> list[BatchRun] | JSONResponse:
        try:
            return required().attempts(run_id, principal)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None
