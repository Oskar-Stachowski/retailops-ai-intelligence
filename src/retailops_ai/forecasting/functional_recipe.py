"""As-of empirical baselines and two-block, validation-only functional selection."""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, cast

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.features_contract import HistoryContext, InputRow
from retailops_ai.forecasting.functional_contract import BASELINES, FunctionalPolicy
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.quality_v2 import interval_score
from retailops_ai.forecasting.quality_v2_contract import CentralInterval, FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


@dataclass(frozen=True)
class Observation:
    key: str
    fold: str
    role: str
    origin: str
    volume: str
    category: str
    channel: str
    horizon: int
    reasons: tuple[str, ...]
    actual: int | None
    available: str | None
    points: dict[str, float | None]
    bands: dict[str, tuple[float, float] | None]


def empirical_baselines(
    row: InputRow, history: HistoryContext
) -> tuple[dict[str, float | None], dict[str, tuple[float, float] | None]]:
    if row.history_context_sha256 != history.content_sha256():
        raise SnapshotError("functional_baseline_history_binding")
    known = [p for p in history.points if p.observed_units is not None]
    points: dict[str, float | None] = {}
    bands: dict[str, tuple[float, float] | None] = {}
    for name in BASELINES:
        sample = sorted(
            float(cast(int, p.observed_units))
            for p in known
            if (
                name == "history28"
                or name == "history7"
                and p.business_date >= row.forecast_origin.date() - timedelta(days=6)
                or name == "weekday28"
                and p.business_date.weekday() == row.target_date.weekday()
            )
        )
        points[name + ":median"] = float(median(sample)) if sample else None
        points[name + ":mean"] = math.fsum(sample) / len(sample) if sample else None
        bands[name] = (
            (
                sample[max(0, math.ceil(len(sample) * 0.05) - 1)],
                sample[math.ceil(len(sample) * 0.95) - 1],
            )
            if sample
            else None
        )
    return points, bands


def _point(row: Observation, choice: dict[str, Any]) -> float | None:
    raw = row.points.get(choice["method"])
    return max(0.0, raw * choice["ratio"] + choice["offset"]) if raw is not None else None


def _metric(
    rows: list[Observation], choice: dict[str, Any], target: str
) -> tuple[float, float | None] | None:
    if not rows:
        return None
    errors = []
    actual = 0.0
    for row in rows:
        value = _point(row, choice)
        if value is None or row.actual is None:
            return None
        errors.append(value - row.actual)
        actual += row.actual
    cost = math.fsum(abs(e) if target == "median" else e * e for e in errors) / len(rows)
    bias = math.fsum(errors) / actual if actual else 0.0 if not any(errors) else None
    return cost, bias


def identity(method: str) -> dict[str, Any]:
    return {"method": method, "ratio": 1.0, "offset": 0.0, "correction": "identity"}


def _choose_point(
    first: list[Observation], second: list[Observation], target: str, policy: FunctionalPolicy
) -> dict[str, Any]:
    names = [name + ":" + target for name in BASELINES]
    baseline_options = [
        (m[0], i, identity(name))
        for i, name in enumerate(names)
        if (m := _metric(second, identity(name), target)) is not None
    ]
    base = min(baseline_options, key=lambda x: x[:2])[2] if baseline_options else identity(names[0])
    maybe_reference = [_metric(block, base, target) for block in (first, second)]
    if min(len(first), len(second)) < policy.minimum_group_rows or any(
        m is None for m in maybe_reference
    ):
        return {
            "baseline": base,
            "selected": base,
            "reason": "sparse_or_incomplete_validation_fallback",
        }
    reference = cast(list[tuple[float, float | None]], maybe_reference)
    names += ["hgb_median"] if target == "median" else ["rf_mean", "hgb_mean"]
    options = []
    for index, name in enumerate(names):
        maybe_raw = [row.points.get(name) for row in first]
        if any(value is None for value in maybe_raw):
            continue
        maybe_actuals = [row.actual for row in first]
        if any(value is None for value in maybe_actuals):
            raise SnapshotError("functional_calibration_actual_missing")
        raw = cast(list[float], maybe_raw)
        actuals = cast(list[int], maybe_actuals)
        corrected = identity(name)
        corrected["correction"] = "median_residual" if target == "median" else "mean_ratio"
        if target == "median":
            corrected["offset"] = float(median(y - p for y, p in zip(actuals, raw, strict=True)))
        elif math.fsum(raw) == 0:
            corrected["offset"] = math.fsum(actuals) / len(actuals)
        else:
            corrected["ratio"] = min(
                policy.maximum_correction_ratio,
                max(policy.minimum_correction_ratio, math.fsum(actuals) / math.fsum(raw)),
            )
        for mode, choice in enumerate((identity(name), corrected)):
            maybe_metrics = [_metric(block, choice, target) for block in (first, second)]
            if any(m is None for m in maybe_metrics):
                continue
            metrics = cast(list[tuple[float, float | None]], maybe_metrics)
            if all(
                m[0]
                <= ref[0]
                * (1 + policy.quality.maximum_segment_mae_regression if target == "median" else 1)
                and (
                    target == "median"
                    or m[1] is not None
                    and abs(m[1]) <= policy.quality.maximum_absolute_normalized_mean_bias
                )
                for m, ref in zip(metrics, reference, strict=True)
            ):
                options.append((metrics[1][0], mode, index, choice))
    selected = min(options, key=lambda x: x[:3])[3] if options else base
    selected_metric = _metric(second, selected, target)
    if target == "median" and selected_metric is not None and selected_metric[0] >= reference[1][0]:
        selected = base
    return {
        "baseline": base,
        "selected": selected,
        "reason": "validation_guarded_selection" if selected != base else "baseline_retained",
        "calibration_rows": len(first),
        "selection_rows": len(second),
    }


def _calibrate(rows: list[Observation], policy: FunctionalPolicy) -> dict[str, Any]:
    pools: dict[str, list[Observation]] = defaultdict(list)
    for row in rows:
        for volume, category in ((row.volume, row.category), (row.volume, "*"), ("*", "*")):
            pools[volume + ":" + category].append(row)
    calibrated = {}
    for group, sample in sorted(pools.items()):
        n = len(sample)
        low, high = math.floor((n + 1) * 0.05), math.ceil((n + 1) * 0.95)
        for name in (*BASELINES, "hgb"):
            if (
                n < policy.quality.minimum_calibration_rows
                or not 1 <= low <= high <= n
                or any(row.bands.get(name) is None for row in sample)
            ):
                continue
            lo = sorted(
                cast(int, row.actual) - cast(tuple[float, float], row.bands[name])[0]
                for row in sample
            )
            hi = sorted(
                cast(int, row.actual) - cast(tuple[float, float], row.bands[name])[1]
                for row in sample
            )
            calibrated[group + ":" + name] = {
                "rows": n,
                "lower_shift": lo[low - 1],
                "upper_shift": hi[high - 1],
                "sample_sha256": canonical_sha256(
                    [(r.key, r.actual, r.bands[name], r.available) for r in sample]
                ),
            }
    return calibrated


def calibrated_band(
    row: Observation, method: str, point: float | None, cells: dict[str, Any]
) -> tuple[CentralInterval | None, str | None]:
    raw = row.bands.get(method)
    if raw is None or point is None:
        return None, None
    for group in (row.volume + ":" + row.category, row.volume + ":*", "*:*"):
        cell = cells.get(group + ":" + method)
        if cell:
            lo = max(0.0, min(point, raw[0] + cell["lower_shift"]))
            hi = max(point, lo, raw[1] + cell["upper_shift"])
            return CentralInterval(lower=lo, upper=hi), group + ":" + method
    return None, None


def _band_metric(
    rows: list[Observation], name: str, choices: dict[str, Any], cells: dict[str, Any], side: str
) -> tuple[float, float] | None:
    scores = []
    covered = 0
    for row in rows:
        band, _ = calibrated_band(row, name, _point(row, choices["median"][side]), cells)
        if band is None or row.actual is None:
            return None
        scores.append(interval_score(row.actual, band))
        covered += band.lower <= row.actual <= band.upper
    return (math.fsum(scores) / len(scores), covered / len(scores)) if scores else None


def fit_recipe(rows: list[Observation], fold: FoldPlan, policy: FunctionalPolicy) -> dict[str, Any]:
    if len({r.key for r in rows}) != len(rows):
        raise SnapshotError("functional_duplicate_validation_keys")
    if any(
        r.fold != fold.name
        or r.role != "validation"
        or r.reasons
        or r.actual is None
        or r.available is None
        or datetime.fromisoformat(r.available) > fold.selection_cutoff
        or not fold.validation.start
        <= datetime.fromisoformat(r.origin).date()
        <= fold.validation.end
        for r in rows
    ):
        raise SnapshotError("functional_recipe_requires_only_available_validation_labels")
    boundary = fold.validation.start + timedelta(
        days=((fold.validation.end - fold.validation.start).days + 1) // 2
    )
    first = [r for r in rows if datetime.fromisoformat(r.origin).date() < boundary]
    second = [r for r in rows if datetime.fromisoformat(r.origin).date() >= boundary]
    if not first or not second:
        raise SnapshotError("functional_validation_requires_two_nonempty_blocks")
    cells = _calibrate(first, policy)
    groups = sorted({(r.volume, r.category) for r in rows} | {("*", "*")})
    recipes = {}
    for volume, category in groups:
        blocks = [
            [r for r in block if volume == "*" or (r.volume, r.category) == (volume, category)]
            for block in (first, second)
        ]
        recipes[volume + ":" + category] = {
            target: _choose_point(blocks[0], blocks[1], target, policy)
            for target in ("median", "mean")
        }

    def choice(row: Observation) -> dict[str, Any]:
        return recipes.get(row.volume + ":" + row.category, recipes["*:*"])

    if any(
        _point(r, choice(r)["median"][side]) is None
        for r in second
        for side in ("baseline", "selected")
    ):
        raise SnapshotError("functional_incomplete_validation_median_baseline")
    base_mae = math.fsum(
        abs(cast(float, _point(r, choice(r)["median"]["baseline"])) - cast(int, r.actual))
        for r in second
    ) / len(second)
    selected_mae = math.fsum(
        abs(cast(float, _point(r, choice(r)["median"]["selected"])) - cast(int, r.actual))
        for r in second
    ) / len(second)
    if selected_mae >= base_mae * (1 - policy.quality.minimum_relative_mae_improvement):
        for group in recipes.values():
            group["median"]["selected"] = group["median"]["baseline"]
            group["median"]["reason"] = "global_validation_improvement_fallback"
    for volume, category in groups:
        group = recipes[volume + ":" + category]
        sample = [
            r for r in second if volume == "*" or (r.volume, r.category) == (volume, category)
        ]
        options = [
            (m[0], i, name)
            for i, name in enumerate(BASELINES)
            if (m := _band_metric(sample, name, group, cells, "baseline")) is not None
        ]
        base = min(options, key=lambda x: x[:2])[2] if options else BASELINES[0]
        ref = _band_metric(sample, base, group, cells, "baseline")
        options = []
        if ref and len(sample) >= policy.minimum_group_rows:
            for i, name in enumerate((*BASELINES, "hgb")):
                score = _band_metric(sample, name, group, cells, "selected")
                if (
                    score
                    and score[0] <= ref[0]
                    and score[1] >= policy.quality.minimum_empirical_coverage
                ):
                    options.append((score[0], i, name))
        group["interval"] = {
            "baseline": base,
            "selected": min(options, key=lambda x: x[:2])[2] if options else base,
        }
    body = {
        "fold": fold.name,
        "selection_cutoff": fold.selection_cutoff.isoformat(),
        "partition_boundary": boundary.isoformat(),
        "calibration_rows": len(first),
        "selection_rows": len(second),
        "validation_keys_sha256": canonical_sha256([r.key for r in rows]),
        "groups": recipes,
        "calibration": cells,
    }
    return {**body, "recipe_id": "functional-recipe-sha256-" + canonical_sha256(body)}


def predict_pair(
    row: Observation, recipe: dict[str, Any]
) -> tuple[FunctionalForecast, FunctionalForecast, dict[str, Any]]:
    group = recipe["groups"].get(row.volume + ":" + row.category, recipe["groups"]["*:*"])
    values = []
    calibration = {}
    for side in ("selected", "baseline"):
        median_point = _point(row, group["median"][side]) if not row.reasons else None
        mean_point = _point(row, group["mean"][side]) if not row.reasons else None
        band, calibration[side] = calibrated_band(
            row, group["interval"][side], median_point, recipe["calibration"]
        )
        values.append(FunctionalForecast(median=median_point, mean=mean_point, interval=band))
    return values[0], values[1], calibration
