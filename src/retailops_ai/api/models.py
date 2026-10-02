"""Versioned diagnostic HTTP contracts, separate from CLI metadata."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from retailops_ai.contracts import ApplicationInfo
from retailops_ai.forecast_jobs.contracts import BatchErrorCode
from retailops_ai.forecast_jobs.read_contracts import ReadErrorCode
from retailops_ai.knowledge.jobs import IndexErrorCode
from retailops_ai.model_lifecycle.evaluation_contracts import EvaluationErrorCode
from retailops_ai.model_lifecycle.read_contracts import CatalogErrorCode


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Health(Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok"] = "ok"


class DependencyStatus(Contract):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    required: bool
    status: Literal["up", "down", "timeout"]


class Ready(Contract):
    schema_version: Literal["1.0"] = "1.0"
    role: Literal["foundation", "ai_api"] = "foundation"
    status: Literal["ready", "degraded", "not_ready"]
    dependencies: list[DependencyStatus]


class ServiceVersion(ApplicationInfo):
    build_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    image_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    model: None = None


class Problem(Contract):
    type: Literal["about:blank"] = "about:blank"
    title: str
    status: int = Field(ge=400, le=599)
    detail: str
    instance: str = Field(pattern=r"^urn:uuid:[0-9a-f-]{36}$")
    correlation_id: UUID
    readiness: Ready | None = None
    code: (
        IndexErrorCode
        | BatchErrorCode
        | ReadErrorCode
        | CatalogErrorCode
        | EvaluationErrorCode
        | None
    ) = None
