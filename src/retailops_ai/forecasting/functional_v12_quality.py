"""Bounded streaming implementation of frozen quality 2.0, with unchanged equations.

The row limit is a technical resource limit, not a quality threshold. Campaign
qualification still needs independent frozen-source, as-of and calibration evidence.
The frozen list evaluator and all v11 artifacts remain unchanged.
"""

from __future__ import annotations

import hashlib
import math
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Self, cast

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.forecasting.quality_v2 import interval_score
from retailops_ai.forecasting.quality_v2_contract import (
    FunctionalForecast,
    ProtocolObservation,
    QualityPolicyV2,
)
from retailops_ai.source_snapshot.files import SnapshotError

Dimension = Literal["global", "horizon", "category", "channel", "volume"]
DIMENSIONS = ("global", "horizon", "category", "channel", "volume")
ROLES = ("validation", "development_holdout")
DEFAULT_MAX_ROWS = 50000000
DEFAULT_INDEX_BYTES = 2 * 1024**3


class StableSum:
    """Non-overlapping float expansion; merging retains low-order terms for math.fsum.

    Never round a chunk into one float before merging it. The expansion length is
    bounded by the finite binary64 exponent range, independently of the row count.
    """

    __slots__ = ("partials", "special")

    def __init__(self) -> None:
        self.partials: list[float] = []
        self.special = 0.0

    def add(self, value: float) -> None:
        if not math.isfinite(value):
            self.special += value
            return
        index = 0
        for other in self.partials:
            if abs(value) < abs(other):
                value, other = other, value
            high = value + other
            if not math.isfinite(high):
                raise OverflowError("intermediate overflow in fsum")
            low = other - (high - value)
            if low:
                self.partials[index] = low
                index += 1
            value = high
        self.partials[index:] = [value] if value else []
        if len(self.partials) > 2048:
            raise SnapshotError("streaming_quality_sum_expansion_budget")

    def merge(self, other: StableSum) -> None:
        for value in other.partials:
            self.add(value)
        if other.special:
            self.add(other.special)

    def value(self) -> float:
        if math.isnan(self.special):
            raise ValueError("-inf + inf in fsum")
        return self.special if self.special else math.fsum(self.partials)


@dataclass
class _Point:
    missing: int = 0
    signed: StableSum = field(default_factory=StableSum)
    absolute: StableSum = field(default_factory=StableSum)
    squared: StableSum = field(default_factory=StableSum)
    zero_excess: StableSum = field(default_factory=StableSum)

    def add(self, value: float | None, actual: int) -> None:
        if value is None:
            self.missing += 1
            return
        error = value - actual
        self.signed.add(error)
        self.absolute.add(abs(error))
        self.squared.add(error * error)
        if actual == 0:
            self.zero_excess.add(value)

    def merge(self, other: _Point) -> None:
        self.missing += other.missing
        for name in ("signed", "absolute", "squared", "zero_excess"):
            getattr(self, name).merge(getattr(other, name))

    def result(self, n: int, total: int) -> dict[str, Any]:
        complete = n > 0 and self.missing == 0
        return {
            "complete": complete,
            "mae": self.absolute.value() / n if complete else None,
            "mse": self.squared.value() / n if complete else None,
            "bias_units": self.signed.value() / n if complete else None,
            "normalized_bias": self.signed.value() / total if complete and total else None,
            "wape": self.absolute.value() / total if complete and total else None,
            "zero_actual_excess_units": self.zero_excess.value() if complete else None,
        }


@dataclass
class _Forecast:
    median: _Point = field(default_factory=_Point)
    mean: _Point = field(default_factory=_Point)
    interval_missing: int = 0
    width: StableSum = field(default_factory=StableSum)
    score: StableSum = field(default_factory=StableSum)
    covered: int = 0

    def add(self, forecast: FunctionalForecast, actual: int, nominal: float) -> None:
        self.median.add(forecast.median, actual)
        self.mean.add(forecast.mean, actual)
        band = forecast.interval
        if band is None:
            self.interval_missing += 1
        else:
            self.width.add(band.upper - band.lower)
            self.score.add(interval_score(actual, band, nominal))
            self.covered += band.lower <= actual <= band.upper

    def merge(self, other: _Forecast) -> None:
        self.median.merge(other.median)
        self.mean.merge(other.mean)
        self.interval_missing += other.interval_missing
        self.width.merge(other.width)
        self.score.merge(other.score)
        self.covered += other.covered

    def result(self, n: int, total: int) -> dict[str, Any]:
        complete = n > 0 and self.interval_missing == 0
        width = self.width.value() / n if complete else None
        return {
            "rows": n,
            "actual_sum": total,
            "median": self.median.result(n, total),
            "mean": self.mean.result(n, total),
            "interval": {
                "complete": complete,
                "mean_width": width,
                "width_to_mean_actual": width / (total / n)
                if width is not None and total
                else None,
                "mean_score": self.score.value() / n if complete else None,
                "coverage": self.covered / n if complete else None,
            },
        }


@dataclass
class _Segment:
    """Internal sufficient statistics; public entry points separately enforce key uniqueness."""

    total: int = 0
    eligible: int = 0
    actual: int = 0
    retained: bool = True
    candidate: _Forecast = field(default_factory=_Forecast)
    baseline: _Forecast = field(default_factory=_Forecast)

    def add(self, row: ProtocolObservation, retained: bool, nominal: float) -> None:
        if not row.exclusion_reasons and retained and row.candidate.median != row.baseline.median:
            raise ValueError("retained_baseline_predictions_differ")
        self.total += 1
        if row.exclusion_reasons:
            return
        actual = cast(int, row.actual)
        self.eligible += 1
        self.actual += actual
        self.retained = self.retained and retained
        self.candidate.add(row.candidate, actual, nominal)
        self.baseline.add(row.baseline, actual, nominal)

    def merge(self, other: _Segment) -> None:
        self.total += other.total
        self.eligible += other.eligible
        self.actual += other.actual
        self.retained = self.retained and other.retained
        self.candidate.merge(other.candidate)
        self.baseline.merge(other.baseline)


def _assess(
    segment: _Segment, dimension: Dimension, retained: bool, policy: QualityPolicyV2
) -> dict[str, Any]:
    """Gate equations deliberately match the frozen assess_segment_v2, including order."""
    if dimension not in DIMENSIONS:
        raise ValueError("unknown_quality_dimension")
    candidate = segment.candidate.result(segment.eligible, segment.actual)
    baseline = segment.baseline.result(segment.eligible, segment.actual)
    failed: list[str] = []
    unavailable: list[str] = []
    minimum = policy.minimum_global_rows if dimension == "global" else policy.minimum_segment_rows
    if segment.eligible < minimum:
        unavailable.append("insufficient_sample")
    eligibility = segment.eligible / segment.total if segment.total else None
    if eligibility is None:
        unavailable.append("eligibility_coverage_not_evaluable")
    elif eligibility < policy.minimum_eligibility_coverage:
        failed.append("eligibility_coverage_below_minimum")
    for component in ("median", "mean", "interval"):
        if not candidate[component]["complete"] or not baseline[component]["complete"]:
            unavailable.append(component + "_predictions_incomplete_or_empty")
    mae, reference = candidate["median"]["mae"], baseline["median"]["mae"]
    relative = (mae - reference) / reference if mae is not None and reference else None
    if mae is not None and reference is not None:
        if reference == 0:
            if mae > 0:
                failed.append("median_mae_regression_from_perfect_baseline")
        elif dimension == "global" and not retained:
            if mae >= reference * (1 - policy.minimum_relative_mae_improvement):
                failed.append("global_median_mae_improvement_below_minimum")
        elif mae > reference * (1 + policy.maximum_segment_mae_regression):
            failed.append("critical_segment_median_mae_regression")
    mse, reference_mse = candidate["mean"]["mse"], baseline["mean"]["mse"]
    if mse is not None and reference_mse is not None and mse > reference_mse:
        failed.append("mean_mse_regression")
    bias = candidate["mean"]["normalized_bias"]
    if bias is not None:
        if abs(bias) > policy.maximum_absolute_normalized_mean_bias:
            failed.append("absolute_normalized_mean_bias_exceeded")
    elif candidate["mean"]["complete"] and candidate["mean"]["zero_actual_excess_units"] > 0:
        failed.append("positive_mean_forecast_on_all_zero_actuals")
    band, reference_band = candidate["interval"], baseline["interval"]
    if band["coverage"] is not None and band["coverage"] < policy.minimum_empirical_coverage:
        failed.append("empirical_interval_coverage_below_minimum")
    if (
        band["mean_score"] is not None
        and reference_band["mean_score"] is not None
        and band["mean_score"] > reference_band["mean_score"]
    ):
        failed.append("interval_score_regression")
    ratio = band["width_to_mean_actual"]
    mean_mae, baseline_mean_mae = candidate["mean"]["mae"], baseline["mean"]["mae"]
    median_bias = candidate["median"]["normalized_bias"]
    return {
        "protocol_version": policy.version,
        "scope": "segment_component_not_campaign_qualification",
        "dimension": dimension,
        "eligible_rows": segment.eligible,
        "total_rows": segment.total,
        "eligibility_coverage": eligibility,
        "candidate": candidate,
        "baseline": baseline,
        "relative_median_mae_change": relative,
        "perfect_median_baseline_tied": reference == 0 and mae == 0,
        "diagnostics": {
            "mean_mae_above_legacy_regression_limit": mean_mae
            > baseline_mean_mae * (1 + policy.maximum_segment_mae_regression)
            if mean_mae is not None and baseline_mean_mae is not None
            else None,
            "median_bias_above_legacy_mean_limit": abs(median_bias)
            > policy.maximum_absolute_normalized_mean_bias
            if median_bias is not None
            else None,
        },
        "legacy_width_ratio_exceeded": ratio > policy.legacy_width_ratio_threshold
        if ratio is not None
        else None,
        "status": "not_ready" if unavailable else "failed" if failed else "passed",
        "has_measurable_failures": bool(failed),
        "not_ready_reasons": unavailable,
        "failed_reasons": failed,
    }


class _UniqueKeys:
    """Disk-bounded exact-identity digests; a hash collision can only reject, never admit."""

    def __init__(self, directory: Path | None, maximum_rows: int, maximum_bytes: int) -> None:
        if maximum_rows < 1 or maximum_bytes < 1024**2:
            raise ValueError("streaming_quality_invalid_resource_budget")
        volume = directory or Path(tempfile.gettempdir())
        if shutil.disk_usage(volume).free < maximum_bytes + 1024**3:
            raise SnapshotError("streaming_quality_dedup_space_budget")
        self.temporary = tempfile.TemporaryDirectory(prefix="forecast-quality-v12-", dir=directory)
        self.db = sqlite3.connect(Path(self.temporary.name) / "keys.sqlite")
        self.db.execute("PRAGMA cache_size=-4096")
        self.db.execute("PRAGMA temp_store=FILE")
        page = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={maximum_bytes // page}")
        self.db.execute("CREATE TABLE keys (digest BLOB PRIMARY KEY) WITHOUT ROWID")
        self.maximum_rows = maximum_rows
        self.maximum_bytes = maximum_bytes
        self.count = 0

    def add(self, identity: object) -> None:
        if self.count >= self.maximum_rows:
            raise SnapshotError("streaming_quality_technical_row_budget")
        digest = hashlib.sha256(canonical_bytes(identity)).digest()
        try:
            self.db.execute("INSERT INTO keys VALUES (?)", (digest,))
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate_quality_key") from exc
        except sqlite3.OperationalError as exc:
            raise SnapshotError("streaming_quality_dedup_index_budget_or_io") from exc
        self.count += 1
        if self.count % 10000 == 0:
            self.db.commit()

    def close(self) -> None:
        self.db.close()
        self.temporary.cleanup()


def assess_segment_streaming(
    observations: Iterable[ProtocolObservation],
    *,
    dimension: Dimension,
    retained_median_baseline: bool = False,
    policy: QualityPolicyV2 | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_index_bytes: int = DEFAULT_INDEX_BYTES,
    temporary_directory: Path | None = None,
) -> dict[str, Any]:
    """Public pure-metric counterpart of assess_segment_v2, without its list-size limit."""
    if dimension not in DIMENSIONS:
        raise ValueError("unknown_quality_dimension")
    policy = policy or QualityPolicyV2()
    segment = _Segment()
    unique = _UniqueKeys(temporary_directory, max_rows, max_index_bytes)
    try:
        for row in observations:
            unique.add(row.key)
            segment.add(row, retained_median_baseline, policy.nominal_coverage)
        return _assess(segment, dimension, retained_median_baseline, policy)
    finally:
        unique.close()


@dataclass(frozen=True)
class CampaignObservation:
    cohort_id: str
    fold: str
    role: Literal["validation", "development_holdout"]
    horizon: int
    category: str
    channel: str
    volume: str
    observation: ProtocolObservation
    retained_median_baseline: bool
    candidate_calibration_rows: int | None
    baseline_calibration_rows: int | None


@dataclass
class _Calibration:
    minimum_rows: int | None = None
    missing_evidence_rows: int = 0

    def add(self, rows: int | None) -> None:
        if rows is None:
            self.missing_evidence_rows += 1
        else:
            self.minimum_rows = rows if self.minimum_rows is None else min(self.minimum_rows, rows)

    def merge(self, other: _Calibration) -> None:
        if other.minimum_rows is not None:
            self.add(other.minimum_rows)
        self.missing_evidence_rows += other.missing_evidence_rows


@dataclass
class _Cell:
    segment: _Segment = field(default_factory=_Segment)
    candidate_calibration: _Calibration = field(default_factory=_Calibration)
    baseline_calibration: _Calibration = field(default_factory=_Calibration)

    def merge(self, other: _Cell) -> None:
        self.segment.merge(other.segment)
        self.candidate_calibration.merge(other.candidate_calibration)
        self.baseline_calibration.merge(other.baseline_calibration)


class StreamingCampaignScorer:
    """One pass over cohorts, fixed leaf inventory, then exact pooled sufficient-statistic merges.

    Call finalize for the complete metric report. A technical failure is raised when it
    happens, and finalize still exposes prefix metrics as not_ready, never partial success.
    No input forecast is changed to compensate for missing calibration evidence.
    """

    def __init__(
        self,
        *,
        cohorts: Sequence[str],
        folds: Sequence[str],
        dimensions: Mapping[str, Sequence[str]],
        policy: QualityPolicyV2 | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_index_bytes: int = DEFAULT_INDEX_BYTES,
        max_leaf_cells: int = 50000,
        temporary_directory: Path | None = None,
    ) -> None:
        self.policy = policy or QualityPolicyV2()
        self.cohorts, self.folds = tuple(cohorts), tuple(folds)
        if (
            not self.cohorts
            or not self.folds
            or len(self.cohorts) > 256
            or len(self.folds) > 10
            or len(set(self.cohorts)) != len(self.cohorts)
            or len(set(self.folds)) != len(self.folds)
            or "pooled" in self.folds
            or set(dimensions) != {"category", "channel", "volume"}
            or any(not value or len(value) > 256 for value in (*self.cohorts, *self.folds))
        ):
            raise ValueError("streaming_quality_invalid_campaign_inventory")
        self.required = {
            "global": ["all"],
            "horizon": [str(i) for i in range(1, 15)],
            "category": sorted(set(dimensions["category"])),
            "channel": sorted(set(dimensions["channel"])),
            "volume": sorted(set(dimensions["volume"]) | set(self.policy.required_volume_bins)),
        }
        possible = (
            len(self.folds) * len(ROLES) * math.prod(len(self.required[d]) for d in DIMENSIONS[1:])
        )
        if not all(self.required.values()) or possible > max_leaf_cells or max_leaf_cells < 1:
            raise SnapshotError("streaming_quality_leaf_inventory_budget")
        self.cells: dict[tuple[str, str, str, str, str, str], _Cell] = {}
        self.unique = _UniqueKeys(temporary_directory, max_rows, max_index_bytes)
        self.failure: str | None = None
        self.finished = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.unique.close()

    def add(self, row: CampaignObservation) -> None:
        if self.failure or self.finished:
            raise SnapshotError("streaming_quality_scorer_already_terminal")
        try:
            if (
                row.cohort_id not in self.cohorts
                or row.fold not in self.folds
                or row.role not in ROLES
                or type(row.retained_median_baseline) is not bool
                or type(row.horizon) is not int
                or str(row.horizon) not in self.required["horizon"]
                or row.category not in self.required["category"]
                or row.channel not in self.required["channel"]
                or row.volume not in self.required["volume"]
                or any(
                    value is not None and (type(value) is not int or value < 0)
                    for value in (row.candidate_calibration_rows, row.baseline_calibration_rows)
                )
            ):
                raise ValueError("streaming_quality_row_outside_frozen_inventory")
            if (
                not row.observation.exclusion_reasons
                and row.retained_median_baseline
                and row.observation.candidate.median != row.observation.baseline.median
            ):
                raise ValueError("retained_baseline_predictions_differ")
            self.unique.add([row.cohort_id, row.fold, row.role, row.observation.key])
            key = (row.fold, row.role, str(row.horizon), row.category, row.channel, row.volume)
            cell = self.cells.get(key)
            if cell is None:
                cell = _Cell()
                self.cells[key] = cell
            cell.segment.add(
                row.observation, row.retained_median_baseline, self.policy.nominal_coverage
            )
            if not row.observation.exclusion_reasons:
                if row.observation.candidate.interval is not None:
                    cell.candidate_calibration.add(row.candidate_calibration_rows)
                if row.observation.baseline.interval is not None:
                    cell.baseline_calibration.add(row.baseline_calibration_rows)
        except (ValueError, OverflowError, sqlite3.Error) as exc:
            self.failure = str(exc)
            raise

    def finalize(self) -> dict[str, Any]:
        self.finished = True
        aggregates: dict[tuple[str, str, str, str], _Cell] = {}
        for key, cell in sorted(self.cells.items()):
            fold, role, horizon, category, channel, volume = key
            leaf_values = ("all", horizon, category, channel, volume)
            for target_fold in (fold, "pooled"):
                for dimension, value in zip(DIMENSIONS, leaf_values, strict=True):
                    aggregate = aggregates.setdefault(
                        (target_fold, role, dimension, value), _Cell()
                    )
                    aggregate.merge(cell)
        segments = []
        for fold in (*self.folds, "pooled"):
            for role in ROLES:
                for dimension, values in self.required.items():
                    for value in values:
                        cell = aggregates.get((fold, role, dimension, value), _Cell())
                        result = _assess(
                            cell.segment,
                            cast(Dimension, dimension),
                            cell.segment.retained,
                            self.policy,
                        )
                        calibration = {}
                        for side in ("candidate", "baseline"):
                            evidence = cast(_Calibration, getattr(cell, side + "_calibration"))
                            calibration[side] = {
                                "minimum_rows": evidence.minimum_rows,
                                "missing_evidence_rows": evidence.missing_evidence_rows,
                            }
                            if (
                                evidence.missing_evidence_rows
                                or evidence.minimum_rows is not None
                                and evidence.minimum_rows < self.policy.minimum_calibration_rows
                            ):
                                result["not_ready_reasons"].append(
                                    side + "_interval_calibration_evidence_below_minimum"
                                )
                                result["status"] = "not_ready"
                        segments.append(
                            {
                                "fold": fold,
                                "role": role,
                                "value": value,
                                "retained_median_baseline": cell.segment.retained,
                                **result,
                                "calibration_evidence": calibration,
                            }
                        )
        counts = dict(Counter(s["status"] for s in segments))
        failures = dict(Counter(reason for s in segments for reason in s["failed_reasons"]))
        return {
            "status": "passed"
            if not self.failure and all(s["status"] == "passed" for s in segments)
            else "not_ready",
            "scope": "streaming_campaign_metrics_requires_frozen_independent_lineage_qualification",
            "input_complete": self.failure is None,
            "execution_failure": self.failure,
            "processed_rows": self.unique.count,
            "leaf_cells": len(self.cells),
            "technical_limits": {
                "maximum_rows": self.unique.maximum_rows,
                "maximum_index_bytes": self.unique.maximum_bytes,
            },
            "segment_counts": counts,
            "failed_reasons": failures,
            "segments": segments,
            "required_segment_inventory": self.required,
            "required_folds": [*self.folds, "pooled"],
            "required_roles": list(ROLES),
            "cohorts": list(self.cohorts),
            "portfolio_final_test": "not_included_not_opened",
            "legacy_results_reclassified": False,
        }
