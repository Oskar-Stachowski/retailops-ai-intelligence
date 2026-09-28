"""Strict wire types and bounded UTC business semantics for contract v1."""

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema

VERSION = "1.0"
UTC_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)$"
)


def utc_input(value: object) -> object:
    if isinstance(value, str):
        if re.fullmatch(UTC_PATTERN, value) is None:
            raise ValueError("utc_timestamp_required")
        return datetime.fromisoformat(value)
    return value


def utc_time(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("utc_timestamp_required")
    return value.astimezone(UTC)


UtcTime = Annotated[
    datetime,
    BeforeValidator(utc_input),
    AfterValidator(utc_time),
    WithJsonSchema({"type": "string", "format": "date-time", "pattern": UTC_PATTERN}),
]
Symbol = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
SourceID = Annotated[str, Field(pattern=r"^source-sha256-[0-9a-f]{64}$")]
CuratedID = Annotated[str, Field(pattern=r"^curated-sha256-[0-9a-f]{64}$")]
FeatureID = Annotated[str, Field(pattern=r"^features-sha256-[0-9a-f]{64}$")]
LabelID = Annotated[str, Field(pattern=r"^labels-sha256-[0-9a-f]{64}$")]
SplitID = Annotated[str, Field(pattern=r"^split-sha256-[0-9a-f]{64}$")]
PredictionDatasetID = Annotated[str, Field(pattern=r"^predictions-sha256-[0-9a-f]{64}$")]
ModelID = Annotated[str, Field(pattern=r"^model-sha256-[0-9a-f]{64}$")]
PredictionID = Annotated[str, Field(pattern=r"^prediction-sha256-[0-9a-f]{64}$")]
RunID = Annotated[str, Field(pattern=r"^run-[0-9a-f]{32}$")]
DatasetID = Annotated[
    str, Field(pattern=r"^(source|curated|features|labels|split|predictions)-sha256-[0-9a-f]{64}$")
]
NonNegativeInt = Annotated[int, Field(ge=0)]
Units = Annotated[float, Field(ge=0)]
Channel = Literal["store", "online"]
Freshness = Literal["current", "stale", "unknown"]
Classification = Literal[
    "facts",
    "simulation_truth",
    "raw_events",
    "source_operational_output",
    "curated",
    "features",
    "labels",
    "split",
    "predictions",
]


def literal_boolean(value: object) -> object:
    if type(value) is not bool:
        raise ValueError("literal_boolean_required")
    return value


TrueFlag = Annotated[Literal[True], BeforeValidator(literal_boolean)]
FalseFlag = Annotated[Literal[False], BeforeValidator(literal_boolean)]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, hide_input_in_errors=True, allow_inf_nan=False
    )


class Versioned(Contract):
    schema_version: Literal["1.0"]


class Provenance(Contract):
    repository: Literal["retailops-cloud-native-platform", "retailops-ai-intelligence"]
    commit_sha: CommitSha
    code_state: Literal["clean", "dirty"]
    dirty_patch_sha256: Sha256 | None
    dependency_lock_sha256: Sha256
    transform_version: Symbol

    @property
    def source_owner(self) -> bool:
        return self.repository == "retailops-cloud-native-platform"

    def model_post_init(self, context: object) -> None:
        if (self.code_state == "dirty") != (self.dirty_patch_sha256 is not None):
            raise ValueError("dirty_code_requires_patch_hash")


class SellingKey(Contract):
    product_id: Symbol
    selling_location_id: Symbol
    channel: Channel


class ForecastKey(SellingKey):
    forecast_origin: UtcTime
    business_timezone: Literal["UTC"]
    cutoff_policy: Literal["end_of_day_second_v1"]
    target_date: date
    horizon_days: Annotated[int, Field(ge=1, le=14)]

    def model_post_init(self, context: object) -> None:
        if self.forecast_origin.time().isoformat() != "23:59:59":
            raise ValueError("origin_must_close_utc_business_day")
        if self.target_date != self.forecast_origin.date() + timedelta(days=self.horizon_days):
            raise ValueError("target_date_horizon_mismatch")


class DateWindow(Contract):
    start: date
    end: date

    def model_post_init(self, context: object) -> None:
        if self.start > self.end:
            raise ValueError("invalid_date_window")


class DataLineage(Contract):
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID


def end_of_day(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, 23, 59, 59, tzinfo=UTC)
