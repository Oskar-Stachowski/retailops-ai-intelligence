"""Public v12 projection without model paths, registry URIs or other tenants' scope."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    FeatureID,
    PredictionID,
    RunID,
    Sha256,
    SourceID,
    UtcTime,
)
from retailops_ai.forecast_jobs.contracts import ProfileID
from retailops_ai.forecast_jobs.read_contracts import ForecastFreshness, Pagination, ReadPolicy
from retailops_ai.forecast_jobs.v12_batch import ReceiptID
from retailops_ai.forecast_jobs.v12_publication import OutputID, V12PublishedRow
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import DatabaseReleaseID, ModelName
from retailops_ai.model_lifecycle.v12_release_contracts import ImageDigest


class V12ForecastItem(V12PublishedRow):
    prediction_id: PredictionID
    prediction_dataset_id: OutputID
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    unit_of_measure: Literal["unit"] = "unit"
    model_name: ModelName
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    approval_sha256: Sha256
    runtime_pin_sha256: Sha256
    image_digest: ImageDigest
    release_id: DatabaseReleaseID
    receipt_id: ReceiptID
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    inference_run_id: RunID
    profile_id: ProfileID
    generated_at: UtcTime
    approval_valid_until: UtcTime
    freshness: ForecastFreshness
    quality_status: Literal["passed_at_publication"] = "passed_at_publication"


class V12ForecastPage(Contract):
    version: Literal["forecast-v12-read-page-1.0.0"] = "forecast-v12-read-page-1.0.0"
    items: tuple[V12ForecastItem, ...] = Field(max_length=200)
    pagination: Pagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    selection: Literal["latest_per_series_horizon", "origin", "inference_run"]
    view_sha256: Sha256
    freshness_policy: ReadPolicy = ReadPolicy()
