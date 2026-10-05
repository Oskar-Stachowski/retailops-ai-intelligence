"""Physical risk response metadata; importing these types never starts preparation or training."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    RunID,
    Sha256,
    SourceID,
    Symbol,
    UtcTime,
)

RiskStatus = Literal["scored", "already_stockout", "insufficient_data", "stale_input"]
RiskBand = Literal["low", "medium", "high", "critical"]


class RuntimeLineage(Contract):
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: Annotated[str, Field(pattern=r"^feature-partitions-sha256-[0-9a-f]{64}$")]
    upstream_bundle_id: Annotated[str, Field(pattern=r"^upstream-partitions-sha256-[0-9a-f]{64}$")]
    source_watermark: UtcTime | None
    source_completeness_status: Literal["complete", "not_ready", "unavailable"]


class RuntimeReleasePin(Contract):
    model_name: Literal["retailops-stockout-risk", "retailops-stockout-risk-test-mechanics"]
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,8}$")]
    release_id: Annotated[str, Field(pattern=r"^stockout-release-sha256-[0-9a-f]{64}$")]
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    recipe_content_sha256: Sha256
    policy_content_sha256: Sha256


class FactualFactor(Contract):
    code: Literal[
        "known_available_stock",
        "inventory_snapshot_age",
        "supply_from_observed_sales",
        "supply_from_verified_in_stock_sales",
        "known_delivery_plan_within_horizon",
        "known_overdue_order_quantity",
        "history_had_inventory_constraints",
        "history_inventory_coverage_missing",
        "current_stockout",
        "input_not_evaluable",
        "stale_inventory",
    ]
    field: Symbol
    value: float | int | None
    interpretation: Literal["verified_PIT_fact_not_causal_attribution"] = (
        "verified_PIT_fact_not_causal_attribution"
    )


class RiskItem(Contract):
    risk_id: Annotated[str, Field(pattern=r"^risk-sha256-[0-9a-f]{64}$")]
    product_id: Symbol
    stock_location_id: Symbol
    as_of: UtcTime
    horizon_days: Literal[7] = 7
    status: RiskStatus
    status_reason: (
        Literal[
            "already_stockout",
            "inventory_unknown",
            "stale_inventory_snapshot",
            "inactive_or_unknown_product",
            "inactive_or_unknown_assortment_routing",
            "insufficient_sales_history",
        ]
        | None
    )
    probability: Annotated[float, Field(ge=0, le=1)] | None
    risk_band: RiskBand | None
    threshold_version: Annotated[
        str, Field(pattern=r"^stockout-scoring-policy-sha256-[0-9a-f]{64}$")
    ]
    calibrator_version: Annotated[str, Field(pattern=r"^stockout-calibrator-sha256-[0-9a-f]{64}$")]
    model_name: Literal["retailops-stockout-risk", "retailops-stockout-risk-test-mechanics"]
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,8}$")]
    release_id: Annotated[str, Field(pattern=r"^stockout-release-sha256-[0-9a-f]{64}$")]
    top_factors: tuple[FactualFactor, ...] = Field(min_length=1, max_length=12)
    inventory_freshness_status: Literal["current", "stale", "unknown"]
    freshness_status: Literal["current", "stale", "unknown"]
    lineage: RuntimeLineage
    upstream_lineage_sha256: Sha256 | None
    feature_lineage_sha256: Sha256
    inference_run_id: RunID
    generated_at: UtcTime
    quality_status: Literal["passed_at_publication", "mechanics_only"]

    @model_validator(mode="after")
    def honest_status(self) -> Self:
        if (
            (self.probability is not None) != (self.status == "scored")
            or (self.status_reason is None) != (self.status == "scored")
            or (self.risk_band is not None) != (self.status == "scored")
            or self.generated_at < self.as_of
            or (self.status == "scored" and self.inventory_freshness_status != "current")
            or (self.status == "already_stockout" and self.inventory_freshness_status != "current")
            or (self.status == "stale_input" and self.inventory_freshness_status != "stale")
            or (self.model_name.endswith("test-mechanics"))
            != (self.quality_status == "mechanics_only")
        ):
            raise ValueError("stockout_runtime_output_status_or_namespace")
        return self
