"""Administrative submission/read endpoints; activation is intentionally outside HTTP."""

import re
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from retailops_ai.adapters.index_jobs import IndexAdministration, IndexJobError
from retailops_ai.api.errors import problem_response
from retailops_ai.api.middleware import single_header
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import RunID
from retailops_ai.data_contracts.run import RunRecord
from retailops_ai.domain.access import Principal
from retailops_ai.knowledge.jobs import CurrentKnowledgeIndex, KnowledgeIndexRequest


def add_index_routes(
    router: APIRouter, verified: Callable[..., object], backend: IndexAdministration | None
) -> None:
    async def administrator(principal: Annotated[Principal, Depends(verified)]) -> Principal:
        if "admin" not in principal.roles or "knowledge:index" not in principal.capabilities:
            raise HTTPException(403)
        return principal

    def required_backend() -> IndexAdministration:
        if backend is None:
            raise HTTPException(503)
        return backend

    @router.post(
        "/knowledge-index-runs",
        response_model=RunRecord,
        status_code=202,
        responses={
            202: {
                "headers": {
                    "Location": {
                        "schema": {"type": "string"},
                        "description": "Relative URL of the durable run.",
                    }
                }
            },
            409: {"model": Problem},
            429: {"model": Problem},
            503: {"model": Problem},
        },
    )
    def submit(
        body: KnowledgeIndexRequest,
        request: Request,
        response: Response,
        principal: Annotated[Principal, Depends(administrator)],
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
                pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
            ),
        ],
    ) -> RunRecord | JSONResponse:
        key = single_header(request.headers, "idempotency-key")
        if (
            key != idempotency_key
            or key is None
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", key) is None
        ):
            raise HTTPException(422)
        try:
            run = required_backend().submit(body, principal.principal_id, key)
        except IndexJobError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None
        response.headers["Location"] = "/api/v1/knowledge-index-runs/" + run.run_id
        return run

    @router.get(
        "/knowledge-index-runs/{run_id}",
        response_model=RunRecord,
        responses={503: {"model": Problem}},
    )
    def get_run(
        run_id: RunID, principal: Annotated[Principal, Depends(administrator)]
    ) -> RunRecord | JSONResponse:
        try:
            return required_backend().get(run_id)
        except IndexJobError as exc:
            return problem_response(exc.status, code=exc.code)
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None

    @router.get(
        "/knowledge-indexes/current",
        response_model=CurrentKnowledgeIndex,
        responses={503: {"model": Problem}},
    )
    def current_index(
        principal: Annotated[Principal, Depends(administrator)],
    ) -> CurrentKnowledgeIndex | JSONResponse:
        try:
            current = required_backend().current()
            if current is None:
                return problem_response(404, code="index-not-configured")
            return current
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None
