"""Safe metadata for models used by scoped forecasts, separate from evaluations."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Channel, Contract, Sha256, Symbol, UtcTime
from retailops_ai.model_lifecycle.contracts import Receipt, RunID, Version

CatalogErrorCode = Literal[
    "model-read-denied",
    "model-scope-invalid",
    "model-scope-limit",
    "model-not-found",
    "model-view-required",
    "model-view-changed",
    "model-read-budget",
    "model-metadata-invalid",
]


class CatalogScope(Contract):
    product_id: Symbol | None = None
    selling_location_id: Symbol | None = None
    channel: Channel | None = None


class CatalogQuery(CatalogScope):
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0, le=1000)] = 0
    view_sha256: Sha256 | None = None


class CatalogFreshness(Contract):
    status: Literal["unknown"] = "unknown"
    reason: Literal["registry_and_runtime_not_observed"] = "registry_and_runtime_not_observed"
    evaluated_at: UtcTime


class ApprovedModelRelease(Contract):
    release_id: Annotated[
        str, Field(pattern=r"^(model-release|anomaly-release)-sha256-[0-9a-f]{64}$")
    ]
    model_version: Version
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class CatalogVersion(Contract):
    model_name: Literal["retailops-demand-forecast", "retailops-sales-anomaly"] = (
        "retailops-demand-forecast"
    )
    model_version: Version
    status: Literal["approved_release_recorded", "previously_published"]
    model_family: Literal[
        "baseline",
        "random_forest",
        "hist_gradient_boosting",
        "seasonal_residual",
        "isolation_forest",
    ]
    flavor: Literal["forecast-json-v1", "baseline-json-v1", "anomaly-json-v1"]
    mlflow_run_id: RunID
    model_artifact: Receipt
    model_card_report: Receipt
    qualification_sha256: Sha256
    config_sha256: Sha256
    feature_schema_version: Literal["forecast-features-v1", "qualified-anomaly-inputs-1.0.0"] = (
        "forecast-features-v1"
    )
    evaluation_id: Symbol
    visible_last_published_at: UtcTime
    freshness: CatalogFreshness


class CatalogModel(Contract):
    model_name: Literal["retailops-demand-forecast", "retailops-sales-anomaly"] = (
        "retailops-demand-forecast"
    )
    visible_version_count: Annotated[int, Field(ge=1, le=1000)]
    approved_release: ApprovedModelRelease | None
    registry_aliases: None = None
    deployed_model_version: None = None
    deployment_status: Literal["not_attested"] = "not_attested"
    drift_status: Literal["not_run"] = "not_run"
    freshness: CatalogFreshness
    generated_at: UtcTime


class CatalogPagination(Contract):
    limit: Annotated[int, Field(ge=1, le=200)]
    offset: Annotated[int, Field(ge=0, le=1000)]
    total: Annotated[int, Field(ge=0, le=1000)]
    next_offset: Annotated[int, Field(ge=0, le=1000)] | None


class ModelPage(Contract):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[CatalogModel, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256


class VersionPage(Contract):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[CatalogVersion, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256
