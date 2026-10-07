"""Protected synchronous bounded queries and owner/admin safe run metadata."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.adapters.telemetry import CORRELATION_ID
from retailops_ai.api.errors import problem_response
from retailops_ai.api.middleware import single_header
from retailops_ai.api.models import Problem
from retailops_ai.assistant.contracts import AssistantAnswer, AssistantQuery, AssistantRun
from retailops_ai.assistant.service import (
    AssistantError,
    AssistantService,
    AssistantStore,
    authorized,
)
from retailops_ai.domain.access import Principal
from retailops_ai.security.local import LocalAccess


def assistant_router(
    authority: LocalAccess, service: AssistantService | None, store: AssistantStore | None
) -> APIRouter:
    bearer = HTTPBearer(auto_error=False, scheme_name="apiBearer")

    async def verified(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    ) -> Principal:
        principal = authority.authenticate(single_header(request.headers, "authorization"))
        if principal is None:
            raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})
        return principal

    router = APIRouter(
        prefix="/api/v1/assistant",
        dependencies=[Depends(verified)],
        responses={
            code: {"model": Problem}
            for code in (401, 403, 404, 408, 413, 422, 424, 429, 500, 502, 503, 504)
        },
    )

    @router.post("/queries", response_model=AssistantAnswer)
    async def query(
        body: AssistantQuery, request: Request, principal: Annotated[Principal, Depends(verified)]
    ) -> AssistantAnswer | JSONResponse:
        if not authorized(principal, body):
            return problem_response(403)
        if service is None:
            return problem_response(503)
        try:
            return await service.query(
                body,
                principal,
                single_header(request.headers, "authorization"),
                correlation_id=UUID(CORRELATION_ID.get() or ""),
            )
        except AssistantError as exc:
            return problem_response(
                exc.status,
                problem_type="urn:retailops:problem:dependency-unavailable"
                if exc.status == 424
                else "about:blank",
            )
        except SQLAlchemyError:
            return problem_response(503)

    @router.get("/runs/{trace_id}", response_model=AssistantRun)
    async def get_run(
        trace_id: UUID, principal: Annotated[Principal, Depends(verified)]
    ) -> AssistantRun | JSONResponse:
        if store is None:
            return problem_response(404)
        try:
            run = await store.get(trace_id, principal)
        except SQLAlchemyError:
            return problem_response(503)
        if run is None:
            return problem_response(404)
        return run

    return router
