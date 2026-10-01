"""Development-only additive mean recipes with unchanged v11 reference forecasts.

The recipe variant is supplied by the caller, never chosen from evaluation results.
Calibration diagnostics use the same validation observations that fit the offsets;
they are not an independent qualification of the resulting forecasts.
"""

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Annotated, Any, Literal, cast

from pydantic import Field

from retailops_ai.data_contracts.common import Contract
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.functional_recipe import Observation, calibrated_band, fit_recipe
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.mean_validation_weights import FrozenMeanWeights
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast, ProtocolObservation
from retailops_ai.source_snapshot.files import SnapshotError


class FunctionalV12Policy(Contract):
    version: Literal["forecast-functional-recipe-2.0.0", "forecast-functional-recipe-3.0.0"] = (
        "forecast-functional-recipe-2.0.0"
    )
    mean_weights: FrozenMeanWeights | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    mean_variant: Literal["baseline", "zero_only", "additive", "hgb_blend"] = "additive"
    prior_strength: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 50.0
    hgb_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] = 0.5
    zero_estimation: Literal["validation_only", "train_validation_pooled"] = "validation_only"
    zero_pooling: Literal["global", "category"] = "global"
    zero_prior_strength: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 50.0
    reference_policy: FunctionalPolicy = FunctionalPolicy()
    median_and_interval: Literal["exact_v11_selected_reference"] = "exact_v11_selected_reference"
    offset_fit: Literal["all_available_validation_forecast_key_weighted_residual"] = (
        "all_available_validation_forecast_key_weighted_residual"
    )
    shrinkage_support: Literal["unique_cohort_product_location_channel_target_date"] = (
        "unique_cohort_product_location_channel_target_date"
    )
    zero_pool: Literal[
        "separate_global_zero_no_nonzero_prior_no_category_correction",
        "separate_zero_pool_optional_category_shrinkage_no_nonzero_prior",
    ] = "separate_zero_pool_optional_category_shrinkage_no_nonzero_prior"
    evaluation_use: Literal["calibration_diagnostic_not_independent_qualification"] = (
        "calibration_diagnostic_not_independent_qualification"
    )

    def model_post_init(self, context: object) -> None:
        if (self.version == "forecast-functional-recipe-3.0.0") != (self.mean_weights is not None):
            raise ValueError("recipe_version_requires_explicit_frozen_mean_weights")
        if self.mean_weights is not None and self.mean_variant != "additive":
            raise ValueError("frozen_mean_weights_require_additive_reference_correction")


@dataclass(frozen=True)
class CohortObservation:
    """Explicit namespace without changing the frozen v11 Observation contract."""

    observation: Observation
    cohort_id: str = "legacy"


Input = Observation | CohortObservation
Target = tuple[str, str, str, str, str]


def _wrap(value: Input) -> CohortObservation:
    return value if isinstance(value, CohortObservation) else CohortObservation(value)


def _target(value: CohortObservation) -> Target:
    row = value.observation
    try:
        parts = json.loads(row.key)
        origin = datetime.fromisoformat(row.origin)
        if (
            not isinstance(parts, list)
            or len(parts) != 7
            or any(not isinstance(p, str) or not p for p in parts)
            or parts[0] != row.fold
            or parts[1] != row.role
            or datetime.fromisoformat(parts[2]) != origin
            or origin.utcoffset() is None
            or parts[5] != row.channel
            or not 1 <= row.horizon <= 14
            or (date.fromisoformat(parts[6]) - origin.date()).days != row.horizon
            or not value.cohort_id
            or len(value.cohort_id) > 128
        ):
            raise ValueError("key_binding")
        return value.cohort_id, parts[3], parts[4], parts[5], parts[6]
    except (TypeError, ValueError) as exc:
        raise SnapshotError("functional_v12_target_or_cohort_binding") from exc


def _scoped(row: CohortObservation) -> Observation:
    return replace(
        row.observation,
        key=canonical_bytes([row.cohort_id, row.observation.key]).decode(),
    )


def _raw_mean(
    row: Observation, reference: FunctionalForecast, policy: FunctionalV12Policy
) -> float | None:
    value = reference.mean
    if value is None:
        return None
    if policy.mean_variant == "hgb_blend" and row.volume != "zero" and policy.hgb_weight:
        learned = row.points.get("hgb_mean")
        if learned is None:
            return None
        value = (1 - policy.hgb_weight) * value + policy.hgb_weight * learned
    if not math.isfinite(value) or value < 0:
        raise SnapshotError("functional_v12_invalid_mean_point")
    return value


def _reference_prediction(
    row: Observation, recipe: dict[str, Any]
) -> tuple[FunctionalForecast, str | None]:
    group = recipe["groups"].get(row.volume + ":" + row.category, recipe["groups"]["*:*"])
    values = {}
    for target in ("median", "mean"):
        choice = group[target]["baseline"]
        raw = row.points.get(choice["method"]) if not row.reasons else None
        values[target] = (
            max(0.0, raw * choice["ratio"] + choice["offset"]) if raw is not None else None
        )
    band, cell = calibrated_band(
        row, group["interval"]["baseline"], values["median"], recipe["calibration"]
    )
    return FunctionalForecast(median=values["median"], mean=values["mean"], interval=band), cell


def _statistics(sample: list[tuple[CohortObservation, float]]) -> dict[str, Any] | None:
    if not sample:
        return None
    targets = {_target(row): cast(int, row.observation.actual) for row, _ in sample}
    return {
        "rows": len(sample),
        "unique_targets": len(targets),
        "unique_positive_targets": sum(y > 0 for y in targets.values()),
        "actual_sum": sum(cast(int, row.observation.actual) for row, _ in sample),
        "unique_actual_sum": sum(targets.values()),
        "raw_prediction_sum": math.fsum(raw for _, raw in sample),
        "residual_sum": math.fsum(cast(int, row.observation.actual) - raw for row, raw in sample),
        "mean_residual": math.fsum(cast(int, row.observation.actual) - raw for row, raw in sample)
        / len(sample),
        "latest_label_available_at": max(
            datetime.fromisoformat(cast(str, row.observation.available)) for row, _ in sample
        ).isoformat(),
        "origin_start": min(row.observation.origin for row, _ in sample),
        "origin_end": max(row.observation.origin for row, _ in sample),
        "target_values_sha256": canonical_sha256(sorted(targets.items())),
        "independent_observations_assumed": False,
    }


def _cell(stats: dict[str, Any], parent_offset: float | None, alpha: float) -> dict[str, Any]:
    n = stats["unique_targets"]
    local_weight = 1.0 if parent_offset is None else n / (n + alpha)
    offset = local_weight * stats["mean_residual"] + (1 - local_weight) * (parent_offset or 0.0)
    return {
        "offset": offset,
        "local_weight": local_weight,
        "parent_offset": parent_offset,
        "statistics": stats,
    }


def _offsets(statistics: dict[str, Any], policy: FunctionalV12Policy) -> dict[str, Any]:
    global_stats, zero_stats = statistics.get("global"), statistics.get("zero")
    global_cell = _cell(global_stats, None, policy.prior_strength) if global_stats else None
    zero_cell = _cell(zero_stats, None, policy.prior_strength) if zero_stats else None
    groups = [json.loads(key) for key in statistics if key.startswith("[")]
    volumes: dict[str, Any] = {}
    for volume in sorted({g[0] for g in groups}):
        volume_stats = statistics.get(canonical_bytes([volume]).decode())
        if not volume_stats or global_cell is None:
            continue
        cell = _cell(volume_stats, global_cell["offset"], policy.prior_strength)
        categories = {}
        for category in sorted({g[1] for g in groups if len(g) == 2 and g[0] == volume}):
            stats = statistics[canonical_bytes([volume, category]).decode()]
            categories[category] = _cell(stats, cell["offset"], policy.prior_strength)
        volumes[volume] = cell | {"categories": categories}
    result = {"global": global_cell, "zero": zero_cell, "volumes": volumes}
    if policy.zero_pooling == "category":
        result["zero_categories"] = {
            group[1]: _cell(
                statistics[canonical_bytes(group).decode()],
                zero_cell["offset"],
                policy.zero_prior_strength,
            )
            for group in groups
            if len(group) == 2 and group[0] == "zero" and zero_cell is not None
        }
    return result


def _checked(
    rows: Sequence[Input], fold: FoldPlan, role: Literal["train", "validation"]
) -> tuple[list[CohortObservation], dict[Target, int]]:
    wrapped = sorted((_wrap(r) for r in rows), key=lambda r: (r.cohort_id, r.observation.key))
    targets: dict[Target, int] = {}
    window = fold.train if role == "train" else fold.validation
    cutoff = fold.label_cutoff(role)
    if len({(r.cohort_id, r.observation.key) for r in wrapped}) != len(wrapped):
        raise SnapshotError("functional_v12_duplicate_validation_keys_or_train_keys")
    for item in wrapped:
        row = item.observation
        if (
            row.role != role
            or row.fold != fold.name
            or row.reasons
            or type(row.actual) is not int
            or row.actual < 0
            or row.available is None
        ):
            raise SnapshotError("functional_v12_requires_eligible_" + role)
        try:
            available = datetime.fromisoformat(row.available)
            if available.utcoffset() is None or available > cutoff:
                raise ValueError("late_or_naive_label")
        except ValueError as exc:
            raise SnapshotError("functional_v12_label_not_available_at_cutoff") from exc
        target = _target(item)
        if not window.start <= datetime.fromisoformat(row.origin).date() <= window.end:
            raise SnapshotError("functional_v12_origin_outside_role_window")
        if target in targets and targets[target] != row.actual:
            raise SnapshotError("functional_v12_inconsistent_unique_target_actual")
        targets[target] = row.actual
    return wrapped, targets


def fit_recipe_v12(
    rows: Sequence[Input],
    fold: FoldPlan,
    policy: FunctionalV12Policy | None = None,
    *,
    training_rows: Sequence[Input] = (),
) -> dict[str, Any]:
    """Fit a fixed variant; optional zero pooling respects each role's own cutoff."""
    policy = policy or FunctionalV12Policy()
    wrapped, targets = _checked(rows, fold, "validation")
    training, train_targets = _checked(training_rows, fold, "train")
    if targets.keys() & train_targets.keys():
        raise SnapshotError("functional_v12_train_validation_target_overlap")

    # This call preserves the frozen baseline selection and interval calibration.
    # Its learned selections are retained in the receipt but never used by v12.
    reference = fit_recipe([_scoped(r) for r in wrapped], fold, policy.reference_policy)
    pools: dict[str, list[tuple[CohortObservation, float]]] = defaultdict(list)
    for item in wrapped:
        row = item.observation
        baseline, _ = _reference_prediction(row, reference)
        raw = _raw_mean(row, baseline, policy)
        if raw is None:
            continue
        if row.volume == "zero":
            pools["zero"].append((item, raw))
            if policy.zero_pooling == "category":
                pools[canonical_bytes(["zero", row.category]).decode()].append((item, raw))
        else:
            for key in (
                "global",
                canonical_bytes([row.volume]).decode(),
                canonical_bytes([row.volume, row.category]).decode(),
            ):
                pools[key].append((item, raw))
    if policy.zero_estimation == "train_validation_pooled":
        for item in training:
            row = item.observation
            if row.volume != "zero":
                continue
            # This is as-of history, not a model or reference selected using later validation.
            raw = row.points.get("history28:mean")
            if raw is None:
                continue
            if not math.isfinite(raw) or raw != 0.0:
                raise SnapshotError("functional_v12_zero_training_history_not_zero")
            pools["zero"].append((item, raw))
            if policy.zero_pooling == "category":
                pools[canonical_bytes(["zero", row.category]).decode()].append((item, raw))
    statistics = {name: _statistics(sample) for name, sample in sorted(pools.items())}
    zero_role_support = {
        role: _statistics(
            [item for item in pools.get("zero", []) if item[0].observation.role == role]
        )
        for role in ("train", "validation")
    }
    offsets = _offsets(statistics, policy)
    body = {
        "version": policy.version,
        "policy": policy.model_dump(mode="json"),
        "fold": fold.name,
        "selection_cutoff": fold.selection_cutoff.isoformat(),
        "reference_recipe": reference,
        "reference_selected_predictions_used": False,
        "offsets": offsets,
        "mean_sufficient_statistics": statistics,
        "zero_role_support": zero_role_support,
        "support": {
            "rows": len(wrapped),
            "unique_targets": len(targets),
            "unique_positive_targets": sum(y > 0 for y in targets.values()),
            "cohort_ids": sorted({r.cohort_id for r in wrapped}),
            "target_values_sha256": canonical_sha256(sorted(targets.items())),
        },
        "training_support": {
            "rows": len(training),
            "unique_targets": len(train_targets),
            "unique_positive_targets": sum(y > 0 for y in train_targets.values()),
            "cohort_ids": sorted({r.cohort_id for r in training}),
            "target_values_sha256": canonical_sha256(sorted(train_targets.items())),
            "used_for_zero": policy.zero_estimation == "train_validation_pooled",
        },
        "calibration_gaps": ["zero_pool_unavailable"] if offsets["zero"] is None else [],
        "evaluation_use": policy.evaluation_use,
        "quality_thresholds_changed": False,
    }
    return {**body, "recipe_id": "functional-v12-recipe-sha256-" + canonical_sha256(body)}


def _predict(
    value: Input,
    recipe: dict[str, Any],
    policy: FunctionalV12Policy,
    mean_weights: dict[tuple[str, str, str], float] | None = None,
) -> tuple[FunctionalForecast, FunctionalForecast, dict[str, Any]]:
    row = _wrap(value).observation
    baseline, calibration = _reference_prediction(row, recipe["reference_recipe"])
    mean = _raw_mean(row, baseline, policy)
    source = "exact_baseline"
    if (
        not row.reasons
        and policy.mean_variant != "baseline"
        and not (policy.mean_variant == "zero_only" and row.volume != "zero")
    ):
        offsets = recipe["offsets"]
        if row.volume == "zero":
            cell, source = offsets["zero"], "zero_pool"
            if policy.zero_pooling == "category" and row.category in offsets.get(
                "zero_categories", {}
            ):
                cell, source = offsets["zero_categories"][row.category], "zero_category"
        else:
            volume = offsets["volumes"].get(row.volume)
            cell = volume["categories"].get(row.category, volume) if volume else offsets["global"]
            source = (
                "category"
                if volume and row.category in volume["categories"]
                else "volume"
                if volume
                else "global"
            )
        weight = (
            mean_weights.get((row.fold, row.volume, row.category), 0.0)
            if mean_weights is not None and row.volume != "zero"
            else 1.0
        )
        mean = (
            max(0.0, mean + (weight * cell["offset"] if weight else 0.0))
            if mean is not None and (cell is not None or weight == 0)
            else None
        )
        if cell is None:
            source += "_unavailable"
    candidate = FunctionalForecast(median=baseline.median, mean=mean, interval=baseline.interval)
    return (
        candidate,
        baseline,
        {
            "selected": calibration,
            "baseline": calibration,
            "mean_source": source,
            "exact_reference_median": True,
            "exact_reference_interval": True,
            "recipe_id": recipe["recipe_id"],
        },
    )


def diagnose_recipe_v12(rows: Sequence[Input], recipe: dict[str, Any]) -> dict[str, Any]:
    """Keep every quality failure; calibration results cannot make a campaign ready."""
    policy = FunctionalV12Policy.model_validate_json(canonical_bytes(recipe["policy"]))
    predictor = PreparedV12Predictor(recipe)
    wrapped = [_wrap(r) for r in rows]
    if any(r.observation.role != "validation" for r in wrapped):
        raise SnapshotError("functional_v12_diagnostics_validation_only")
    protocols = []
    for item in wrapped:
        candidate, baseline, _ = predictor.predict(item)
        protocols.append(
            ProtocolObservation(
                key=_scoped(item).key,
                actual=item.observation.actual,
                exclusion_reasons=item.observation.reasons,
                candidate=candidate,
                baseline=baseline,
            )
        )
    dimensions: dict[str, list[str]] = {
        "global": ["all"],
        "horizon": [str(h) for h in range(1, 15)],
        "category": sorted({r.observation.category for r in wrapped}),
        "channel": sorted({r.observation.channel for r in wrapped}),
        "volume": list(policy.reference_policy.quality.required_volume_bins),
    }
    segments = []
    for dimension, values in dimensions.items():
        for value in values:
            sample = [
                p
                for item, p in zip(wrapped, protocols, strict=True)
                if dimension == "global" or str(getattr(item.observation, dimension)) == value
            ]
            result = assess_segment_v2(
                sample,
                dimension=cast(
                    Literal["global", "horizon", "category", "channel", "volume"], dimension
                ),
                retained_median_baseline=True,
                policy=policy.reference_policy.quality,
            )
            segments.append(result | {"fold": recipe["fold"], "value": value})
    return {
        "recipe_id": recipe["recipe_id"],
        "status": "not_ready",
        "evaluation_use": policy.evaluation_use,
        "segments": segments,
        "failed_reasons": sorted({reason for s in segments for reason in s["failed_reasons"]}),
        "not_ready_reasons": sorted(
            {reason for s in segments for reason in s["not_ready_reasons"]}
        ),
    }


def _verify_identity(recipe: dict[str, Any]) -> None:
    body = {k: v for k, v in recipe.items() if k != "recipe_id"}
    if recipe.get("recipe_id") != "functional-v12-recipe-sha256-" + canonical_sha256(body):
        raise SnapshotError("functional_v12_recipe_identity_mismatch")


def _merge_statistics(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not samples:
        return None
    rows = sum(s["rows"] for s in samples)
    residual = math.fsum(s["residual_sum"] for s in samples)
    return {
        **{
            name: sum(s[name] for s in samples)
            for name in (
                "rows",
                "unique_targets",
                "unique_positive_targets",
                "actual_sum",
                "unique_actual_sum",
            )
        },
        "raw_prediction_sum": math.fsum(s["raw_prediction_sum"] for s in samples),
        "residual_sum": residual,
        "mean_residual": residual / rows,
        "latest_label_available_at": max(
            datetime.fromisoformat(s["latest_label_available_at"]) for s in samples
        ).isoformat(),
        "origin_start": min(s["origin_start"] for s in samples),
        "origin_end": max(s["origin_end"] for s in samples),
        "target_values_sha256": canonical_sha256(
            sorted(s["target_values_sha256"] for s in samples)
        ),
        "target_hash_kind": "merkle_disjoint_cohort_target_hashes",
        "independent_observations_assumed": False,
    }


def merge_mean_calibration(recipes: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Merge bounded per-cohort sufficient statistics, keeping local baselines unchanged."""
    if not recipes:
        raise SnapshotError("functional_v12_empty_mean_calibration_merge")
    ordered = sorted(recipes, key=lambda r: r["recipe_id"])
    first = ordered[0]
    policy = FunctionalV12Policy.model_validate_json(canonical_bytes(first["policy"]))
    cohorts: set[str] = set()
    for recipe in ordered:
        _verify_identity(recipe)
        if any(recipe[k] != first[k] for k in ("policy", "fold", "selection_cutoff")):
            raise SnapshotError("functional_v12_incompatible_mean_calibration_merge")
        namespace = set(recipe["support"]["cohort_ids"]) | set(
            recipe["training_support"]["cohort_ids"]
        )
        if not namespace or namespace & cohorts or recipe.get("pooled_mean_calibration"):
            raise SnapshotError("functional_v12_cohort_overlap_or_repeated_merge")
        cohorts.update(namespace)
    names = sorted({name for recipe in ordered for name in recipe["mean_sufficient_statistics"]})
    statistics = {
        name: _merge_statistics(
            [
                r["mean_sufficient_statistics"][name]
                for r in ordered
                if name in r["mean_sufficient_statistics"]
            ]
        )
        for name in names
    }
    body = {
        "version": "functional-v12-mean-calibration-1.0.0",
        "policy": first["policy"],
        "fold": first["fold"],
        "selection_cutoff": first["selection_cutoff"],
        "cohort_ids": sorted(cohorts),
        "source_recipe_ids": [r["recipe_id"] for r in ordered],
        "mean_sufficient_statistics": statistics,
        "zero_role_support": {
            role: _merge_statistics(
                [r["zero_role_support"][role] for r in ordered if r["zero_role_support"][role]]
            )
            for role in ("train", "validation")
        },
        "offsets": _offsets(statistics, policy),
        "reference_selection": "cohort_local_v11_references_unchanged",
        "evaluation_use": policy.evaluation_use,
    }
    return {**body, "calibration_id": "functional-v12-mean-sha256-" + canonical_sha256(body)}


def bind_pooled_mean(recipe: dict[str, Any], calibration: dict[str, Any]) -> dict[str, Any]:
    """Bind one pooled mean to a retained local reference; no labels are consulted."""
    _verify_identity(recipe)
    body = {k: v for k, v in calibration.items() if k != "calibration_id"}
    if calibration.get("calibration_id") != "functional-v12-mean-sha256-" + canonical_sha256(body):
        raise SnapshotError("functional_v12_mean_calibration_identity_mismatch")
    if (
        recipe["recipe_id"] not in calibration["source_recipe_ids"]
        or any(recipe[k] != calibration[k] for k in ("policy", "fold", "selection_cutoff"))
        or recipe.get("pooled_mean_calibration")
    ):
        raise SnapshotError("functional_v12_mean_calibration_binding")
    result = {k: v for k, v in recipe.items() if k != "recipe_id"}
    result.update(
        offsets=calibration["offsets"],
        calibration_gaps=["zero_pool_unavailable"]
        if calibration["offsets"]["zero"] is None
        else [],
        pooled_mean_calibration=calibration,
        local_recipe_id=recipe["recipe_id"],
    )
    return {**result, "recipe_id": "functional-v12-recipe-sha256-" + canonical_sha256(result)}


class PreparedV12Predictor:
    """Validate an immutable receipt once, then consume a bounded stream of rows."""

    def __init__(self, recipe: dict[str, Any]) -> None:
        _verify_identity(recipe)
        self.recipe = json.loads(canonical_bytes(recipe))
        self.policy = FunctionalV12Policy.model_validate_json(canonical_bytes(recipe["policy"]))
        self.mean_weights = self.policy.mean_weights.lookup() if self.policy.mean_weights else None

    def predict(
        self, value: Input
    ) -> tuple[FunctionalForecast, FunctionalForecast, dict[str, Any]]:
        return _predict(value, self.recipe, self.policy, self.mean_weights)


def predict_pair_v12(
    value: Input, recipe: dict[str, Any]
) -> tuple[FunctionalForecast, FunctionalForecast, dict[str, Any]]:
    """Prediction reads no actuals, label availability, or test-derived parameters."""
    return PreparedV12Predictor(recipe).predict(value)
