"""Forecast v2 embeds the existing public ML schema without translating functionals."""

from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import model_validator

from retailops_ai.data_contracts.common import Contract, RunID, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.read_contracts import ForecastQuery
from retailops_ai.forecast_jobs.v12_batch import V12BatchReceipt, V12BatchRun
from retailops_ai.forecast_jobs.v12_publication import V12Publication
from retailops_ai.forecast_jobs.v12_read_contracts import V12ForecastItem
from retailops_ai.forecast_jobs.v12_reader import projection

TOPIC: Literal["retailops.intelligence.v2"] = "retailops.intelligence.v2"
EVENT_NAMESPACE = UUID("b85cb398-e62a-5f70-91ed-6c0fbe868a50")
MAX_EVENT_BYTES = 32 * 1024


def forecast_event_id(prediction_id: str) -> UUID:
    return uuid5(EVENT_NAMESPACE, "forecast_generated:" + prediction_id)


def prediction_identity(item: V12ForecastItem) -> str:
    key = item.model_dump(
        mode="json",
        include={
            "product_id",
            "selling_location_id",
            "channel",
            "forecast_origin",
            "business_timezone",
            "cutoff_policy",
            "target_date",
            "horizon_days",
        },
    )
    return "prediction-sha256-" + canonical_sha256(
        dict(projection="forecast-v12-read-v1", artifact_id=item.prediction_dataset_id, key=key)
    )


class ForecastGenerated(Contract):
    event_id: UUID
    event_type: Literal["forecast_generated"]
    schema_version: Literal["2.0"]
    topic: Literal["retailops.intelligence.v2"]
    source: Literal["retailops-ai"]
    correlation_id: RunID
    occurred_at: UtcTime
    ingested_at: UtcTime
    payload: V12ForecastItem

    @model_validator(mode="after")
    def binding(self) -> Self:
        item = self.payload
        if (
            item.prediction_id != prediction_identity(item)
            or self.event_id != forecast_event_id(item.prediction_id)
            or self.correlation_id != item.inference_run_id
            or self.occurred_at != item.generated_at
            or self.ingested_at != self.occurred_at
            or item.generated_at < item.forecast_origin
            or item.freshness.evaluated_at != item.generated_at
            or not item.generated_at < item.approval_valid_until
        ):
            raise ValueError("intelligence_forecast_binding")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_EVENT_BYTES:
            raise ValueError("intelligence_event_byte_limit")
        return self

    @property
    def partition_key(self) -> str:
        return canonical_sha256(
            dict(
                product_id=self.payload.product_id,
                selling_location_id=self.payload.selling_location_id,
                channel=self.payload.channel,
            )
        )


def forecast_events(
    output: V12Publication, run: V12BatchRun, receipt: V12BatchReceipt
) -> tuple[ForecastGenerated, ...]:
    """Only verified complete publication produces events; freshness is pinned at publication."""
    actor = Principal(
        "intelligence-publication",
        frozenset({"viewer"}),
        frozenset({"forecast:read"}),
        frozenset(output.scope.product_ids),
        frozenset(output.scope.selling_location_ids),
        frozenset({output.scope.channel}),
    )
    items: list[V12ForecastItem] = []
    view = None
    offset = 0
    while True:
        page = projection(
            ForecastQuery(limit=200, offset=offset, view_sha256=view),
            actor,
            ((output, run, receipt),),
            now=output.generated_at,
            unpublished={},
        )
        items.extend(page.items)
        if page.pagination.next_offset is None:
            break
        offset, view = page.pagination.next_offset, page.view_sha256
    return tuple(
        ForecastGenerated(
            event_id=forecast_event_id(item.prediction_id),
            event_type="forecast_generated",
            schema_version="2.0",
            topic=TOPIC,
            source="retailops-ai",
            correlation_id=item.inference_run_id,
            occurred_at=item.generated_at,
            ingested_at=item.generated_at,
            payload=item,
        )
        for item in items
    )
