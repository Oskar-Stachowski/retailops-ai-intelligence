"""Explicit AP, reliability and fixed per-origin capacity at natural prevalence."""

from collections import defaultdict
from math import ceil
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score  # type: ignore[import-untyped]

from retailops_ai.stockout.split import key
from retailops_ai.stockout_training.contract import TrainingPolicy

THRESHOLDS = (0.1, 0.25, 0.5, 0.75, 0.9)


def value(v: float) -> float:
    return round(float(v), 12)


def confusion(outcomes: list[int], selected: list[bool], policy: TrainingPolicy) -> dict[str, Any]:
    if len(outcomes) != len(selected):
        raise ValueError("stockout_evaluation_prediction_count_mismatch")
    tp = sum(y == 1 and picked for y, picked in zip(outcomes, selected, strict=True))
    fp = sum(y == 0 and picked for y, picked in zip(outcomes, selected, strict=True))
    positives = sum(outcomes)
    fn = positives - tp
    return dict(
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=len(outcomes) - tp - fp - fn,
        selected=tp + fp,
        recall=value(tp / positives) if positives else None,
        precision=value(tp / (tp + fp)) if tp + fp else None,
        cost=value(fp * policy.false_attention_cost + fn * policy.missed_incident_cost),
        cost_unit="exploratory_attention_and_missed_incident_units",
    )


def capacity(
    rows: list[dict[str, Any]],
    outcomes: list[int],
    probabilities: list[float],
    policy: TrainingPolicy,
) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        groups[r["as_of"]].append(i)
    selected = [False] * len(rows)
    counts = []
    for origin, indices in sorted(groups.items()):
        limit = ceil(len(indices) * policy.capacity_fraction)
        ranked = sorted(indices, key=lambda i: (-probabilities[i], key(rows[i])))
        for i in ranked[:limit]:
            selected[i] = True
        counts.append(dict(as_of=origin, eligible=len(indices), capacity=limit))
    return dict(**confusion(outcomes, selected, policy), per_origin=counts)


def metrics(
    rows: list[dict[str, Any]],
    outcomes: list[int],
    probabilities: list[float],
    policy: TrainingPolicy,
    *,
    train_prevalence: float,
) -> dict[str, Any]:
    if (
        not rows
        or len(rows) != len(outcomes)
        or len(rows) != len(probabilities)
        or not set(outcomes) <= {0, 1}
        or not np.isfinite(probabilities).all()
        or any(not 0 <= p <= 1 for p in probabilities)
    ):
        raise ValueError("stockout_evaluation_inputs_invalid")
    n, positives = len(rows), sum(outcomes)
    both = 0 < positives < n
    bins = []
    for b in range(5):
        indices = [i for i, p in enumerate(probabilities) if min(int(p * 5), 4) == b]
        bins.append(
            dict(
                lower=value(b / 5),
                upper=value((b + 1) / 5),
                upper_inclusive=b == 4,
                count=len(indices),
                mean_probability=value(sum(probabilities[i] for i in indices) / len(indices))
                if indices
                else None,
                observed_rate=value(sum(outcomes[i] for i in indices) / len(indices))
                if indices
                else None,
            )
        )
    return dict(
        rows=n,
        positives=positives,
        negatives=n - positives,
        status="passed" if both else "not_evaluable",
        reason=None if both else "ranking_requires_both_classes",
        average_precision=value(average_precision_score(outcomes, probabilities)) if both else None,
        PR_AUC_convention="sklearn_average_precision_non_interpolated",
        prevalence=value(positives / n),
        AP_no_skill_reference=value(positives / n) if positives else None,
        roc_auc=value(roc_auc_score(outcomes, probabilities)) if both else None,
        brier=value(sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / n),
        train_prevalence_constant_brier=value(
            sum((train_prevalence - y) ** 2 for y in outcomes) / n
        ),
        reliability=bins,
        capacity=capacity(rows, outcomes, probabilities, policy),
        threshold_costs=[
            dict(threshold=t, **confusion(outcomes, [p >= t for p in probabilities], policy))
            for t in THRESHOLDS
        ],
    )


def segmented_metrics(
    rows: list[dict[str, Any]],
    outcomes: list[int],
    probabilities: list[float],
    policy: TrainingPolicy,
    *,
    train_prevalence: float,
) -> dict[str, Any]:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        groups["all"].append(i)
        groups["stock_location:" + r["stock_location_id"]].append(i)
        groups["category:" + r["category_id"]].append(i)
        constrained = r["values"]["history_constrained_days"] > 0
        groups["historical_inventory_constraint:" + str(constrained).lower()].append(i)
    return {
        name: metrics(
            [rows[i] for i in indices],
            [outcomes[i] for i in indices],
            [probabilities[i] for i in indices],
            policy,
            train_prevalence=train_prevalence,
        )
        for name, indices in sorted(groups.items())
    }
