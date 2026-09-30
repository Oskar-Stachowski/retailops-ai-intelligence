"""Scoped published forecasts; unavailable intervals remain explicitly absent."""

import re
from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Channel, RunID, Sha256, Symbol, UtcTime
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.forecast_jobs.read_contracts import ForecastPage, ForecastQuery
from retailops_ai.forecast_jobs.reader import ForecastReader, ForecastReadError


def add_forecast_read_routes(
    router: APIRouter,
    verified: Callable[..., object],
    backend: ForecastReader | None,
) -> None:
    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "forecast:read" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    @router.get(
        "/forecasts",
        response_model=ForecastPage,
        responses={409: {"model": Problem}, 429: {"model": Problem}, 503: {"model": Problem}},
    )
    def forecasts(
        request: Request,
        principal: Annotated[Principal, Depends(reader)],
        product_id: Annotated[Symbol | None, Query()] = None,
        selling_location_id: Annotated[Symbol | None, Query()] = None,
        channel: Annotated[Channel | None, Query()] = None,
        target_from: Annotated[date | None, Query()] = None,
        target_to: Annotated[date | None, Query()] = None,
        as_of: Annotated[UtcTime | None, Query()] = None,
        inference_run_id: Annotated[RunID | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2800)] = 0,
        view_sha256: Annotated[Sha256 | None, Query()] = None,
    ) -> ForecastPage | JSONResponse:
        if any(
            k not in ForecastQuery.model_fields or len(request.query_params.getlist(k)) != 1
            for k in request.query_params
        ):
            raise HTTPException(422)
        if any(
            re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", request.query_params[k]) is None
            for k in ("target_from", "target_to")
            if k in request.query_params
        ):
            raise HTTPException(422)
        try:
            query = ForecastQuery(
                product_id=product_id,
                selling_location_id=selling_location_id,
                channel=channel,
                target_from=target_from,
                target_to=target_to,
                as_of=as_of,
                inference_run_id=inference_run_id,
                limit=limit,
                offset=offset,
                view_sha256=view_sha256,
            )
        except ValidationError:
            raise HTTPException(422) from None
        if backend is None:
            raise HTTPException(503)
        try:
            return backend.read(query, principal)
        except ForecastReadError as exc:
            return problem_response(exc.status, code=exc.code)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        except (ValueError, OverflowError):
            return problem_response(503, code="forecast-output-invalid")
