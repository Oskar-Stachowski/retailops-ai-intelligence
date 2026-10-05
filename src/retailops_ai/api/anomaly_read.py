"""Authenticated persisted anomaly reads; no HTTP model decisions or scoring side effects."""

import re
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Query as Param
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.anomaly_detectors.protocol import EventType
from retailops_ai.anomaly_portfolio.result_store import Reader, ReadError
from retailops_ai.anomaly_portfolio.serving_contract import (
    AnomalyID,
    AnomalyType,
    BatchID,
    Item,
    Page,
    Query,
)
from retailops_ai.api.errors import problem_response
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Sha256, Symbol
from retailops_ai.domain.access import Principal


def add_anomaly_read_routes(
    router: APIRouter, verified: Callable[..., object], backend: Reader | None
) -> None:
    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "anomaly:read" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    def call(query: Query, principal: Principal) -> Page | JSONResponse:
        if backend is None:
            raise HTTPException(503)
        try:
            return backend.read(query, principal)
        except ReadError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            return problem_response(503, code="anomaly-output-unavailable")

    def parameters(
        request: Request,
        product_id: Annotated[Symbol | None, Param()] = None,
        selling_location_id: Annotated[Symbol | None, Param()] = None,
        channel: Annotated[
            Literal["store", "online", "marketplace", "wholesale"] | None, Param()
        ] = None,
        event_type: Annotated[EventType | None, Param()] = None,
        business_from: Annotated[date | None, Param()] = None,
        business_to: Annotated[date | None, Param()] = None,
        batch_id: Annotated[BatchID | None, Param()] = None,
        status: Annotated[Literal["scored", "insufficient_data"] | None, Param()] = None,
        severity: Annotated[Literal["none", "medium", "high"] | None, Param()] = None,
        anomaly_type: Annotated[AnomalyType | None, Param()] = None,
        limit: Annotated[int, Param(ge=1, le=200)] = 50,
        offset: Annotated[int, Param(ge=0, le=10000)] = 0,
        view_sha256: Annotated[Sha256 | None, Param()] = None,
    ) -> Query:
        if any(
            k not in Query.model_fields
            or k == "anomaly_id"
            or len(request.query_params.getlist(k)) != 1
            for k in request.query_params
        ):
            raise HTTPException(422)
        if any(
            re.fullmatch(r"\d{4}-\d{2}-\d{2}", request.query_params[k]) is None
            for k in ("business_from", "business_to")
            if k in request.query_params
        ):
            raise HTTPException(422)
        try:
            return Query(
                product_id=product_id,
                selling_location_id=selling_location_id,
                channel=channel,
                event_type=event_type,
                business_from=business_from,
                business_to=business_to,
                batch_id=batch_id,
                status=status,
                severity=severity,
                anomaly_type=anomaly_type,
                limit=limit,
                offset=offset,
                view_sha256=view_sha256,
            )
        except ValidationError:
            raise HTTPException(422) from None

    @router.get(
        "/anomalies",
        response_model=Page,
        responses={409: {"model": Problem}, 429: {"model": Problem}, 503: {"model": Problem}},
    )
    def anomalies(
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[Query, Depends(parameters)],
    ) -> Page | JSONResponse:
        return call(query, principal)

    @router.get(
        "/anomalies/{anomaly_id}",
        response_model=Item,
        responses={404: {"model": Problem}, 503: {"model": Problem}},
    )
    def anomaly(
        request: Request, anomaly_id: AnomalyID, principal: Annotated[Principal, Depends(reader)]
    ) -> Item | JSONResponse:
        if request.query_params:
            raise HTTPException(422)
        result = call(Query(anomaly_id=anomaly_id, limit=1), principal)
        if isinstance(result, JSONResponse):
            return result
        if len(result.items) != 1:
            return problem_response(404, code="anomaly-not-found")
        return result.items[0]
