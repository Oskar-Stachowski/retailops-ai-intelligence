"""Mean correction strengths learned on development validation, then frozen.

These diagnostics do not qualify a forecast. New data must independently test
the fixed strengths and the offsets fitted before opening their holdout labels.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol

WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
MAX_BIAS = 0.1
MINIMUM_ROWS = 30
Bins = Mapping[float, Sequence[int]]


class MeanWeight(Contract):
    fold: Symbol
    volume: Literal["high", "low", "medium"]
    category: Symbol
    weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    selection_status: Literal["selected", "no_admissible_validation_weight"] = "selected"

    def model_post_init(self, context: object) -> None:
        if self.weight not in WEIGHTS or (
            self.selection_status == "no_admissible_validation_weight" and self.weight != 0
        ):
            raise ValueError("invalid_validation_mean_weight_or_reference_fallback")


class FrozenMeanWeights(Contract):
    version: Literal["development-validation-mean-weights-1.0.0"] = (
        "development-validation-mean-weights-1.0.0"
    )
    development_campaign_id: Annotated[
        str, Field(pattern=r"^functional-v12-campaign-sha256-[0-9a-f]{64}$")
    ]
    selector_receipt_sha256: Sha256
    standard_validation_receipt_sha256: Sha256
    holdout_labels_used: FalseFlag = False
    qualification_use: Literal["fixed_before_new_independent_test"] = (
        "fixed_before_new_independent_test"
    )
    missing_group: Literal["retain_reference_mean"] = "retain_reference_mean"
    zero_volume: Literal["unchanged_separate_v12_zero_estimator"] = (
        "unchanged_separate_v12_zero_estimator"
    )
    weights: Annotated[tuple[MeanWeight, ...], Field(min_length=1, max_length=72)]

    def model_post_init(self, context: object) -> None:
        keys = [(row.fold, row.volume, row.category) for row in self.weights]
        if keys != sorted(set(keys)):
            raise ValueError("mean_weights_require_unique_sorted_groups")

    def lookup(self) -> dict[tuple[str, str, str], float]:
        return {(row.fold, row.volume, row.category): row.weight for row in self.weights}


def metrics(bins: Bins, offset: float, weight: float) -> dict[str, Any]:
    rows = sum(v[0] for v in bins.values())
    actual = sum(v[1] for v in bins.values())
    predicted = math.fsum(max(0.0, b + weight * offset) * v[0] for b, v in bins.items())
    sse = math.fsum(
        v[2] - 2 * max(0.0, b + weight * offset) * v[1] + max(0.0, b + weight * offset) ** 2 * v[0]
        for b, v in bins.items()
    )
    return {
        "rows": rows,
        "actual_sum": actual,
        "predicted_sum": predicted,
        "mse": sse / rows if rows else None,
        "normalized_bias": (predicted - actual) / actual if actual else None,
        "zero_actual_excess_units": predicted if not actual else None,
    }


def bias_passes(value: dict[str, Any]) -> bool:
    if value["actual_sum"] == 0:
        return bool(value["predicted_sum"] == 0)
    return bool(abs(value["normalized_bias"]) <= MAX_BIAS)


def select_weight(
    selection: Bins, full: Bins, prefix_offset: float, full_offset: float
) -> dict[str, Any]:
    """Rank a declared grid on later validation; preserve every failed constraint."""
    base_selection = metrics(selection, prefix_offset, 0.0)
    base_full = metrics(full, full_offset, 0.0)
    candidates: list[dict[str, Any]] = []
    for weight in WEIGHTS:
        selected = metrics(selection, prefix_offset, weight)
        diagnostic = metrics(full, full_offset, weight)
        feasible = (
            selected["rows"] >= MINIMUM_ROWS
            and diagnostic["rows"] >= MINIMUM_ROWS
            and selected["mse"] <= base_selection["mse"]
            and diagnostic["mse"] <= base_full["mse"]
            and bias_passes(selected)
            and bias_passes(diagnostic)
        )
        candidates.append(
            {
                "weight": weight,
                "selection": selected,
                "full_validation": diagnostic,
                "feasible": feasible,
            }
        )
    eligible = [row for row in candidates if row["feasible"]]
    choice = (
        min(eligible, key=lambda row: (row["selection"]["mse"], row["weight"]))
        if eligible
        else None
    )
    return {
        "selected_weight": choice["weight"] if choice else None,
        "status": "selected" if choice else "no_admissible_validation_weight",
        "baseline_selection": base_selection,
        "baseline_full_validation": base_full,
        "candidates": candidates,
    }


def validation_payload(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Check role before accessing a label-bearing observation."""
    if row["role"] != "validation":
        return None
    payload: dict[str, Any] = row["observation"]
    return None if payload["exclusion_reasons"] else payload
