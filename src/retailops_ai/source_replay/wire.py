"""Proposed single-table capture protocol; native SourceSnapshot is unchanged."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_FACTS = 10000
MAX_RECEIPTS = 20000
MAX_CAPTURE_BYTES = 16 * 1024**2
MAX_OFFSET = 2**63 - 1
UUIDText = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def canonical(model: BaseModel, *, exclude: set[str] | None = None) -> bytes:
    return json.dumps(
        model.model_dump(mode="json", exclude=exclude),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(model: BaseModel) -> str:
    return hashlib.sha256(canonical(model)).hexdigest()


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Stream(Wire):
    source_authority_id: UUIDText
    cluster_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
    topic_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:+/-]+$")]
    topic: Literal["retailops.source-observations.v1"] = "retailops.source-observations.v1"

    @model_validator(mode="after")
    def real_identity(self) -> Stream:
        if self.topic_id.rstrip("=") == "A" * 22:
            raise ValueError("uninitialized_topic_identity")
        return self


class ObservationVersion(Wire):
    # Exact native daily_demand_versions field names/types, not legacy sales.
    id: UUIDText
    observation_id: UUIDText
    business_date: date
    product_id: UUIDText
    selling_location_id: UUIDText
    channel: Literal["store", "online", "marketplace", "wholesale"]
    version: int = Field(ge=1, le=MAX_OFFSET)
    observed_units: Annotated[int, Field(ge=0, le=MAX_OFFSET)] | None
    observation_status: Literal["observed_positive", "observed_zero", "closed", "missing"]
    available_at: datetime
    history_policy_version: Literal["observed-quantity-history-1.0.0"]

    @model_validator(mode="after")
    def business_semantics(self) -> ObservationVersion:
        if self.available_at.tzinfo is None or self.available_at.utcoffset() != UTC.utcoffset(
            self.available_at
        ):
            raise ValueError("observation_availability_requires_utc")
        if (
            (
                self.observation_status == "observed_positive"
                and (self.observed_units is None or self.observed_units == 0)
            )
            or (self.observation_status in {"observed_zero", "closed"} and self.observed_units != 0)
            or (self.observation_status == "missing" and self.observed_units is not None)
        ):
            raise ValueError("observation_status_quantity_mismatch")
        return self

    @property
    def key(self) -> tuple[str, int]:
        return self.observation_id, self.version

    @property
    def grain(self) -> tuple[date, str, str, str]:
        return self.business_date, self.product_id, self.selling_location_id, self.channel


class Envelope(Wire):
    version: Literal["source-observation-event-1.0"] = "source-observation-event-1.0"
    table: Literal["daily_demand_versions"] = "daily_demand_versions"
    source_authority_id: UUIDText
    event_id: UUIDText
    fact: ObservationVersion


class Record(Wire):
    partition: int = Field(ge=0, le=31)
    offset: int = Field(ge=0, lt=MAX_OFFSET)
    envelope: Envelope


class Boundary(Wire):
    partition: int = Field(ge=0, le=31)
    next_offset: int = Field(ge=0, le=MAX_OFFSET)


class Receipt(Wire):
    partition: int = Field(ge=0, le=31)
    offset: int = Field(ge=0, lt=MAX_OFFSET)
    event_id: UUIDText
    event_sha256: Digest
    fact_sha256: Digest


class Capture(Wire):
    version: Literal["source-observation-capture-1.0"] = "source-observation-capture-1.0"
    # This scope cannot stand in for the full 43-table SourceSnapshot 1.2.
    table: Literal["daily_demand_versions"] = "daily_demand_versions"
    stream: Stream
    boundaries: tuple[Boundary, ...] = Field(min_length=1, max_length=32)
    rows: tuple[ObservationVersion, ...] = Field(max_length=MAX_FACTS)
    receipts: tuple[Receipt, ...] = Field(max_length=MAX_RECEIPTS)
    capture_id: Annotated[str, Field(pattern=r"^observation-capture-sha256-[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def sealed_identity(self) -> Capture:
        raw = canonical(self, exclude={"capture_id"})
        if len(canonical(self)) > MAX_CAPTURE_BYTES:
            raise ValueError("observation_capture_size_limit")
        if self.capture_id != "observation-capture-sha256-" + hashlib.sha256(raw).hexdigest():
            raise ValueError("observation_capture_identity_mismatch")
        # Semantic checks also run when the frame is rehydrated by the receiver.
        from .history import validate_capture

        validate_capture(self)
        return self
