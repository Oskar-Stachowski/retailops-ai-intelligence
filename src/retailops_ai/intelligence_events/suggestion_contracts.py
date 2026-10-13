"""Human-review transport bound to the accepted Source v1 suggestion payload."""

from typing import Literal, Self
from uuid import uuid5

from pydantic import Field, model_validator

from retailops_ai.assistant.contracts import PersistedSuggestion, WireUUID
from retailops_ai.data_contracts.common import Contract, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.intelligence_events.contracts import EVENT_NAMESPACE, MAX_EVENT_BYTES, TOPIC


class TransportSuggestion(PersistedSuggestion):
    # Source's pinned acceptance predates native-v2 observation expiry. Do not
    # silently discard that field or relabel a v2 candidate as a v1 candidate.
    policy_version: Literal["read-only-review-v1"]
    evidence_observed_at: None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def source_relationships(self) -> Self:
        if (
            self.recommendation_id != uuid5(self.trace_id, self.candidate_id)
            or self.answer_id != uuid5(self.trace_id, "answer")
            or not self.source_as_of <= self.created_at < self.expires_at
        ):
            raise ValueError("suggestion_transport_identity_or_time")
        return self


class RecommendationGenerated(Contract):
    event_id: WireUUID
    event_type: Literal["recommendation_generated"]
    schema_version: Literal["2.0"]
    topic: Literal["retailops.intelligence.v2"]
    source: Literal["retailops-ai"]
    correlation_id: WireUUID
    occurred_at: UtcTime
    ingested_at: UtcTime
    payload: TransportSuggestion

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.event_id
            != uuid5(
                EVENT_NAMESPACE, "recommendation_generated:" + str(self.payload.recommendation_id)
            )
            or self.correlation_id != self.payload.trace_id
            or self.occurred_at != self.payload.created_at
            or self.ingested_at != self.occurred_at
            or len(self.wire_bytes()) > MAX_EVENT_BYTES
        ):
            raise ValueError("suggestion_transport_envelope_binding")
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

    def wire_bytes(self) -> bytes:
        return canonical_bytes(self.model_dump(mode="json"))


def suggestion_event(value: PersistedSuggestion) -> RecommendationGenerated:
    payload = TransportSuggestion.model_validate_json(value.model_dump_json())
    return RecommendationGenerated(
        event_id=uuid5(
            EVENT_NAMESPACE, "recommendation_generated:" + str(payload.recommendation_id)
        ),
        event_type="recommendation_generated",
        schema_version="2.0",
        topic=TOPIC,
        source="retailops-ai",
        correlation_id=payload.trace_id,
        occurred_at=payload.created_at,
        ingested_at=payload.created_at,
        payload=payload,
    )
