"""Separate fitting knowledge, scoring knowledge and detector readiness."""

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, Sha256, UtcTime
from retailops_ai.day_qualification.contract import GRAIN, ID
from retailops_ai.day_qualification.contract import Point as QualifiedDay
from retailops_ai.raw_dq.contract import stamp

VERSION = "qualified-anomaly-inputs-1.0.0"
MAX_ROWS = 10000
MAX_BYTES = 128 * 1024**2
Units = Annotated[int, Field(ge=0)]
ModelFeatures = tuple[
    Literal["observed_units"],
    Literal["expected_units"],
    Literal["residual_units"],
    Literal["robust_scale_units"],
    Literal["standardized_residual"],
    Literal["planned_price"],
    Literal["promotion_offered"],
    Literal["on_hand"],
]
MODEL_FEATURES: ModelFeatures = (
    "observed_units",
    "expected_units",
    "residual_units",
    "robust_scale_units",
    "standardized_residual",
    "planned_price",
    "promotion_offered",
    "on_hand",
)


class ModelRow(Contract):
    """Exact numerical allowlist; lineage, labels and query audit stay outside it."""

    observed_units: Units
    expected_units: Units
    residual_units: int
    robust_scale_units: Annotated[float, Field(ge=1, allow_inf_nan=False)]
    standardized_residual: Annotated[float, Field(allow_inf_nan=False)]
    planned_price: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    promotion_offered: bool
    on_hand: Units | None


class Policy(Contract):
    version: Literal["qualified-anomaly-inputs-1.0.0"] = "qualified-anomaly-inputs-1.0.0"
    history_days: Literal[28] = 28
    seasonal_lag_days: Literal[7] = 7
    minimum_history_days: Literal[14] = 14
    minimum_residuals: Literal[7] = 7
    sales_delay_hours: Annotated[int, Field(ge=0, le=168)] = 24
    returns_delay_hours: Annotated[int, Field(ge=0, le=168)] = 72
    fit_clock: Literal["utc_window_start_minus_one_microsecond"] = (
        "utc_window_start_minus_one_microsecond"
    )
    scoring_clock: Literal["exclusive_utc_day_end_plus_delay_inclusive"] = (
        "exclusive_utc_day_end_plus_delay_inclusive"
    )
    quantity_basis: Literal["dq_accepted_receipts_at_query_cutoff"] = (
        "dq_accepted_receipts_at_query_cutoff"
    )
    scale_policy: Literal["max_1_pcs_1.4826_mad_prior_seasonal_residuals"] = (
        "max_1_pcs_1.4826_mad_prior_seasonal_residuals"
    )
    stock_policy: Literal["scoring_context_only_no_automatic_censoring_correction"] = (
        "scoring_context_only_no_automatic_censoring_correction"
    )


class Reference(Contract):
    table: Literal[
        "daily_demand_versions", "price_plans", "promotion_plans", "inventory_daily_snapshots"
    ]
    record_sha256: Sha256
    available_at: UtcTime
    business_date: date | None
    role: Literal["known_plan", "stock_mapping", "inventory_context"]


class Context(Contract):
    planned_price: Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{2}$")] | None
    promotion_offered: bool
    stock_location_id: str | None
    on_hand: Units | None
    stock_status: Literal["unavailable", "potential_stockout", "no_stockout_signal"]
    references: tuple[Reference, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def stock_contract(self) -> Self:
        expected = (
            "unavailable"
            if self.on_hand is None
            else "potential_stockout"
            if self.on_hand == 0
            else "no_stockout_signal"
        )
        if self.stock_status != expected or (
            self.on_hand is not None and self.stock_location_id is None
        ):
            raise ValueError("qualified_anomaly_stock_status_mismatch")
        return self


def statistics(
    history: tuple[QualifiedDay, ...], day: date
) -> tuple[int | None, list[int], float | None, bool]:
    known = {
        date.fromisoformat(p.business_date): p.observed_units
        for p in history
        if p.status == "qualified"
    }
    expected = known.get(day - timedelta(days=7))
    residuals = [
        value - previous
        for d, value in sorted(known.items())
        if value is not None and (previous := known.get(d - timedelta(days=7))) is not None
    ]
    scale = None
    floor = False
    if len(known) >= 14 and len(residuals) >= 7 and expected is not None:
        center = median(residuals)
        raw_scale = 1.4826 * float(median(abs(r - center) for r in residuals))
        scale = max(1.0, raw_scale)
        floor = raw_scale < 1.0
    return expected, residuals, scale, floor


class Point(Contract):
    product_id: ID
    selling_location_id: ID
    schema_version: Literal["1.0.0"] = "1.0.0"
    event_type: Literal["sale_completed", "return_completed"]
    currency: Literal["PLN", "EUR"]
    channel: Literal["store", "online", "marketplace", "wholesale"]
    business_date: date
    fit_cutoff: UtcTime
    scoring_origin: UtcTime
    observation: QualifiedDay
    history: tuple[QualifiedDay, ...] = Field(min_length=28, max_length=28)
    history_status_counts: dict[str, Annotated[int, Field(ge=0)]]
    usable_history_days: Annotated[int, Field(ge=0, le=28)]
    residual_count: Annotated[int, Field(ge=0, le=21)]
    expected_units: Units | None
    robust_scale_units: Annotated[float, Field(ge=1, allow_inf_nan=False)] | None
    scale_floor_applied: bool
    residual_units: int | None
    standardized_residual: Annotated[float, Field(allow_inf_nan=False)] | None
    status: Literal["ready_input", "insufficient_history", "day_unqualified"]
    reason_codes: tuple[str, ...]
    context: Context
    detector_readiness: Literal["not_qualified"] = "not_qualified"

    @model_validator(mode="after")
    def causal_contract(self) -> Self:
        start = datetime.combine(self.business_date, datetime.min.time(), UTC)
        if self.fit_cutoff != start - timedelta(
            microseconds=1
        ) or self.scoring_origin < start + timedelta(days=1):
            raise ValueError("qualified_anomaly_invalid_clocks")
        own = tuple(getattr(self, k) for k in GRAIN if k != "business_date")
        for point in (*self.history, self.observation):
            if tuple(getattr(point, k) for k in GRAIN if k != "business_date") != own:
                raise ValueError("qualified_anomaly_series_or_currency_mismatch")
        if (
            self.observation.business_date != self.business_date.isoformat()
            or stamp(self.observation.as_of) != self.scoring_origin
        ):
            raise ValueError("qualified_anomaly_outcome_clock_mismatch")
        dates = [(self.business_date - timedelta(days=i)).isoformat() for i in range(28, 0, -1)]
        if [p.business_date for p in self.history] != dates or any(
            stamp(p.as_of) != self.fit_cutoff for p in self.history
        ):
            raise ValueError("qualified_anomaly_history_contains_future_knowledge")
        counts = dict(Counter(p.status for p in self.history))
        expected, residuals, scale, floor = statistics(self.history, self.business_date)
        if (
            self.history_status_counts,
            self.usable_history_days,
            self.residual_count,
            self.expected_units,
            self.robust_scale_units,
            self.scale_floor_applied,
        ) != (counts, counts.get("qualified", 0), len(residuals), expected, scale, floor):
            raise ValueError("qualified_anomaly_history_statistics_mismatch")
        status = (
            "day_unqualified"
            if self.observation.status != "qualified"
            else "insufficient_history"
            if scale is None
            else "ready_input"
        )
        reasons = (
            ([] if self.observation.status == "qualified" else [self.observation.status])
            + (["insufficient_history"] if scale is None else [])
            + (["robust_scale_floor_1_pcs"] if floor else [])
        )
        if self.status != status or self.reason_codes != tuple(reasons):
            raise ValueError("qualified_anomaly_status_mismatch")
        observed = self.observation.observed_units
        residual = (
            observed - expected
            if status == "ready_input" and observed is not None and expected is not None
            else None
        )
        standardized = residual / scale if residual is not None and scale is not None else None
        if self.residual_units != residual or self.standardized_residual != standardized:
            raise ValueError("qualified_anomaly_unscoreable_or_inconsistent_residual")
        for ref in self.context.references:
            cutoff = self.fit_cutoff if ref.role == "known_plan" else self.scoring_origin
            if ref.available_at > cutoff or (
                ref.role != "known_plan" and ref.business_date != self.business_date
            ):
                raise ValueError("qualified_anomaly_context_after_cutoff")
        return self
