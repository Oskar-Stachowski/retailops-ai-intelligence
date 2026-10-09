"""Explicit full-census extension; legacy numerical contracts retain their caps."""

from typing import Annotated, Literal

from pydantic import Field

from retailops_ai.anomaly_detectors.contract import (
    Fill,
    FitPolicy,
    Group,
    Pipeline,
    Threshold,
)

MAX_CENSUS_ROWS = 1_000_000


class CensusFitPolicy(FitPolicy):
    version: Literal["anomaly-detector-census-fit-1.0.0"] = "anomaly-detector-census-fit-1.0.0"  # type: ignore[assignment]
    fit_wall_seconds: Literal[300] = 300  # type: ignore[assignment]
    fit_cpu_seconds: Literal[300] = 300  # type: ignore[assignment]
    fit_rss_bytes: Literal[1073741824] = 1073741824  # type: ignore[assignment]
    max_train_rows: Literal[1000000] = 1000000  # type: ignore[assignment]
    max_matrix_bytes: Literal[268435456] = 268435456  # type: ignore[assignment]
    minimum_available_memory_bytes: Literal[1073741824] = 1073741824
    minimum_free_disk_bytes: Literal[6442450944] = 6442450944
    population: Literal["every_declared_eligible_training_row"] = (
        "every_declared_eligible_training_row"
    )


class CensusFill(Fill):
    known_count: Annotated[int, Field(ge=0, le=MAX_CENSUS_ROWS)]


class CensusPipeline(Pipeline):
    training_rows: Annotated[int, Field(ge=16, le=MAX_CENSUS_ROWS)]
    fills: tuple[CensusFill, ...] = Field(min_length=1, max_length=11)


class CensusThreshold(Threshold):
    validation_rows: Annotated[int, Field(ge=16, le=MAX_CENSUS_ROWS)]
    allowed_alerts: Annotated[int, Field(ge=0, le=200000)]
    allowed_high_alerts: Annotated[int, Field(ge=0, le=200000)]


class CensusGroup(Group):
    training_rows: Annotated[int, Field(ge=0, le=MAX_CENSUS_ROWS)]
    pipeline: CensusPipeline | None
    baseline_threshold: CensusThreshold | None
    forest_threshold: CensusThreshold | None
