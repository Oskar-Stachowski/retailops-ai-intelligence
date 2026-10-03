"""Read-only development reports require access to every entity included in the report."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.model_catalog import scope_query
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Channel, Sha256, Symbol
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.forecasting.quality_contract import QualityStatus
from retailops_ai.model_lifecycle.evaluation_contracts import (
    EvaluationDetail,
    EvaluationID,
    EvaluationPage,
    EvaluationQuery,
)
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError, EvaluationReader
from retailops_ai.model_lifecycle.read_contracts import CatalogScope


def list_query(
    request: Request,
    product_id: Annotated[Symbol | None, Query()] = None,
    selling_location_id: Annotated[Symbol | None, Query()] = None,
    channel: Annotated[Channel | None, Query()] = None,
    quality_status: Annotated[QualityStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=256)] = 0,
    view_sha256: Annotated[Sha256 | None, Query()] = None,
) -> EvaluationQuery:
    if any(
        k not in EvaluationQuery.model_fields or len(request.query_params.getlist(k)) != 1
        for k in request.query_params
    ):
        raise HTTPException(422)
    return EvaluationQuery(
        product_id=product_id,
        selling_location_id=selling_location_id,
        channel=channel,
        quality_status=quality_status,
        limit=limit,
        offset=offset,
        view_sha256=view_sha256,
    )


def add_evaluation_routes(
    router: APIRouter, verified: Callable[..., object], backend: EvaluationReader | None
) -> None:
    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "forecast:read" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    def call(
        action: Callable[[EvaluationReader], EvaluationPage | EvaluationDetail],
    ) -> EvaluationPage | EvaluationDetail | JSONResponse:
        if backend is None:
            raise HTTPException(503)
        try:
            return action(backend)
        except EvaluationError as exc:
            return problem_response(exc.status, code=exc.code)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        except (ValueError, OverflowError):
            return problem_response(503, code="evaluation-evidence-invalid")

    responses: dict[int | str, dict[str, Any]] = {
        409: {"model": Problem},
        429: {"model": Problem},
        503: {"model": Problem},
    }

    @router.get("/evaluations", response_model=EvaluationPage, responses=responses)
    def evaluations(
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[EvaluationQuery, Depends(list_query)],
    ) -> EvaluationPage | EvaluationDetail | JSONResponse:
        return call(lambda b: b.evaluations(query, principal))

    @router.get(
        "/evaluations/{evaluation_id}",
        response_model=EvaluationDetail,
        responses={503: {"model": Problem}},
    )
    def evaluation(
        evaluation_id: EvaluationID,
        principal: Annotated[Principal, Depends(reader)],
        scope: Annotated[CatalogScope, Depends(scope_query)],
    ) -> EvaluationPage | EvaluationDetail | JSONResponse:
        return call(lambda b: b.evaluation(evaluation_id, scope, principal))
