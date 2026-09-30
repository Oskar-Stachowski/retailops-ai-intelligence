"""Pinned source declarations and separate observations available at the forecast cutoff."""

import json
from datetime import date
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, SellingKey, Symbol, UtcTime, end_of_day
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchScope
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.features_contract import HistoryContext

STREAM = "daily_demand_observations"


class SourceWatermark(Contract):
    as_of_time: UtcTime
    complete_through: date | None
    completeness_status: Literal["complete", "not_ready"]
    meaning: Symbol
    policy_version: Symbol

    @model_validator(mode="after")
    def boundary(self) -> Self:
        if (self.completeness_status == "complete") != (self.complete_through is not None):
            raise ValueError("source_watermark_completeness_mismatch")
        if (
            self.complete_through is not None
            and end_of_day(self.complete_through) > self.as_of_time
        ):
            raise ValueError("source_watermark_boundary_after_declaration")
        return self

    @property
    def supported(self) -> bool:
        return (
            self.policy_version == "daily-demand-1.0.0"
            and self.meaning == "synthetic_sales_day_close_without_return_guarantee"
        )


class SourceObservation(SellingKey):
    latest_complete_observation_date: date | None


def observation_key(row: SellingKey) -> tuple[str, str, str]:
    return row.product_id, row.selling_location_id, row.channel


def observations(histories: tuple[HistoryContext, ...]) -> tuple[SourceObservation, ...]:
    """Last complete point is an availability check, never the declared stream watermark."""
    return tuple(
        SourceObservation(
            product_id=h.product_id,
            selling_location_id=h.selling_location_id,
            channel=h.channel,
            latest_complete_observation_date=max(
                (p.business_date for p in h.points if p.source_data_complete), default=None
            ),
        )
        for h in histories
    )


class SourceFreshness(Contract):
    schema_version: Literal["1.0"] = "1.0"
    policy_id: Literal["forecast-source-watermark-v1"] = "forecast-source-watermark-v1"
    stream: Literal["daily_demand_observations"] = "daily_demand_observations"
    as_of_time: UtcTime
    # Exact descriptor proves the declaration belongs to the immutable AI04 parent.
    # Never returned through HTTP. Size is bounded independently of inference rows.
    curated_descriptor: dict[str, JsonValue]
    watermark: SourceWatermark | None
    observations: tuple[SourceObservation, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def declaration(self) -> Self:
        d = self.curated_descriptor
        if len(canonical_bytes(d)) > 192 * 1024 or d.get("schema_version") not in (
            "1.0.0",
            "1.1.0",
        ):
            raise ValueError("source_freshness_descriptor_version_or_byte_limit")
        declarations = d.get("watermarks")
        if not isinstance(declarations, dict):
            raise ValueError("source_freshness_missing_declarations")
        raw = declarations.get(STREAM)
        expected = SourceWatermark.model_validate_json(json.dumps(raw)) if raw is not None else None
        keys = [observation_key(r) for r in self.observations]
        if (
            self.watermark != expected
            or keys != sorted(set(keys))
            or any(
                r.latest_complete_observation_date is not None
                and r.latest_complete_observation_date > self.as_of_time.date()
                for r in self.observations
            )
            or self.as_of_time != end_of_day(self.as_of_time.date())
        ):
            raise ValueError("source_freshness_declaration_or_cutoff_mismatch")
        return self

    def verify_parent(self, parent: Parent) -> None:
        self.verify_ids(parent.source_dataset_id, parent.curated_dataset_id)
        if canonical_sha256(self.curated_descriptor) != parent.curated_descriptor_sha256:
            raise ValueError("source_freshness_parent_descriptor_mismatch")

    def verify_ids(self, source_id: str, curated_id: str) -> None:
        if (
            self.curated_descriptor.get("parent_source_dataset_id") != source_id
            or "curated-sha256-" + canonical_sha256(self.curated_descriptor) != curated_id
        ):
            raise ValueError("source_freshness_parent_identity_mismatch")

    def scoped(self, scope: BatchScope) -> "SourceFreshness":
        raw = self.model_dump(mode="json")
        raw["observations"] = [
            r.model_dump(mode="json")
            for r in self.observations
            if r.product_id in scope.product_ids
            and r.selling_location_id in scope.selling_location_ids
            and r.channel == scope.channel
        ]
        result = SourceFreshness.model_validate_json(json.dumps(raw))
        expected = sorted(
            (p, loc, scope.channel) for p in scope.product_ids for loc in scope.selling_location_ids
        )
        if [observation_key(r) for r in result.observations] != expected:
            raise ValueError("source_freshness_scope_uncovered")
        return result


def source_freshness(
    descriptor: dict[str, JsonValue], as_of: UtcTime, histories: tuple[HistoryContext, ...]
) -> SourceFreshness:
    declarations = descriptor.get("watermarks")
    raw = declarations.get(STREAM) if isinstance(declarations, dict) else None
    return SourceFreshness.model_validate_json(
        json.dumps(
            dict(
                as_of_time=as_of.isoformat(),
                curated_descriptor=descriptor,
                watermark=raw,
                observations=[r.model_dump(mode="json") for r in observations(histories)],
            )
        )
    )


def versioned_schema(schema: dict[str, Any]) -> None:
    """JSON Schema mirrors conditional metadata presence; old canonical identities remain valid."""
    schema["allOf"] = [
        {
            "if": {
                "properties": {"schema_version": {"const": "1.1"}},
                "required": ["schema_version"],
            },
            "then": {
                "required": ["source_freshness"],
                "properties": {"source_freshness": {"type": "object"}},
            },
            "else": {"properties": {"source_freshness": {"type": "null"}}},
        }
    ]
