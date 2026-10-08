"""Paired sufficient-statistic cluster diagnostics, separate from campaign access gates.

Time blocks and whole series are two separate sensitivity reports, not a joint
multiway bootstrap. The caller must prove the complete segment census. This
component neither finds segments nor provides final-label access permission.
"""

import hashlib
import json
import math
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPrediction,
)
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    METRICS,
    CampaignForecastUncertaintyPolicy,
    CampaignForecastUncertaintyReport,
    CampaignPairedConfidenceMetric,
    CampaignPairedMethodReport,
    CampaignUncertaintyScope,
    Method,
    Metric,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.functional_v12_quality import StableSum
from retailops_ai.forecasting.quality_v2_contract import CentralInterval
from retailops_ai.source_snapshot.files import SnapshotError

SUM_FIELDS = (
    "median_absolute_delta",
    "reference_median_absolute",
    "mean_squared_delta",
    "mean_signed_delta",
    "interval_score_delta",
)
COUNT_FIELDS = (
    "rows",
    "eligible",
    "actual_units",
    "missing_median",
    "missing_mean",
    "missing_interval",
    "interval_covered_delta",
)


def _score(band: CentralInterval, actual: int, nominal: float) -> float:
    result = band.upper - band.lower
    penalty = 2.0 / (1.0 - nominal)
    if actual < band.lower:
        result += penalty * (band.lower - actual)
    elif actual > band.upper:
        result += penalty * (actual - band.upper)
    return result


@dataclass
class _PairedSums:
    rows: int = 0
    eligible: int = 0
    actual_units: int = 0
    missing_median: int = 0
    missing_mean: int = 0
    missing_interval: int = 0
    interval_covered_delta: int = 0
    sums: dict[str, StableSum] = field(default_factory=lambda: {n: StableSum() for n in SUM_FIELDS})

    def add(
        self, row: CampaignForecastEvaluationPrediction, actual: int | None, nominal: float
    ) -> None:
        self.rows += 1
        if not row.eligible:
            return
        if actual is None:
            raise SnapshotError("campaign_uncertainty_eligible_actual_missing")
        self.eligible += 1
        self.actual_units += actual
        candidate, reference = row.candidate, row.reference
        if candidate.median is None or reference.median is None:
            self.missing_median += 1
        else:
            c, r = abs(candidate.median - actual), abs(reference.median - actual)
            self.sums["median_absolute_delta"].add(c - r)
            self.sums["reference_median_absolute"].add(r)
        if candidate.mean is None or reference.mean is None:
            self.missing_mean += 1
        else:
            c, r = candidate.mean - actual, reference.mean - actual
            self.sums["mean_squared_delta"].add(c * c - r * r)
            self.sums["mean_signed_delta"].add(c - r)
        if candidate.interval is None or reference.interval is None:
            self.missing_interval += 1
        else:
            c_band, r_band = candidate.interval, reference.interval
            self.sums["interval_score_delta"].add(
                _score(c_band, actual, nominal) - _score(r_band, actual, nominal)
            )
            self.interval_covered_delta += int(c_band.lower <= actual <= c_band.upper) - int(
                r_band.lower <= actual <= r_band.upper
            )
        if any(not math.isfinite(s.value()) for s in self.sums.values()):
            raise SnapshotError("campaign_uncertainty_nonfinite_paired_statistics")

    def merge(self, other: Self) -> None:
        for name in COUNT_FIELDS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for name in SUM_FIELDS:
            self.sums[name].merge(other.sums[name])

    def payload(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in COUNT_FIELDS} | {
            "sums": {name: self.sums[name].partials for name in SUM_FIELDS}
        }

    @classmethod
    def decode(cls, raw: bytes) -> Self:
        if len(raw) > 512 * 1024:
            raise SnapshotError("campaign_uncertainty_cluster_payload_budget")
        data = json.loads(raw)
        if (
            not isinstance(data, dict)
            or set(data) != {*COUNT_FIELDS, "sums"}
            or canonical_bytes(data) != raw
            or not isinstance(data["sums"], dict)
            or set(data["sums"]) != set(SUM_FIELDS)
            or any(type(data[n]) is not int for n in COUNT_FIELDS)
            or any(data[n] < 0 for n in COUNT_FIELDS if n != "interval_covered_delta")
            or not 0 <= data["eligible"] <= data["rows"]
            or any(
                data[n] > data["eligible"]
                for n in ("missing_median", "missing_mean", "missing_interval")
            )
            or abs(data["interval_covered_delta"]) > data["eligible"]
        ):
            raise SnapshotError("campaign_uncertainty_invalid_cluster_statistics")
        result = cls(**{n: data[n] for n in COUNT_FIELDS})
        for name in SUM_FIELDS:
            parts = data["sums"][name]
            if (
                not isinstance(parts, list)
                or len(parts) > 2048
                or any(type(v) is not float or not math.isfinite(v) for v in parts)
            ):
                raise SnapshotError("campaign_uncertainty_invalid_sum_expansion")
            for value in parts:
                result.sums[name].add(value)
        return result

    def metrics(self) -> dict[Metric, tuple[float | None, str | None]]:
        def ratio(
            numerator: float, denominator: int | float, reason: str
        ) -> tuple[float | None, str | None]:
            if not denominator:
                return None, reason
            value = numerator / denominator
            return (value, None) if math.isfinite(value) else (None, "nonfinite_metric")

        values: dict[Metric, tuple[float | None, str | None]] = {}
        median_delta = self.sums["median_absolute_delta"].value()
        median_metrics: tuple[tuple[Metric, int | float], ...] = (
            ("median_mae_delta", self.eligible),
            ("median_wape_delta", self.actual_units),
            ("relative_median_mae_change", self.sums["reference_median_absolute"].value()),
        )
        for metric, denominator in median_metrics:
            values[metric] = (
                (None, "no_eligible_rows")
                if not self.eligible
                else (None, "missing_median_prediction")
                if self.missing_median
                else ratio(
                    median_delta,
                    denominator,
                    "zero_reference_error"
                    if metric == "relative_median_mae_change"
                    else "zero_actual_units",
                )
            )
        mean_metrics: tuple[tuple[Metric, float, int], ...] = (
            ("mean_mse_delta", self.sums["mean_squared_delta"].value(), self.eligible),
            (
                "normalized_mean_bias_delta",
                self.sums["mean_signed_delta"].value(),
                self.actual_units,
            ),
        )
        for metric, numerator, denominator in mean_metrics:
            values[metric] = (
                (None, "no_eligible_rows")
                if not self.eligible
                else (None, "missing_mean_prediction")
                if self.missing_mean
                else ratio(numerator, denominator, "zero_actual_units")
            )
        interval_metrics: tuple[tuple[Metric, float], ...] = (
            ("interval_score_delta", self.sums["interval_score_delta"].value()),
            ("interval_coverage_delta", float(self.interval_covered_delta)),
        )
        for metric, numerator in interval_metrics:
            values[metric] = (
                (None, "no_eligible_rows")
                if not self.eligible
                else (None, "missing_interval_prediction")
                if self.missing_interval
                else ratio(numerator, self.eligible, "no_eligible_rows")
            )
        return values


class PairedForecastUncertainty:
    """Private disposable SQLite sums; no individual actuals or residual list in memory.

    Index and cluster caps bound this component. The eventual public worker must
    still enforce its complete process-tree RSS, scratch and time budgets.
    """

    def __init__(
        self,
        path: Path,
        scope: CampaignUncertaintyScope,
        policy: CampaignForecastUncertaintyPolicy | None = None,
    ) -> None:
        self.scope = CampaignUncertaintyScope.model_validate_json(scope.model_dump_json())
        self.policy = CampaignForecastUncertaintyPolicy.model_validate_json(
            (policy or CampaignForecastUncertaintyPolicy()).model_dump_json()
        )
        self.path = path
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-4096")
        self.db.execute("PRAGMA temp_store=FILE")
        self.db.execute(
            "CREATE TABLE clusters(method TEXT, identity TEXT, payload BLOB, "
            "PRIMARY KEY(method,identity)) WITHOUT ROWID"
        )
        self.sums = _PairedSums()
        self.keys = hashlib.sha256()
        self.eligible_keys = hashlib.sha256()
        self.input_trace = hashlib.sha256()
        self.previous: bytes | None = None
        self.cells = 0
        self.failed = self.closed = self.sealed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        if not self.closed:
            self.db.close()
            self.closed = True

    def _available(self) -> None:
        if self.failed or self.closed or self.sealed:
            raise SnapshotError("campaign_uncertainty_stream_unavailable")

    def add(self, row: CampaignForecastEvaluationPrediction, actual: int | None) -> None:
        self._available()
        try:
            self._add(row, actual)
        except Exception:
            self.failed = True
            raise

    def _add(self, row: CampaignForecastEvaluationPrediction, actual: int | None) -> None:
        row = CampaignForecastEvaluationPrediction.model_validate_json(row.model_dump_json())
        key = membership_key(row)
        if (
            row.role != self.scope.role
            or row.frozen_configuration_sha256 != self.scope.frozen_configuration_sha256
        ):
            raise SnapshotError("campaign_uncertainty_row_scope_mismatch")
        if self.previous is not None and key <= self.previous:
            raise SnapshotError("campaign_uncertainty_duplicate_or_unsorted_key")
        if self.sums.rows >= self.policy.max_rows:
            raise SnapshotError("campaign_uncertainty_row_budget")
        if row.eligible and (type(actual) is not int or actual < 0):
            raise SnapshotError("campaign_uncertainty_eligible_actual_missing")
        if not row.eligible and actual is not None:
            raise SnapshotError("campaign_uncertainty_excluded_actual_must_be_unavailable")
        # Reject overflow before differencing: two infinite errors must not cancel.
        if actual is not None:
            for head in (row.candidate, row.reference):
                for value in (head.mean, head.median):
                    if value is not None:
                        error = value - actual
                        if not math.isfinite(error * error):
                            raise SnapshotError("campaign_uncertainty_nonfinite_point_error")
                if head.interval is not None and not math.isfinite(
                    _score(head.interval, actual, self.policy.nominal_interval_coverage)
                ):
                    raise SnapshotError("campaign_uncertainty_nonfinite_interval_score")
        day = row.forecast_origin.date()
        block = (day - self.policy.time_block_anchor).days // self.policy.time_block_days
        identities: tuple[tuple[Method, str], ...] = (
            ("time_block", str(block)),
            (
                "series_cluster",
                canonical_bytes([row.product_id, row.selling_location_id, row.channel]).decode(),
            ),
        )
        for method, identity in identities:
            found = self.db.execute(
                "SELECT payload FROM clusters WHERE method=? AND identity=?", (method, identity)
            ).fetchone()
            if found is None:
                if self.cells >= self.policy.max_cluster_cells:
                    raise SnapshotError("campaign_uncertainty_cluster_cell_budget")
                sums = _PairedSums()
                self.cells += 1
            else:
                sums = _PairedSums.decode(found[0])
            sums.add(row, actual, self.policy.nominal_interval_coverage)
            self.db.execute(
                "INSERT OR REPLACE INTO clusters VALUES(?,?,?)",
                (method, identity, canonical_bytes(sums.payload())),
            )
        self.sums.add(row, actual, self.policy.nominal_interval_coverage)
        self.keys.update(key + b"\n")
        if row.eligible:
            self.eligible_keys.update(key + b"\n")
        self.input_trace.update(
            canonical_bytes({"prediction": row.model_dump(mode="json"), "actual": actual}) + b"\n"
        )
        self.previous = key
        self._check_index()

    def _check_index(self) -> None:
        pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        size = self.db.execute("PRAGMA page_size").fetchone()[0]
        if pages * size > self.policy.max_index_bytes:
            raise SnapshotError("campaign_uncertainty_index_byte_budget")

    def report(
        self,
        *,
        expected_rows: int,
        expected_eligible_rows: int,
        expected_keys_sha256: str,
        expected_eligible_keys_sha256: str,
    ) -> CampaignForecastUncertaintyReport:
        self._available()
        try:
            result = self._report(
                expected_rows,
                expected_eligible_rows,
                expected_keys_sha256,
                expected_eligible_keys_sha256,
            )
            self.sealed = True
            return result
        except Exception:
            self.failed = True
            raise

    def _report(
        self,
        rows: int,
        eligible: int,
        keys: str,
        eligible_keys: str,
    ) -> CampaignForecastUncertaintyReport:
        if (
            type(rows) is not int
            or type(eligible) is not int
            or (
                self.sums.rows,
                self.sums.eligible,
                self.keys.hexdigest(),
                self.eligible_keys.hexdigest(),
            )
            != (rows, eligible, keys, eligible_keys)
        ):
            raise SnapshotError("campaign_uncertainty_complete_census_mismatch")
        self._check_index()
        self.db.commit()
        counts: dict[Method, tuple[int, int]] = {}
        for method in self.policy.methods:
            clusters = eligible_clusters = 0
            for (raw,) in self.db.execute("SELECT payload FROM clusters WHERE method=?", (method,)):
                clusters += 1
                eligible_clusters += int(_PairedSums.decode(raw).eligible > 0)
            counts[method] = clusters, eligible_clusters

        def enough(method: Method) -> bool:
            minimum = (
                self.policy.minimum_eligible_time_blocks
                if method == "time_block"
                else self.policy.minimum_eligible_series_clusters
            )
            return counts[method][1] >= minimum

        visits = sum(
            n * self.policy.resamples for method, (n, _) in counts.items() if enough(method)
        )
        within_budget = visits <= self.policy.max_resampled_cluster_visits
        reports = tuple(
            self._method(method, enough(method), within_budget) for method in self.policy.methods
        )
        return CampaignForecastUncertaintyReport(
            scope=self.scope,
            policy=self.policy,
            policy_sha256=self.policy.content_sha256(),
            rows=rows,
            eligible_rows=eligible,
            keys_sha256=keys,
            eligible_keys_sha256=eligible_keys,
            paired_input_trace_sha256=self.input_trace.hexdigest(),
            methods=(reports[0], reports[1]),
        )

    def _method(
        self, method: Method, enough: bool, within_budget: bool
    ) -> CampaignPairedMethodReport:
        clusters: list[_PairedSums] = []
        inventory = hashlib.sha256()
        combined = _PairedSums()
        for identity, raw in self.db.execute(
            "SELECT identity,payload FROM clusters WHERE method=? ORDER BY identity", (method,)
        ):
            sums = _PairedSums.decode(raw)
            clusters.append(sums)
            combined.merge(sums)
            inventory.update(
                canonical_bytes({"identity": identity, "sums": sums.payload()}) + b"\n"
            )
        if combined.payload() != self.sums.payload() and (
            any(getattr(combined, n) != getattr(self.sums, n) for n in COUNT_FIELDS)
            or any(combined.sums[n].value() != self.sums.sums[n].value() for n in SUM_FIELDS)
        ):
            raise SnapshotError("campaign_uncertainty_cluster_population_changed")
        seed = int.from_bytes(
            hashlib.sha256(
                canonical_bytes(
                    {
                        "scope": self.scope.model_dump(mode="json"),
                        "policy_sha256": self.policy.content_sha256(),
                        "method": method,
                    }
                )
            ).digest()[:16],
            "big",
        )
        trace = hashlib.sha256()
        samples: dict[Metric, list[float]] = {n: [] for n in METRICS}
        invalid: dict[Metric, int] = dict.fromkeys(METRICS, 0)
        executed = self.policy.resamples if enough and within_budget else 0
        if executed:
            import numpy as np

            rng = np.random.Generator(np.random.PCG64(seed))
            for _ in range(executed):
                indices = rng.integers(0, len(clusters), size=len(clusters))
                trace.update(canonical_bytes([int(i) for i in indices]) + b"\n")
                sums = _PairedSums()
                for index in indices:
                    sums.merge(clusters[int(index)])
                for name, (value, _) in sums.metrics().items():
                    if value is None:
                        invalid[name] += 1
                    else:
                        samples[name].append(value)
        metrics: dict[Metric, CampaignPairedConfidenceMetric] = {}
        for name, (point, point_reason) in self.sums.metrics().items():
            reasons = tuple(
                r
                for r in (
                    point_reason,
                    "insufficient_eligible_clusters" if not enough else None,
                    "resampling_visit_budget" if enough and not within_budget else None,
                    "undefined_resampled_metric" if invalid[name] else None,
                )
                if r is not None
            )
            lower = upper = None
            if not reasons:
                tail = (1.0 - self.policy.confidence_level) / 2.0
                lower, upper = (
                    float(x)
                    for x in np.quantile(samples[name], [tail, 1.0 - tail], method="linear")
                )
            metrics[name] = CampaignPairedConfidenceMetric(
                status="not_evaluable" if reasons else "evaluated",
                point_delta=point,
                lower=lower,
                upper=upper,
                valid_replicates=len(samples[name]),
                invalid_replicates=invalid[name],
                reasons=reasons,
            )
        return CampaignPairedMethodReport(
            method=method,
            clusters=len(clusters),
            eligible_clusters=sum(c.eligible > 0 for c in clusters),
            rows=self.sums.rows,
            eligible_rows=self.sums.eligible,
            actual_units=self.sums.actual_units,
            cluster_inventory_sha256=inventory.hexdigest(),
            derived_resampling_seed=seed,
            resampling_trace_sha256=trace.hexdigest(),
            resamples_executed=executed,
            status="evaluated"
            if all(m.status == "evaluated" for m in metrics.values())
            else "not_evaluable",
            metrics=metrics,
        )
