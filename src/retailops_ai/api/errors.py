"""Problem details with static messages and no reflected URLs or input values."""

from http import HTTPStatus
from uuid import UUID

from starlette.responses import JSONResponse

from retailops_ai.adapters.telemetry import CORRELATION_ID
from retailops_ai.api.models import Problem, Ready
from retailops_ai.forecast_jobs.contracts import BatchErrorCode
from retailops_ai.forecast_jobs.read_contracts import ReadErrorCode
from retailops_ai.knowledge.jobs import IndexErrorCode
from retailops_ai.model_lifecycle.read_contracts import CatalogErrorCode

DETAILS = {
    400: "The request cannot be accepted.",
    401: "Valid credentials are required.",
    403: "Access is forbidden.",
    404: "The resource is not available.",
    405: "The method is not allowed.",
    413: "The request body exceeds the allowed size.",
    408: "The request body did not arrive in time.",
    409: "The request conflicts with the recorded state.",
    422: "The request does not match the expected schema.",
    429: "The request limit was exceeded.",
    500: "An internal error occurred.",
    503: "A required dependency is unavailable.",
}


def problem_response(
    status: int,
    *,
    readiness: Ready | None = None,
    headers: dict[str, str] | None = None,
    code: IndexErrorCode | BatchErrorCode | ReadErrorCode | CatalogErrorCode | None = None,
) -> JSONResponse:
    current = CORRELATION_ID.get()
    if current is None:
        raise RuntimeError("problem responses require request context")
    try:
        title = HTTPStatus(status).phrase
    except ValueError:
        title = "Request failed"
    problem = Problem(
        title=title,
        status=status,
        detail=DETAILS.get(status, "The request could not be completed."),
        instance=f"urn:uuid:{current}",
        correlation_id=UUID(current),
        readiness=readiness,
        code=code,
    )
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )
