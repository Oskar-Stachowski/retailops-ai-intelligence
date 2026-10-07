"""Closed review candidates from typed evidence; no operational quantity or writer."""

import json
from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.agent.chat_context import tool_result_ref
from retailops_ai.agent.chat_contracts import DraftAction, EvidenceRef, ShortText
from retailops_ai.agent.execution import ToolSession
from retailops_ai.agent.tools import (
    AnomalyResult,
    ForecastResult,
    InventoryResult,
    ModelStatusResult,
    OperationsResult,
    RiskResult,
)
from retailops_ai.data_contracts.common import (
    Channel,
    Contract,
    Sha256,
    Symbol,
    TrueFlag,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import canonical_sha256


class SuggestionPolicy(Versioned):
    policy_version: Literal["read-only-review-v1"] = "read-only-review-v1"
    minimum_stockout_probability: Annotated[float, Field(ge=0.8, le=1)] = 0.8
    operations_lag_seconds: Annotated[float, Field(ge=60, le=3600)] = 60.0
    lifetime_seconds: Annotated[int, Field(ge=1, le=300)] = 300
    max_candidates: Annotated[int, Field(ge=1, le=5)] = 5


class SuggestionCandidate(Contract):
    candidate_id: Annotated[str, Field(pattern=r"^candidate-sha256-[0-9a-f]{64}$")]
    recommendation_type: Literal[
        "review_replenishment", "investigate_anomaly", "refresh_source_data"
    ]
    product_id: Symbol
    selling_location_id: Symbol
    stock_location_id: Symbol | None
    channel: Channel
    action: ShortText
    priority: Literal["low", "medium", "high"]
    rationale: ShortText
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=8)
    model_release_refs: list[EvidenceRef] = Field(max_length=8)
    policy_version: Literal["read-only-review-v1"]
    policy_sha256: Sha256
    source_as_of: UtcTime
    expires_at: UtcTime
    freshness_status: Literal["current"]
    requires_human_review: TrueFlag
    status: Literal["proposed"]

    @model_validator(mode="after")
    def identity(self) -> Self:
        if (
            self.candidate_id
            != "candidate-sha256-"
            + canonical_sha256(self.model_dump(mode="json", exclude={"candidate_id"}))
            or not 0 < (self.expires_at - self.source_as_of).total_seconds() <= 300
        ):
            raise ValueError("suggestion_candidate_identity_or_expiry_mismatch")
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or len(
            set(self.model_release_refs)
        ) != len(self.model_release_refs):
            raise ValueError("duplicate_candidate_reference")
        return self

    def draft_action(self) -> DraftAction:
        return DraftAction(
            action=self.action,
            priority=self.priority,
            rationale=self.rationale,
            evidence_refs=self.evidence_refs.copy(),
            requires_human_review=True,
        )


def candidates(tools: ToolSession, policy: SuggestionPolicy) -> tuple[SuggestionCandidate, ...]:
    """Called only after complete, consistent evidence. Expiry is checked at decision time."""
    outputs = tools.accepted_outputs()
    now = tools.executor.clock()
    rows: list[SuggestionCandidate] = []
    ttl = min(policy.lifetime_seconds, tools.executor.policy.freshness_seconds)

    def add(
        kind: Literal["review_replenishment", "investigate_anomaly", "refresh_source_data"],
        product: str,
        location: str,
        channel: Channel,
        stock: str | None,
        action: str,
        priority: Literal["low", "medium", "high"],
        rationale: str,
        refs: list[str],
        releases: list[str],
        timestamps: list[UtcTime],
    ) -> None:
        oldest = min(timestamps)
        expires = oldest + timedelta(seconds=ttl)
        if any(timestamp > now for timestamp in timestamps) or now >= expires:
            return
        value = {
            "recommendation_type": kind,
            "product_id": product,
            "selling_location_id": location,
            "stock_location_id": stock,
            "channel": channel,
            "action": action,
            "priority": priority,
            "rationale": rationale,
            "evidence_refs": sorted(set(refs)),
            "model_release_refs": sorted(set(releases)),
            "policy_version": policy.policy_version,
            "policy_sha256": canonical_sha256(policy.model_dump(mode="json")),
            "source_as_of": oldest.isoformat().replace("+00:00", "Z"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "freshness_status": "current",
            "requires_human_review": True,
            "status": "proposed",
        }
        # JSON conversion preserves strict wire validation of dates and lists.
        serialized = json.loads(json.dumps(value, default=str))
        serialized["candidate_id"] = "candidate-sha256-" + canonical_sha256(serialized)
        rows.append(SuggestionCandidate.model_validate_json(json.dumps(serialized)))

    def grain(item: object) -> tuple[str, str, Channel]:
        from retailops_ai.data_contracts.common import SellingKey

        if not isinstance(item, SellingKey):
            raise ValueError("invalid_candidate_grain")
        return item.product_id, item.selling_location_id, item.channel

    for output in outputs:
        if isinstance(output, AnomalyResult) and output.status == "ok":
            for item in output.items:
                if item.observed_units == item.expected_units or output.as_of is None:
                    continue
                add(
                    "investigate_anomaly",
                    *grain(item),
                    None,
                    "Review the detector observation and source data with an operator.",
                    "medium",
                    "The typed detector reports unequal observed and expected values; this does not establish a cause.",
                    [tool_result_ref(output)],
                    [item.model_release_ref],
                    [output.as_of],
                )
        elif isinstance(output, OperationsResult) and output.status == "ok":
            for operation in output.items:
                if output.as_of is None or (
                    operation.stream_status == "healthy"
                    and operation.lag_seconds < policy.operations_lag_seconds
                ):
                    continue
                add(
                    "refresh_source_data",
                    *grain(operation),
                    None,
                    "Ask an operator to review source freshness and the stream read model.",
                    "high" if operation.stream_status == "stopped" else "medium",
                    "The typed operations source meets the configured status or lag review rule; no refresh is executed.",
                    [tool_result_ref(output)],
                    [],
                    [output.as_of],
                )
        elif isinstance(output, RiskResult) and output.status == "ok":
            for risk in output.items:
                if output.as_of is None or risk.probability < max(
                    policy.minimum_stockout_probability, risk.threshold
                ):
                    continue
                inventories = [
                    (other, row)
                    for other in outputs
                    if isinstance(other, InventoryResult) and other.status == "ok"
                    for row in other.items
                    if grain(row) == grain(risk)
                ]
                models = [
                    (other, row)
                    for other in outputs
                    if isinstance(other, ModelStatusResult) and other.status == "ok"
                    for row in other.items
                    if grain(row) == grain(risk)
                    and row.model_id == risk.model_id
                    and row.deployed_release_ref == risk.model_release_ref
                ]
                forecasts = [
                    other
                    for other in outputs
                    if isinstance(other, ForecastResult)
                    and other.result.status == "ok"
                    and other.result.items
                    and all(
                        row.quality_status == "passed"
                        and row.freshness_status == "current"
                        and row.generated_at <= now
                        and row.key.forecast_origin == other.result.request.as_of
                        for row in other.result.items
                    )
                    and len({row.release_id for row in other.result.items}) == 1
                    and len({row.model.model_id for row in other.result.items}) == 1
                    and len([row for row in other.result.items if grain(row.key) == grain(risk)])
                    == (risk.window.end - risk.window.start).days + 1
                    and {
                        row.key.target_date
                        for row in other.result.items
                        if grain(row.key) == grain(risk)
                    }
                    == {
                        risk.window.start + timedelta(days=day)
                        for day in range((risk.window.end - risk.window.start).days + 1)
                    }
                ]
                if len(inventories) != 1 or len(models) != 1 or len(forecasts) != 1:
                    continue
                inventory, stock = inventories[0]
                model, _ = models[0]
                forecast = forecasts[0]
                if (
                    risk.inventory_source_ref != inventory.source_ref
                    or risk.inventory_as_of != inventory.as_of
                    or inventory.as_of is None
                    or model.as_of is None
                    or forecast.result.as_of is None
                ):
                    continue
                add(
                    "review_replenishment",
                    *grain(risk),
                    stock.stock_location_id,
                    "Review replenishment options with an operator; no quantity or order is authorized.",
                    "high",
                    "Calibrated risk meets both review thresholds, references this current inventory, and matches the approved deployed risk model; the reported sales forecast has passed its quality gate.",
                    [tool_result_ref(source) for source in (output, inventory, model, forecast)],
                    [risk.model_release_ref, *(row.release_id for row in forecast.result.items)],
                    [output.as_of, inventory.as_of, model.as_of, forecast.result.as_of],
                )
    unique = {row.candidate_id: row for row in rows}
    ordered = sorted(
        unique.values(),
        key=lambda row: (
            {"high": 0, "medium": 1, "low": 2}[row.priority],
            row.recommendation_type,
            row.product_id,
            row.selling_location_id,
            row.candidate_id,
        ),
    )
    return tuple(ordered[: policy.max_candidates])
