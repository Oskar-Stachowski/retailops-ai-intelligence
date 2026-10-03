"""Raw tune permutation diagnostics, linear coefficients and non-causal PIT facts."""

from typing import Any, Literal

import numpy as np
from pydantic import Field
from sklearn.metrics import (  # type: ignore[import-untyped]
    average_precision_score,
    brier_score_loss,
)

from retailops_ai.data_contracts.common import Contract
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeaturePoint
from retailops_ai.stockout_training.contract import CATEGORIES, LinearEstimator, RiskPipeline
from retailops_ai.stockout_training.evaluation import value
from retailops_ai.stockout_training.pipeline import (
    probabilities,
    raw_scores,
    signed_scores,
    transform,
)

MAX_CARD_BYTES = 8 * 1024**2


class ExplanationPolicy(Contract):
    version: Literal["stockout-development-explanation-1.0.0"] = (
        "stockout-development-explanation-1.0.0"
    )
    random_state: Literal[42] = 42
    repeats: Literal[5] = 5
    scoring: Literal["uncalibrated_tune_AP_and_Brier"] = "uncalibrated_tune_AP_and_Brier"
    grouping: Literal["numeric_with_missing_indicator_or_whole_one_hot_category"] = (
        "numeric_with_missing_indicator_or_whole_one_hot_category"
    )
    minimum_rows: int = Field(default=2, ge=2, le=2)


def permutation_importance(
    pipeline: RiskPipeline,
    rows: list[dict[str, Any]],
    outcomes: list[int],
    policy: ExplanationPolicy,
) -> dict[str, Any]:
    """Call only on verified tune rows. Permuted matrices are diagnostics, never serving inputs."""
    if len(rows) != len(outcomes) or len(rows) < policy.minimum_rows or set(outcomes) != {0, 1}:
        raise ValueError("stockout_importance_requires_both_classes")
    baseline = probabilities(raw_scores(pipeline, rows))  # deliberately ignores later sigmoid
    matrix = transform(rows, pipeline.preprocessing)
    baseline_ap = float(average_precision_score(outcomes, baseline))
    baseline_brier = float(brier_score_loss(outcomes, baseline))
    numeric = pipeline.preprocessing.numeric_columns
    groups: dict[str, tuple[int, ...]] = {c: (i, len(numeric) + i) for i, c in enumerate(numeric)}
    offset = 2 * len(numeric)
    for category in CATEGORIES:
        size = len(pipeline.preprocessing.category_values[category])
        groups[category] = tuple(range(offset, offset + size))
        offset += size
    # The same five permutations are used for every group, avoiding unrelated shuffle noise.
    rng = np.random.default_rng(policy.random_state)
    permutations = [rng.permutation(len(rows)) for _ in range(policy.repeats)]
    importance: list[dict[str, Any]] = []
    for name, indices in groups.items():
        ap_drop, brier_increase = [], []
        for permutation in permutations:
            perturbed = matrix.copy()
            perturbed[:, indices] = matrix[permutation][:, indices]
            scores = probabilities(signed_scores(pipeline.estimator, perturbed))
            ap_drop.append(baseline_ap - float(average_precision_score(outcomes, scores)))
            brier_increase.append(float(brier_score_loss(outcomes, scores)) - baseline_brier)
        importance.append(
            dict(
                feature_group=name,
                transformed_columns=[pipeline.preprocessing.output_columns[i] for i in indices],
                AP_drop_mean=value(float(np.mean(ap_drop))),
                AP_drop_std=value(float(np.std(ap_drop))),
                AP_drop_repeats=[value(v) for v in ap_drop],
                Brier_increase_mean=value(float(np.mean(brier_increase))),
                Brier_increase_std=value(float(np.std(brier_increase))),
                Brier_increase_repeats=[value(v) for v in brier_increase],
            )
        )
    importance.sort(key=lambda r: (-r["AP_drop_mean"], r["feature_group"]))
    return dict(
        role="tune",
        probabilities="uncalibrated",
        baseline_AP=value(baseline_ap),
        baseline_Brier=value(baseline_brier),
        rows=len(rows),
        groups=importance,
        interpretation="diagnostic_dependence_not_causal_or_independent_holdout_evidence",
        limitations=[
            "correlated_features_can_mask_or_share_importance",
            "unconditional_shuffle_can_create_physically_inconsistent_combinations",
            "repeat_std_is_shuffle_variation_not_sampling_confidence_interval",
            "tune_already_selected_model_no_new_selection_or_refitting",
        ],
    )


def coefficients(pipeline: RiskPipeline) -> dict[str, Any]:
    if not isinstance(pipeline.estimator, LinearEstimator):
        raise ValueError("stockout_coefficients_require_linear_model")
    state = pipeline.preprocessing
    numeric_size = len(state.numeric_columns)
    entries = []
    for i, (name, weight) in enumerate(
        zip(state.output_columns, pipeline.estimator.weights, strict=True)
    ):
        kind = "numeric" if i < numeric_size else "missing_indicator"
        if i >= 2 * numeric_size:
            kind = "category_indicator"
        entries.append(
            dict(
                column=name,
                coefficient=weight,
                representation=kind,
                train_center=state.means[i] if i < 2 * numeric_size else 0.0,
                train_scale=state.scales[i] if i < 2 * numeric_size else 1.0,
            )
        )
    return dict(
        intercept=pipeline.estimator.intercept,
        scale="raw_log_odds_on_transformed_columns_before_sigmoid",
        interpretation="conditional_model_weight_not_causal_effect_or_probability_change",
        categorical_reference="unknown_all_zero_regularized_full_one_hot_not_independent_effects",
        columns=entries,
    )


def factual_context(point: FeaturePoint) -> dict[str, Any]:
    """Facts at the origin, with no causal attribution, risk band or probability."""
    values = point.values
    if (
        point.status != "eligible"
        or values.snapshot_age_hours is None
        or values.snapshot_age_hours * 3600 > DEFAULT_FEATURE_POLICY.max_snapshot_age_seconds
    ):
        raise ValueError("stockout_context_requires_eligible_fresh_inventory")
    facts: list[dict[str, Any]] = [
        dict(code="known_available_stock", field="available_qty", value=values.available_qty),
        dict(
            code="inventory_snapshot_age",
            field="snapshot_age_hours",
            value=values.snapshot_age_hours,
        ),
    ]
    for field, code in (
        ("days_of_supply_observed", "supply_from_observed_sales"),
        ("days_of_supply_in_stock", "supply_from_verified_in_stock_sales"),
        ("due_within_7d_quantity", "known_delivery_plan_within_horizon"),
        ("overdue_order_quantity", "known_overdue_order_quantity"),
        ("history_constrained_days", "history_had_inventory_constraints"),
        ("history_inventory_unknown_days", "history_inventory_coverage_missing"),
    ):
        observed = getattr(values, field)
        if observed is not None and (field.startswith("days_of_supply") or observed > 0):
            facts.append(dict(code=code, field=field, value=observed))
    return dict(
        product_id=point.product_id,
        stock_location_id=point.stock_location_id,
        as_of=point.model_dump(mode="json")["as_of"],
        role="tune",
        interpretation="verified_PIT_context_not_local_model_attribution",
        facts=facts,
    )
