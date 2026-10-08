"""Bounded sufficient statistics for frozen functional references; no label list or refit."""

import math
from dataclasses import dataclass, field
from typing import Any

from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPrediction,
    CampaignForecastQualityPolicy,
    CampaignForecastReference,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_gates import assess_frozen_metrics
from retailops_ai.evaluation_campaign.campaign_score_metrics import Point
from retailops_ai.evaluation_campaign.campaign_tune_data import BandMetrics
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError

DIMENSIONS = (
    "global",
    "horizon",
    "category",
    "channel",
    "volume",
    "scenario",
    "history",
    "availability",
    "inventory",
    "lead_time",
    "intermittency",
    "anomaly",
)


@dataclass
class ForecastMetrics:
    mean: Point = field(default_factory=Point)
    median: Point = field(default_factory=Point)
    interval: BandMetrics = field(default_factory=BandMetrics)
    rows: int = 0
    actual_sum: int = 0

    def add(
        self, forecast: FunctionalForecast | CampaignForecastReference, actual: int, nominal: float
    ) -> None:
        for value in (forecast.mean, forecast.median):
            if value is not None and not math.isfinite((value - actual) * (value - actual)):
                raise SnapshotError("campaign_evaluation_nonfinite_point_error")
        self.rows += 1
        self.actual_sum += actual
        self.mean.add(forecast.mean, actual)
        self.median.add(forecast.median, actual)
        self.interval.add(actual, forecast.interval, nominal)

    def result(self) -> dict[str, Any]:
        mean, median = self.mean.result(self.rows), self.median.result(self.rows)
        for point in (mean, median):
            point.pop("predicted_rows")
        band = self.interval.result(self.rows)
        band.pop("predicted_rows")
        width = band["mean_width"]
        band["width_to_mean_actual"] = (
            width / (self.actual_sum / self.rows) if width is not None and self.actual_sum else None
        )
        return {
            "rows": self.rows,
            "actual_sum": self.actual_sum,
            "mean": mean,
            "median": median,
            "interval": band,
        }


class EvaluationSegment:
    """One sorted-key segment; a complete campaign separately proves its full inventory."""

    def __init__(
        self,
        dimension: str,
        *,
        retained_median_baseline: bool,
        policy: CampaignForecastQualityPolicy | None = None,
        max_rows: int = 100000000,
    ) -> None:
        if (
            dimension not in DIMENSIONS
            or type(max_rows) is not int
            or not 1 <= max_rows <= 100000000
        ):
            raise ValueError("campaign_evaluation_segment_dimension_or_budget")
        self.policy = CampaignForecastQualityPolicy.model_validate_json(
            (policy or CampaignForecastQualityPolicy()).model_dump_json()
        )
        self.dimension = dimension
        self.retained_median_baseline = retained_median_baseline
        self.max_rows = max_rows
        self.rows = self.eligible = 0
        self.failed = False
        self.previous: bytes | None = None
        self.scope: tuple[str, str] | None = None
        self.candidate = ForecastMetrics()
        self.reference = ForecastMetrics()

    def add(self, row: CampaignForecastEvaluationPrediction, actual: int | None) -> None:
        if self.failed:
            raise SnapshotError("campaign_evaluation_metric_stream_already_failed")
        try:
            self._add(row, actual)
        except Exception:
            self.failed = True
            raise

    def _add(self, row: CampaignForecastEvaluationPrediction, actual: int | None) -> None:
        key = membership_key(row)
        scope = row.role, row.frozen_configuration_sha256
        if self.previous is not None and key <= self.previous:
            raise SnapshotError("campaign_evaluation_duplicate_or_unsorted_metric_key")
        if self.scope is not None and self.scope != scope:
            raise SnapshotError("campaign_evaluation_metric_scope_changed")
        if self.rows >= self.max_rows:
            raise SnapshotError("campaign_evaluation_metric_row_budget")
        if row.eligible and (type(actual) is not int or actual < 0):
            raise SnapshotError("campaign_evaluation_eligible_actual_missing")
        if not row.eligible and actual is not None:
            raise SnapshotError("campaign_evaluation_excluded_actual_must_be_unavailable")
        if (
            row.eligible
            and self.retained_median_baseline
            and row.candidate.median != row.reference.median
        ):
            raise SnapshotError("retained_baseline_predictions_differ")
        self.rows += 1
        self.previous, self.scope = key, scope
        if row.eligible and actual is not None:
            self.eligible += 1
            self.candidate.add(row.candidate, actual, self.policy.nominal_coverage)
            self.reference.add(row.reference, actual, self.policy.nominal_coverage)

    def result(self) -> dict[str, Any]:
        if self.failed:
            raise SnapshotError("campaign_evaluation_failed_metric_stream_has_no_result")
        result = assess_frozen_metrics(
            self.candidate.result(),
            self.reference.result(),
            total_rows=self.rows,
            eligible_rows=self.eligible,
            dimension=self.dimension,
            retained_median_baseline=self.retained_median_baseline,
            policy=self.policy,
        )
        return result | {
            "scope": "frozen_campaign_segment_component_not_campaign_qualification",
            "role": self.scope[0] if self.scope is not None else None,
            "frozen_configuration_sha256": self.scope[1] if self.scope is not None else None,
            "reference_functionals": self.policy.reference_functionals,
        }
