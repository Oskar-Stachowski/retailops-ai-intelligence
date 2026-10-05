"""Portable JSON forest inference: float32 inputs, double thresholds, deterministic scores."""

import math
import struct
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from functools import lru_cache
from statistics import median

from retailops_ai.anomaly_detectors.contract import Fill, FitPolicy, Forest, Pipeline
from retailops_ai.anomaly_detectors.rows import (
    CountRateRow,
    MultiscaleRow,
    NumericalRow,
    validate_row,
)
from retailops_ai.source_snapshot.files import SnapshotError

QUANTUM = Decimal("0.000000000001")
GAMMA = Decimal("0.57721566490153286060651209008240243104215933593992")


def number(value: int | float | bool) -> float:
    try:
        result = float(value)
        converted = struct.unpack("f", struct.pack("f", result))[0]
    except (OverflowError, ValueError, struct.error) as exc:
        raise SnapshotError("anomaly_numeric_dtype_overflow") from exc
    if not math.isfinite(result) or not math.isfinite(converted):
        raise SnapshotError("anomaly_numeric_nonfinite_or_dtype_overflow")
    return result


def fit_fills(rows: list[NumericalRow], policy: FitPolicy) -> tuple[Fill, ...]:
    fills = []
    for name in policy.features:
        known = [number(value) for row in rows if (value := getattr(row, name)) is not None]
        fills.append(
            Fill(
                name=name,
                value=float(median(known)) if known else 0.0,
                known_count=len(known),
                reason="train_median" if known else "entirely_missing_constant_zero",
            )
        )
    return tuple(fills)


def transform(row: NumericalRow, fills: tuple[Fill, ...]) -> tuple[float, ...]:
    row = validate_row(row.model_dump(mode="python"))
    values = tuple(
        fill.value if (value := getattr(row, fill.name)) is None else number(value)
        for fill in fills
    )
    return (*values, *(float(getattr(row, fill.name) is None) for fill in fills))


@lru_cache(maxsize=256)
def average_path(samples: int) -> Decimal:
    if samples <= 1:
        return Decimal(0)
    if samples == 2:
        return Decimal(1)
    with localcontext() as context:
        context.prec = 50
        return 2 * (Decimal(samples - 1).ln() + GAMMA) - 2 * Decimal(samples - 1) / samples


def rounded(value: float) -> float:
    if not math.isfinite(value):
        raise SnapshotError("anomaly_nonfinite_score")
    with localcontext() as context:
        context.prec = 50
        try:
            return float(Decimal(str(value)).quantize(QUANTUM, rounding=ROUND_HALF_EVEN))
        except ArithmeticError as exc:
            raise SnapshotError("anomaly_score_decimal_budget") from exc


def score_matrix(forest: Forest, rows: list[tuple[float, ...]]) -> tuple[float, ...]:
    forest = Forest.model_validate_json(forest.model_dump_json())
    if len(rows) > 10000 or any(len(row) != forest.feature_count for row in rows):
        raise SnapshotError("anomaly_inference_matrix_budget_or_shape")
    output = []
    with localcontext() as context:
        context.prec = 50
        denominator = len(forest.trees) * average_path(forest.max_samples)
        log_two = Decimal(2).ln()
        for row in rows:
            values = tuple(struct.unpack("f", struct.pack("f", number(v)))[0] for v in row)
            total = Decimal(0)
            for tree in forest.trees:
                index = depth = 0
                while tree.nodes[index].feature is not None:
                    node = tree.nodes[index]
                    if (
                        node.feature is None
                        or node.threshold is None
                        or node.left is None
                        or node.right is None
                    ):
                        raise SnapshotError("anomaly_invalid_tree_branch")
                    # values are Python floats holding exact float32 values; the
                    # threshold stays double rather than a NumPy weak scalar.
                    index = node.left if values[node.feature] <= node.threshold else node.right
                    depth += 1
                total += depth + average_path(tree.nodes[index].sample_count)
            score = (-total / denominator * log_two).exp()
            output.append(float(score.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)))
    return tuple(output)


def forest_scores(pipeline: Pipeline, rows: list[NumericalRow]) -> tuple[float, ...]:
    return score_matrix(pipeline.forest, [transform(row, pipeline.fills) for row in rows])


def baseline_score(row: NumericalRow) -> float:
    values = [abs(row.standardized_residual)]
    if isinstance(row, (MultiscaleRow, CountRateRow)):
        values += [
            abs(v) for v in (row.short_count_residual, row.long_count_residual) if v is not None
        ]
        values.append(row.inventory_shortfall)
    return rounded(max(values))
