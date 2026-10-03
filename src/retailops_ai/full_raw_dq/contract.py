"""Strict v2 transport and binding; no fault plan or evaluation labels."""

from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from retailops_ai.data_contracts.common import Contract, Sha256, SourceID
from retailops_ai.raw_dq.contract import MAX_BODY_BYTES, RawID, Timestamp, stamp
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256

VERSION = "ai-full-parent-replay-2.0.0"
SCOPE = "all_parent_sales_and_return_claims"
MAX_PARENT_EVENTS = 4096
MAX_EVENTS = 8192
MAX_RECORDS = 16384
MAX_BYTES = 32 * 1024**2


class Delivery(Contract):
    contract_version: Literal["raw-dq-capture-2.0.0"]
    record_id: RawID
    received_at: Timestamp
    kind: Literal["event"]
    topic: Literal["retailops.sales.v1"]
    partition: Literal[0]
    offset: Annotated[int, Field(ge=0, lt=MAX_EVENTS)]
    body_utf8: Annotated[str, Field(max_length=MAX_BODY_BYTES)]


class Progress(Contract):
    contract_version: Literal["raw-dq-capture-2.0.0"]
    record_id: RawID
    received_at: Timestamp
    kind: Literal["progress"]
    scope: Literal["all_parent_sales_and_return_claims"]
    after_offset: Annotated[int, Field(ge=-1, lt=MAX_EVENTS)]
    complete_through: Timestamp


Capture = Annotated[Delivery | Progress, Field(discriminator="kind")]
ADAPTER: TypeAdapter[Delivery | Progress] = TypeAdapter(Capture)


class TableIdentity(Contract):
    row_count: Annotated[int, Field(ge=0, le=100000)]
    columns: list[str]
    grain: list[str]
    data_class: Literal["source_observation", "source_plan"]
    content_sha256: Sha256


class Binding(Contract):
    contract_version: Literal["raw-dq-binding-2.0.0"]
    source_dataset_id: SourceID
    source_descriptor_sha256: Sha256
    source_events_sha256: Sha256
    source_event_count: Annotated[int, Field(ge=12, le=MAX_PARENT_EVENTS)]
    source_sales_count: Annotated[int, Field(ge=0, le=MAX_PARENT_EVENTS)]
    source_return_count: Annotated[int, Field(ge=0, le=MAX_PARENT_EVENTS)]
    source_facts_ready: Literal[True]
    selection_policy: Literal["all_canonical_sales_and_native_return_claims_v1"]
    projection: Literal["legacy_operational_sales_and_returns_allowlist_v1"]
    scope: Literal["all_parent_sales_and_return_claims"]
    projection_tables: Annotated[
        dict[
            Literal[
                "sales",
                "orders",
                "sale_price_references",
                "products",
                "inventory_sales",
                "return_events",
            ],
            TableIdentity,
        ],
        Field(min_length=6, max_length=6),
    ]
    return_scope: Literal["purchases_in_parent_source_only"]
    return_status_source: Literal["verified_native_parent_not_refund_amount_inference"]
    business_event_day_completeness: Literal["not_qualified"]
    missing_grain_policy: Literal["unknown_not_zero"]


def parse_capture(payload: dict[str, Any]) -> Delivery | Progress:
    value = ADAPTER.validate_json(canonical_json(payload))
    normalized = value.model_dump(mode="json")
    if normalized != payload or value.record_id != "raw-record-sha256-" + json_sha256(
        {k: v for k, v in normalized.items() if k != "record_id"}
    ):
        raise SnapshotError("full_dq_capture_identity_mismatch")
    received = stamp(value.received_at)
    if isinstance(value, Delivery):
        if len(value.body_utf8.encode()) > MAX_BODY_BYTES:
            raise SnapshotError("full_dq_body_size_limit")
    elif stamp(value.complete_through) > received:
        raise SnapshotError("full_dq_progress_before_frontier")
    return value


def contract_bytes(name: str) -> bytes:
    resource = files("retailops_ai.full_raw_dq").joinpath("contracts/" + name)
    return (
        resource.read_bytes()
        if resource.is_file()
        else (Path(__file__).resolve().parents[3] / "contracts/raw_dq/v2" / name).read_bytes()
    )
