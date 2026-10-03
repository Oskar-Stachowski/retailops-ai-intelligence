"""Read-only, scoped v12 catalog and whole-campaign evaluation endpoints."""

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.model_catalog import list_query, scope_query
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Channel, Contract, Sha256, Symbol
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.model_lifecycle.evaluation_store import EvaluationError
from retailops_ai.model_lifecycle.read_contracts import CatalogQuery, CatalogScope
from retailops_ai.model_lifecycle.reader import CatalogError
from retailops_ai.model_lifecycle.v12_catalog import V12ModelCatalog
from retailops_ai.model_lifecycle.v12_evaluation_store import V12EvaluationReader
from retailops_ai.model_lifecycle.v12_metadata_contracts import (
    EvaluationID,
    V12CatalogModel,
    V12EvaluationDetail,
    V12EvaluationPage,
    V12EvaluationQuery,
    V12ModelPage,
    V12VersionPage,
)


def evaluation_query(
    request: Request,
    product_id: Annotated[Symbol | None, Query()] = None,
    selling_location_id: Annotated[Symbol | None, Query()] = None,
    channel: Annotated[Channel | None, Query()] = None,
    quality_status: Annotated[Literal["passed", "not_ready"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=256)] = 0,
    view_sha256: Annotated[Sha256 | None, Query()] = None,
) -> V12EvaluationQuery:
    if any(
        k not in V12EvaluationQuery.model_fields or len(request.query_params.getlist(k)) != 1
        for k in request.query_params
    ):
        raise HTTPException(422)
    return V12EvaluationQuery(
        product_id=product_id,
        selling_location_id=selling_location_id,
        channel=channel,
        quality_status=quality_status,
        limit=limit,
        offset=offset,
        view_sha256=view_sha256,
    )


def add_v12_metadata_routes(
    router: APIRouter,
    verified: Callable[..., object],
    catalog: V12ModelCatalog | None,
    evaluations: V12EvaluationReader | None,
) -> None:
    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "forecast:read" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    def call(
        action: Callable[[], Contract], *, evaluation: bool = False
    ) -> Contract | JSONResponse:
        if (evaluations if evaluation else catalog) is None:
            raise HTTPException(503)
        try:
            return action()
        except (CatalogError, EvaluationError, BatchError) as exc:
            return problem_response(exc.status, code=exc.code)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        except (ValueError, OverflowError, KeyError, TypeError):
            return problem_response(
                503, code="evaluation-evidence-invalid" if evaluation else "model-metadata-invalid"
            )

    responses: dict[int | str, dict[str, Any]] = {
        409: {"model": Problem},
        429: {"model": Problem},
        503: {"model": Problem},
    }

    @router.get("/models/v12", response_model=V12ModelPage, responses=responses)
    def models(
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[CatalogQuery, Depends(list_query)],
    ) -> Contract | JSONResponse:
        def action() -> Contract:
            if catalog is None:
                raise HTTPException(503)
            return catalog.models(query, principal)

        return call(action)

    @router.get("/models/v12/{model_name}", response_model=V12CatalogModel, responses=responses)
    def model(
        model_name: Symbol,
        principal: Annotated[Principal, Depends(reader)],
        scope: Annotated[CatalogScope, Depends(scope_query)],
    ) -> Contract | JSONResponse:
        def action() -> Contract:
            if catalog is None:
                raise HTTPException(503)
            return catalog.model(model_name, scope, principal)

        return call(action)

    @router.get(
        "/models/v12/{model_name}/versions", response_model=V12VersionPage, responses=responses
    )
    def versions(
        model_name: Symbol,
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[CatalogQuery, Depends(list_query)],
    ) -> Contract | JSONResponse:
        def action() -> Contract:
            if catalog is None:
                raise HTTPException(503)
            return catalog.versions(model_name, query, principal)

        return call(action)

    @router.get("/evaluations/v12", response_model=V12EvaluationPage, responses=responses)
    def reports(
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[V12EvaluationQuery, Depends(evaluation_query)],
    ) -> Contract | JSONResponse:
        def action() -> Contract:
            if evaluations is None:
                raise HTTPException(503)
            return evaluations.evaluations(query, principal)

        return call(action, evaluation=True)

    @router.get(
        "/evaluations/v12/{evaluation_id}", response_model=V12EvaluationDetail, responses=responses
    )
    def report(
        evaluation_id: EvaluationID,
        principal: Annotated[Principal, Depends(reader)],
        scope: Annotated[CatalogScope, Depends(scope_query)],
    ) -> Contract | JSONResponse:
        def action() -> Contract:
            if evaluations is None:
                raise HTTPException(503)
            return evaluations.evaluation(evaluation_id, scope, principal)

        return call(action, evaluation=True)
