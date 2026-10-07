"""Intelligence v2 carries the accepted anomaly and physical risk API types unchanged."""

from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import model_validator

from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.anomaly_portfolio.serving_contract import BatchID, Item
from retailops_ai.data_contracts.common import Contract, RunID, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.intelligence_events.contracts import EVENT_NAMESPACE, MAX_EVENT_BYTES, TOPIC
from retailops_ai.stockout_runtime.public_contracts import RiskItem


def model_event_id(event_type: str, result_id: str) -> UUID:
    if event_type not in {"anomaly_detected", "stockout_risk_scored"}:
        raise ValueError("intelligence_model_event_type")
    return uuid5(EVENT_NAMESPACE, event_type + ":" + result_id)


def anomaly_identity(item: Item) -> str:
    return "anomaly-sha256-" + canonical_sha256(
        dict(
            batch_id=item.batch_id,
            decision=item.model_dump(mode="json", include=set(Decision.model_fields)),
        )
    )


def risk_identity(item: RiskItem) -> str:
    raw = item.model_dump(mode="json")
    return "risk-sha256-" + canonical_sha256(
        dict(
            product_id=item.product_id,
            stock_location_id=item.stock_location_id,
            as_of=raw["as_of"],
            run_id=item.inference_run_id,
            release_id=item.release_id,
            source=raw["lineage"],
        )
    )


class AnomalyDetected(Contract):
    event_id: UUID
    event_type: Literal["anomaly_detected"]
    schema_version: Literal["2.0"]
    topic: Literal["retailops.intelligence.v2"]
    source: Literal["retailops-ai"]
    correlation_id: BatchID
    occurred_at: UtcTime
    ingested_at: UtcTime
    payload: Item

    @model_validator(mode="after")
    def binding(self) -> Self:
        item = self.payload
        if (
            item.role != "batch"
            or item.anomaly_id != anomaly_identity(item)
            or self.event_id != model_event_id(self.event_type, item.anomaly_id)
            or self.correlation_id != item.inference_run_id
            or self.occurred_at != item.generated_at
            or self.ingested_at != self.occurred_at
            or item.generated_at < item.as_of
        ):
            raise ValueError("intelligence_anomaly_binding")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_EVENT_BYTES:
            raise ValueError("intelligence_event_byte_limit")
        return self

    @property
    def partition_key(self) -> str:
        return canonical_sha256(
            dict(
                event_type=self.event_type,
                observation_type=self.payload.event_type,
                product_id=self.payload.product_id,
                selling_location_id=self.payload.selling_location_id,
                channel=self.payload.channel,
                currency=self.payload.currency,
            )
        )


class StockoutRiskScored(Contract):
    event_id: UUID
    event_type: Literal["stockout_risk_scored"]
    schema_version: Literal["2.0"]
    topic: Literal["retailops.intelligence.v2"]
    source: Literal["retailops-ai"]
    correlation_id: RunID
    occurred_at: UtcTime
    ingested_at: UtcTime
    payload: RiskItem

    @model_validator(mode="after")
    def binding(self) -> Self:
        item = self.payload
        if (
            item.risk_id != risk_identity(item)
            or self.event_id != model_event_id(self.event_type, item.risk_id)
            or self.correlation_id != item.inference_run_id
            or self.occurred_at != item.generated_at
            or self.ingested_at != self.occurred_at
        ):
            raise ValueError("intelligence_stockout_binding")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_EVENT_BYTES:
            raise ValueError("intelligence_event_byte_limit")
        return self

    @property
    def partition_key(self) -> str:
        return canonical_sha256(
            dict(
                event_type=self.event_type,
                product_id=self.payload.product_id,
                stock_location_id=self.payload.stock_location_id,
            )
        )


ModelEvent = AnomalyDetected | StockoutRiskScored


def anomaly_event(item: Item) -> AnomalyDetected:
    item = Item.model_validate_json(item.model_dump_json())
    return AnomalyDetected(
        event_id=model_event_id("anomaly_detected", item.anomaly_id),
        event_type="anomaly_detected",
        schema_version="2.0",
        topic=TOPIC,
        source="retailops-ai",
        correlation_id=item.inference_run_id,
        occurred_at=item.generated_at,
        ingested_at=item.generated_at,
        payload=item,
    )


def stockout_event(item: RiskItem) -> StockoutRiskScored:
    item = RiskItem.model_validate_json(item.model_dump_json())
    return StockoutRiskScored(
        event_id=model_event_id("stockout_risk_scored", item.risk_id),
        event_type="stockout_risk_scored",
        schema_version="2.0",
        topic=TOPIC,
        source="retailops-ai",
        correlation_id=item.inference_run_id,
        occurred_at=item.generated_at,
        ingested_at=item.generated_at,
        payload=item,
    )
