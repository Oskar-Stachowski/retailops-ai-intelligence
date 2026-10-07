"""Human capacity/cost comparisons on eligible development points only."""

import math
from collections import Counter, defaultdict
from typing import Any

from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    OperatorCapacity,
    PolicyRow,
    PolicySpec,
    risk_band,
)


def confusion(
    rows: list[PolicyRow], selected: set[tuple[str, str, str]], costs: AttentionCosts
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for row in rows:
        flagged = (row.product_id, row.stock_location_id, row.as_of.isoformat()) in selected
        counts[
            ("true_" if flagged == bool(row.incident_stockout) else "false_")
            + ("positive" if flagged else "negative")
        ] += 1
    tp, fp, fn = (counts[c] for c in ("true_positive", "false_positive", "false_negative"))
    return dict(
        rows=len(rows),
        positives=tp + fn,
        selected=tp + fp,
        **{
            c: counts[c]
            for c in ("true_positive", "false_positive", "true_negative", "false_negative")
        },
        precision=tp / (tp + fp) if tp + fp else None,
        recall=tp / (tp + fn) if tp + fn else None,
        status="evaluable" if tp + fp and tp + fn else "not_evaluable",
        cost=fp * costs.false_attention + fn * costs.missed_incident,
        cost_interpretation=costs.interpretation,
    )


def capacity_selection(
    rows: list[PolicyRow], capacity: OperatorCapacity
) -> tuple[set[tuple[str, str, str]], list[dict[str, Any]]]:
    groups: dict[str, list[PolicyRow]] = defaultdict(list)
    for row in rows:
        groups[row.as_of.isoformat()].append(row)
    selected: set[tuple[str, str, str]] = set()
    origins = []
    for origin, points in sorted(groups.items()):
        n = (
            capacity.top_n
            if capacity.mode == "top_n"
            else math.ceil(len(points) * float(capacity.fraction or 0))
        )
        n = min(int(n or 0), len(points))
        ranking = sorted(points, key=lambda r: (-r.probability, r.product_id, r.stock_location_id))
        selected.update((r.product_id, r.stock_location_id, origin) for r in ranking[:n])
        origins.append(dict(as_of=origin, eligible=len(points), selected=n))
    return selected, origins


def at_capacity(
    rows: list[PolicyRow], capacity: OperatorCapacity, costs: AttentionCosts
) -> dict[str, Any]:
    selected, origins = capacity_selection(rows, capacity)
    return {**confusion(rows, selected, costs), "origins": origins}


def assess(rows: list[PolicyRow], spec: PolicySpec) -> dict[str, Any]:
    spec = PolicySpec.model_validate_json(spec.model_dump_json())
    checked = [PolicyRow.model_validate_json(r.model_dump_json()) for r in rows]
    if not checked or len(checked) > 10000:
        raise ValueError("stockout_policy_row_limit")
    keys = [(r.product_id, r.stock_location_id, r.as_of) for r in checked]
    if len(set(keys)) != len(keys):
        raise ValueError("stockout_policy_duplicate_physical_keys")
    bands = Counter(risk_band(r.probability, spec.thresholds) for r in checked)
    thresholds = {}
    for name in ("medium_from", "high_from", "critical_from"):
        threshold = getattr(spec.thresholds, name)
        selected = {
            (r.product_id, r.stock_location_id, r.as_of.isoformat())
            for r in checked
            if r.probability >= threshold
        }
        thresholds[name] = {"threshold": threshold, **confusion(checked, selected, spec.costs)}
    # Scope consumes capacity globally per physical origin, then reports slices
    # of the same chosen queue; it must not grant every category a fresh budget.
    selected, origins = capacity_selection(checked, spec.capacity)
    groups: dict[str, list[PolicyRow]] = defaultdict(list)
    for row in checked:
        groups["category:" + row.category_id].append(row)
        groups["stock_location:" + row.stock_location_id].append(row)
        groups[
            "historical_inventory_constraint:" + str(row.historical_inventory_constraint).lower()
        ].append(row)
    capacity = {**confusion(checked, selected, spec.costs), "origins": origins}
    return dict(
        capacity=capacity,
        threshold_costs=thresholds,
        risk_band_counts={b: bands[b] for b in ("low", "medium", "high", "critical")},
        segments_at_same_global_capacity={
            name: confusion(points, selected, spec.costs) for name, points in sorted(groups.items())
        },
        statuses_excluded=["already_stockout", "insufficient_data", "stale_input"],
        probability_is_not_unconstrained_demand=True,
        serving_eligible=False,
        thresholds_approved=False,
        final_test_outcomes_evaluated=False,
    )
