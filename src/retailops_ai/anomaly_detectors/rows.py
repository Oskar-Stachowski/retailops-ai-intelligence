"""Explicit multiscale count residuals over known, causally available outcomes."""

import math
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract
from retailops_ai.qualified_anomalies.contract import MODEL_FEATURES, ModelRow

EXTRA_FEATURES = ("short_count_residual", "long_count_residual", "inventory_shortfall")
ALL_FEATURES = (*MODEL_FEATURES, *EXTRA_FEATURES)
RECIPE = "causal-count-residuals-1.0.0"


class CountLag(Contract):
    lag_days: Annotated[int, Field(ge=1, le=6)]
    observed_units: Annotated[int, Field(ge=0)]
    expected_units: Annotated[int, Field(ge=0)]


def count_residual(row: ModelRow, recent: tuple[CountLag, ...], days: int) -> float | None:
    known = [r for r in recent if r.lag_days < days]
    # Unknown dates are omitted, never treated as zero. At least two known
    # outcomes in a 3-day window, or four in a 7-day window, are required.
    if len(known) + 1 < (2 if days == 3 else 4):
        return None
    expected = row.expected_units + sum(r.expected_units for r in known)
    observed = row.observed_units + sum(r.observed_units for r in known)
    return (observed - expected) / max(1.0, math.sqrt(expected))


def stock_shortfall(row: ModelRow) -> float:
    # An explicit factual rule within the baseline, never a substitute for IF.
    if row.on_hand is None or row.on_hand > 1 or row.expected_units < 1:
        return 0.0
    return 8.0 * max(0, row.expected_units - row.observed_units) / row.expected_units


class MultiscaleRow(ModelRow):
    residual_recipe: Literal["causal-count-residuals-1.0.0"] = "causal-count-residuals-1.0.0"
    recent_counts: tuple[CountLag, ...] = Field(max_length=6)
    short_count_residual: Annotated[float, Field(allow_inf_nan=False)] | None
    long_count_residual: Annotated[float, Field(allow_inf_nan=False)] | None
    inventory_shortfall: Annotated[float, Field(ge=0, le=8, allow_inf_nan=False)]

    @model_validator(mode="after")
    def binding(self) -> Self:
        lags = [r.lag_days for r in self.recent_counts]
        if lags != sorted(set(lags)) or (
            self.residual_units != self.observed_units - self.expected_units
            or self.standardized_residual != self.residual_units / self.robust_scale_units
            or self.short_count_residual != count_residual(self, self.recent_counts, 3)
            or self.long_count_residual != count_residual(self, self.recent_counts, 7)
            or self.inventory_shortfall != stock_shortfall(self)
        ):
            raise ValueError("anomaly_multiscale_known_count_or_residual_binding")
        return self


NumericalRow = ModelRow | MultiscaleRow


def validate_row(value: dict[str, Any]) -> NumericalRow:
    if value.get("residual_recipe") == RECIPE:
        return MultiscaleRow.model_validate(value)
    return ModelRow.model_validate(value)
