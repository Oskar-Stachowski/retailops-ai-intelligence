"""Public metadata is limited to models with a publication in the verified scope."""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.api.errors import problem_response
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Channel, Sha256, Symbol
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.queue import BatchError
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogModel,
    CatalogQuery,
    CatalogScope,
    ModelPage,
    VersionPage,
)
from retailops_ai.model_lifecycle.reader import CatalogError, ModelCatalog


def scope_query(
    request: Request,
    product_id: Annotated[Symbol | None, Query()] = None,
    selling_location_id: Annotated[Symbol | None, Query()] = None,
    channel: Annotated[Channel | None, Query()] = None,
) -> CatalogScope:
    if any(
        k not in CatalogScope.model_fields or len(request.query_params.getlist(k)) != 1
        for k in request.query_params
    ):
        raise HTTPException(422)
    return CatalogScope(
        product_id=product_id, selling_location_id=selling_location_id, channel=channel
    )


def list_query(
    request: Request,
    product_id: Annotated[Symbol | None, Query()] = None,
    selling_location_id: Annotated[Symbol | None, Query()] = None,
    channel: Annotated[Channel | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
    view_sha256: Annotated[Sha256 | None, Query()] = None,
) -> CatalogQuery:
    if any(
        k not in CatalogQuery.model_fields or len(request.query_params.getlist(k)) != 1
        for k in request.query_params
    ):
        raise HTTPException(422)
    return CatalogQuery(
        product_id=product_id,
        selling_location_id=selling_location_id,
        channel=channel,
        limit=limit,
        offset=offset,
        view_sha256=view_sha256,
    )


def add_model_catalog_routes(
    router: APIRouter, verified: Callable[..., object], backend: ModelCatalog | None
) -> None:
    async def reader(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if not {"forecast:read", "anomaly:read"} & principal.capabilities:
            raise HTTPException(403)
        return principal

    def call(
        action: Callable[[ModelCatalog], CatalogModel | ModelPage | VersionPage],
    ) -> CatalogModel | ModelPage | VersionPage | JSONResponse:
        if backend is None:
            raise HTTPException(503)
        try:
            return action(backend)
        except CatalogError as exc:
            return problem_response(exc.status, code=exc.code)
        except BatchError as exc:
            return problem_response(exc.status, code=exc.code)
        except SQLAlchemyError:
            raise HTTPException(503) from None
        except (ValueError, OverflowError):
            return problem_response(503, code="model-metadata-invalid")

    responses: dict[int | str, dict[str, Any]] = {
        409: {"model": Problem},
        429: {"model": Problem},
        503: {"model": Problem},
    }

    @router.get("/models", response_model=ModelPage, responses=responses)
    def models(
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[CatalogQuery, Depends(list_query)],
    ) -> CatalogModel | ModelPage | VersionPage | JSONResponse:
        return call(lambda b: b.models(query, principal))

    @router.get(
        "/models/{model_name}",
        response_model=CatalogModel,
        responses={429: {"model": Problem}, 503: {"model": Problem}},
    )
    def model(
        model_name: Symbol,
        principal: Annotated[Principal, Depends(reader)],
        scope: Annotated[CatalogScope, Depends(scope_query)],
    ) -> CatalogModel | ModelPage | VersionPage | JSONResponse:
        return call(lambda b: b.model(model_name, scope, principal))

    @router.get("/models/{model_name}/versions", response_model=VersionPage, responses=responses)
    def versions(
        model_name: Symbol,
        principal: Annotated[Principal, Depends(reader)],
        query: Annotated[CatalogQuery, Depends(list_query)],
    ) -> CatalogModel | ModelPage | VersionPage | JSONResponse:
        return call(lambda b: b.versions(model_name, query, principal))
