"""Reviewed operational capture types; private injection plans are excluded."""

from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from retailops_ai.data_contracts.common import UTC_PATTERN, Contract, Sha256, SourceID
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256

VERSION = "ai-selected-sales-replay-1.0.0"
TOPIC = "retailops.sales.v1"
SOURCE = "retailops.synthetic-generator"
MAX_RECORDS = 2048
MAX_BODY_BYTES = 65536
MAX_CAPTURE_BYTES = 8 * 1024**2
Timestamp = Annotated[str, Field(pattern=UTC_PATTERN)]
RawID = Annotated[str, Field(pattern=r"^raw-record-sha256-[0-9a-f]{64}$")]


class Delivery(Contract):
    record_id: RawID
    received_at: Timestamp
    kind: Literal["event"]
    topic: Literal["retailops.sales.v1"]
    partition: Literal[0]
    offset: Annotated[int, Field(ge=0, lt=1024)]
    body_utf8: Annotated[str, Field(max_length=MAX_BODY_BYTES)]


class Progress(Contract):
    record_id: RawID
    received_at: Timestamp
    kind: Literal["progress"]
    scope: Literal["selected_sales_fixture"]
    after_offset: Annotated[int, Field(ge=-1, lt=1024)]
    complete_through: Timestamp


Capture = Annotated[Delivery | Progress, Field(discriminator="kind")]
ADAPTER: TypeAdapter[Delivery | Progress] = TypeAdapter(Capture)


class Binding(Contract):
    source_dataset_id: SourceID
    source_descriptor_sha256: Sha256
    source_events_sha256: Sha256
    source_event_count: Annotated[int, Field(ge=12, le=512)]
    source_sales_count: Annotated[int, Field(ge=12, le=100000)]
    source_facts_ready: Literal[True]
    selection_policy: Literal["chronological_equally_spaced_inclusive_endpoints_v1"]
    projection: Literal["legacy_sale_completed_operational_allowlist_v1"]


def stamp(value: str) -> datetime:
    # Pattern validation is applied to captures and wire events before this call.
    from retailops_ai.data_contracts.common import utc_input, utc_time

    parsed = utc_input(value)
    if not isinstance(parsed, datetime):
        raise SnapshotError("dq_utc_timestamp_required")
    return utc_time(parsed)


def parse_capture(payload: dict[str, Any]) -> Delivery | Progress:
    record = ADAPTER.validate_json(canonical_json(payload))
    normalized = record.model_dump(mode="json")
    unsigned = {k: v for k, v in normalized.items() if k != "record_id"}
    if normalized != payload or record.record_id != "raw-record-sha256-" + json_sha256(unsigned):
        raise SnapshotError("dq_capture_identity_mismatch")
    received = stamp(record.received_at)
    if isinstance(record, Delivery):
        if len(record.body_utf8.encode()) > MAX_BODY_BYTES:
            raise SnapshotError("dq_body_size_limit")
    elif stamp(record.complete_through) > received:
        raise SnapshotError("dq_progress_before_frontier")
    return record


def contract_bytes(name: str) -> bytes:
    resource = files("retailops_ai.raw_dq").joinpath("contracts/" + name)
    return (
        resource.read_bytes()
        if resource.is_file()
        else (Path(__file__).resolve().parents[3] / "contracts/raw_dq/v1" / name).read_bytes()
    )
