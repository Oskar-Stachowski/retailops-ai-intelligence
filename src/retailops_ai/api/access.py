"""Protected local identity, whole-scope preflight and safe administration metadata."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from retailops_ai.adapters.knowledge_search import KnowledgeBackend
from retailops_ai.api.middleware import single_header
from retailops_ai.api.models import Problem
from retailops_ai.data_contracts.common import Contract, Symbol, Versioned
from retailops_ai.domain.access import Capability, Principal, Role, can_read_forecast
from retailops_ai.knowledge.retrieval import RetrievalRequest, RetrievalResult
from retailops_ai.pipelines.retrieval import KnowledgeDenied, resolve_scope
from retailops_ai.security.local import LocalAccess
from retailops_ai.security.models import KnowledgeResourceScope, ResourceScope


class IdentityResponse(Contract):
    schema_version: Literal["1.0"] = "1.0"
    principal_id: Symbol
    roles: list[Role]
    capabilities: list[Capability]
    scope: ResourceScope | None
    knowledge_scope: KnowledgeResourceScope | None = None


class ForecastCheckRequest(Versioned):
    product_ids: list[Symbol] = Field(min_length=1, max_length=20)
    selling_location_ids: list[Symbol] = Field(min_length=1, max_length=5)
    channel: Literal["store", "online"]


class AccessDecision(Contract):
    schema_version: Literal["1.0"] = "1.0"
    principal_id: Symbol
    capability: Literal["forecast:read"] = "forecast:read"
    allowed: Literal[True] = True
    scope: ForecastCheckRequest


class PolicyMetadata(Contract):
    schema_version: Literal["1.0"] = "1.0"
    policy_id: Symbol
    principal_count: int = Field(ge=1, le=32)
    credential_count: int = Field(ge=1, le=64)


def access_router(
    authority: LocalAccess,
    knowledge_backend: KnowledgeBackend | None = None,
    environment: Literal["local", "test"] = "local",
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
        prefix="/api/v1",
        dependencies=[Depends(verified)],
        responses={
            401: {"model": Problem},
            403: {"model": Problem},
            413: {"model": Problem},
            408: {"model": Problem},
        },
    )

    @router.get("/identity", response_model=IdentityResponse)
    async def identity(principal: Annotated[Principal, Depends(verified)]) -> IdentityResponse:
        scope = (
            ResourceScope(
                product_ids=sorted(principal.product_ids),
                selling_location_ids=sorted(principal.selling_location_ids),
                channels=sorted(principal.channels),
            )
            if "forecast:read" in principal.capabilities
            else None
        )
        return IdentityResponse(
            principal_id=principal.principal_id,
            roles=sorted(principal.roles),
            capabilities=sorted(principal.capabilities),
            scope=scope,
            knowledge_scope=KnowledgeResourceScope.model_validate(
                {
                    "environment": principal.knowledge.environment,
                    "repositories": sorted(principal.knowledge.repositories),
                    "access_classes": sorted(principal.knowledge.access_classes),
                    "document_statuses": sorted(principal.knowledge.document_statuses),
                }
            )
            if principal.knowledge
            else None,
        )

    @router.post("/access/forecast-check", response_model=AccessDecision)
    async def forecast_check(
        body: ForecastCheckRequest,
        principal: Annotated[Principal, Depends(verified)],
    ) -> AccessDecision:
        if len(set(body.product_ids)) != len(body.product_ids) or len(
            set(body.selling_location_ids)
        ) != len(body.selling_location_ids):
            raise HTTPException(422)
        if not can_read_forecast(
            principal,
            products=set(body.product_ids),
            locations=set(body.selling_location_ids),
            channel=body.channel,
        ):
            raise HTTPException(403)
        return AccessDecision(principal_id=principal.principal_id, scope=body)

    @router.get("/admin/access-policy", response_model=PolicyMetadata)
    async def policy_metadata(principal: Annotated[Principal, Depends(verified)]) -> PolicyMetadata:
        if "access:admin" not in principal.capabilities:
            raise HTTPException(403)
        policy_id, principals, credentials = authority.policy_metadata()
        return PolicyMetadata(
            policy_id=policy_id, principal_count=principals, credential_count=credentials
        )

    @router.post(
        "/knowledge/search", response_model=RetrievalResult, responses={503: {"model": Problem}}
    )
    def knowledge_search(
        body: RetrievalRequest, principal: Annotated[Principal, Depends(verified)]
    ) -> RetrievalResult:
        try:
            resolve_scope(principal, body, environment)
            if knowledge_backend is None:
                raise HTTPException(503)
            return knowledge_backend.search(body, principal)
        except KnowledgeDenied:
            raise HTTPException(403) from None
        except (SQLAlchemyError, ValueError, OverflowError):
            raise HTTPException(503) from None

    return router
