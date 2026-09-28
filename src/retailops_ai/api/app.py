"""Compose role wiring and diagnostic HTTP; no model serving is implied."""

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from opentelemetry.trace import Tracer
from prometheus_client import CONTENT_TYPE_LATEST
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from retailops_ai.adapters.database import DatabaseProbe, database_engine
from retailops_ai.adapters.knowledge_search import KnowledgeBackend, PostgresKnowledge
from retailops_ai.adapters.telemetry import HttpMetrics, new_tracer
from retailops_ai.adapters.vector_store import index_engine
from retailops_ai.api.access import access_router
from retailops_ai.api.errors import problem_response
from retailops_ai.api.middleware import HttpObservation, single_header
from retailops_ai.api.models import DependencyStatus, Health, Problem, Ready, ServiceVersion
from retailops_ai.config import Settings
from retailops_ai.domain.readiness import Dependency
from retailops_ai.pipelines.readiness import Readiness
from retailops_ai.pipelines.retrieval import load_retrieval_config
from retailops_ai.security.local import load_authority


class DiagnosticAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        # Runtime uses problem+json for all errors; publish the same media type.
        for path in schema["paths"].values():
            for operation in path.values():
                if not isinstance(operation, dict):
                    continue
                for status, response in operation.get("responses", {}).items():
                    if status.isdigit() and int(status) >= 400:
                        content = response.get("content", {})
                        if "application/json" in content:
                            content["application/problem+json"] = content.pop("application/json")
        return schema


def create_app(
    settings: Settings,
    *,
    dependencies: tuple[Dependency, ...] = (),
    tracer: Tracer | None = None,
    knowledge_backend: KnowledgeBackend | None = None,
) -> FastAPI:
    if any(d.name in {"startup", "ai_db"} for d in dependencies):
        raise ValueError("startup and ai_db are reserved dependency names")
    authority = load_authority(
        settings.api_auth_file,
        settings.metrics_token.get_secret_value() if settings.metrics_token else None,
    )
    engine = database_engine(settings) if settings.database_url is not None else None
    knowledge_engine = None
    if knowledge_backend is None and settings.database_url is not None:
        knowledge_engine = index_engine(settings)
        knowledge_backend = PostgresKnowledge(
            knowledge_engine,
            settings.app_env,
            load_retrieval_config(
                Path(__file__).resolve().parents[1] / "knowledge/retrieval.default.json"
            ),
        )
    if engine is not None:
        dependencies = (*dependencies, Dependency("ai_db", DatabaseProbe(engine).check))
    readiness = Readiness(dependencies, settings.readiness_timeout_seconds)
    metrics = HttpMetrics()
    started = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal started
        started = True
        logger = logging.getLogger("retailops_ai.http")
        logger.info("application_started", extra={"event_data": {"event": "application_started"}})
        try:
            yield
        finally:
            started = False
            if engine is not None:
                await engine.dispose()
            if knowledge_engine is not None:
                knowledge_engine.dispose()
            logger.info(
                "application_stopped", extra={"event_data": {"event": "application_stopped"}}
            )

    info = ServiceVersion(
        version=version("retailops-ai-intelligence"),
        build_commit=settings.build_commit,
        image_digest=settings.image_digest,
    )
    app = DiagnosticAPI(
        title="RetailOps AI diagnostics",
        version=info.version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        responses={
            400: {"model": Problem},
            404: {"model": Problem},
            405: {"model": Problem},
            422: {"model": Problem},
            500: {"model": Problem},
        },
    )
    app.add_middleware(
        HttpObservation,
        metrics=metrics,
        tracer=tracer or new_tracer(),
        compose=settings.network_mode == "compose",
    )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        headers = None
        if exc.status_code == 405 and exc.headers and "Allow" in exc.headers:
            headers = {"Allow": exc.headers["Allow"]}
        elif (
            exc.status_code == 401
            and exc.headers
            and exc.headers.get("WWW-Authenticate") == "Bearer"
        ):
            headers = {"WWW-Authenticate": "Bearer"}
        # Exception details and arbitrary exception headers are deliberately not reflected.
        return problem_response(exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(422)

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        return Health()

    @app.get("/ready", response_model=Ready, responses={503: {"model": Problem}})
    async def ready() -> Ready | JSONResponse:
        result = await readiness.evaluate(started=started)
        report = Ready(
            role="ai_api" if engine is not None else "foundation",
            status=result.status,
            dependencies=[
                DependencyStatus(name=d.name, required=d.required, status=d.status)
                for d in result.dependencies
            ],
        )
        if result.status == "not_ready":
            return problem_response(503, readiness=report)
        return report

    @app.get("/version", response_model=ServiceVersion)
    async def service_version() -> ServiceVersion:
        return info

    metrics_bearer = HTTPBearer(auto_error=False, scheme_name="metricsBearer")

    @app.get(
        "/metrics",
        response_class=Response,
        responses={
            200: {"content": {CONTENT_TYPE_LATEST: {"schema": {"type": "string"}}}},
            401: {"model": Problem},
        },
    )
    async def prometheus_metrics(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Security(metrics_bearer)],
    ) -> Response:
        if settings.metrics_token is None:
            return problem_response(404)
        authorization = single_header(request.headers, "authorization")
        scheme, _, supplied = (authorization or "").partition(" ")
        expected = settings.metrics_token.get_secret_value()
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            return problem_response(401, headers={"WWW-Authenticate": "Bearer"})
        return Response(metrics.render(), headers={"Content-Type": CONTENT_TYPE_LATEST})

    app.include_router(access_router(authority, knowledge_backend, settings.app_env))
    return app
