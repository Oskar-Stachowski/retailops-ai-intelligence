"""Raw mean/median diagnostics with stable sums and explicit undefined denominators."""

from dataclasses import dataclass, field
from typing import Any

from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastRawPrediction
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.forecasting.functional_v12_quality import StableSum
from retailops_ai.source_snapshot.files import SnapshotError


@dataclass
class Point:
    predicted: int = 0
    actual: int = 0
    zero_excess: StableSum = field(default_factory=StableSum)
    absolute: StableSum = field(default_factory=StableSum)
    squared: StableSum = field(default_factory=StableSum)
    signed: StableSum = field(default_factory=StableSum)

    def add(self, value: float | None, actual: int) -> None:
        if value is None:
            return
        self.predicted += 1
        self.actual += actual
        error = value - actual
        self.absolute.add(abs(error))
        self.squared.add(error * error)
        self.signed.add(error)
        if actual == 0:
            self.zero_excess.add(value)

    def result(self, eligible: int) -> dict[str, Any]:
        complete = eligible > 0 and self.predicted == eligible
        return {
            "predicted_rows": self.predicted,
            "complete": complete,
            "mae": self.absolute.value() / eligible if complete else None,
            "mse": self.squared.value() / eligible if complete else None,
            "bias_units": self.signed.value() / eligible if complete else None,
            "wape": self.absolute.value() / self.actual if complete and self.actual else None,
            "normalized_bias": self.signed.value() / self.actual
            if complete and self.actual
            else None,
            "zero_actual_excess_units": self.zero_excess.value() if complete else None,
        }


class RawMetrics:
    def __init__(self) -> None:
        self.rows = {str(h): [0, 0] for h in (0, *range(1, 15))}
        self.points = {
            (str(h), model, head): Point()
            for h in (0, *range(1, 15))
            for model in MODELS
            for head in ("mean", "median")
        }

    def add(self, row: CampaignForecastRawPrediction, actual: int | None) -> None:
        if row.eligible and (type(actual) is not int or actual < 0):
            raise SnapshotError("campaign_score_eligible_actual_missing")
        for horizon in ("0", str(row.horizon_days)):
            self.rows[horizon][0] += 1
            if row.eligible and actual is not None:
                self.rows[horizon][1] += 1
                for model, forecast in zip(MODELS, row.values, strict=True):
                    for head in ("mean", "median"):
                        self.points[horizon, model, head].add(getattr(forecast, head), actual)

    def result(self) -> dict[str, Any]:
        return {
            "scope": "raw_development_diagnostic_not_qualification",
            "calibration_fitted": False,
            "quality_qualified": False,
            "final_test_accessed": False,
            "stage_ready": False,
            "segments": [
                {
                    "horizon": "all" if horizon == "0" else horizon,
                    "rows": counts[0],
                    "eligible_rows": counts[1],
                    "models": {
                        model: {
                            head: self.points[horizon, model, head].result(counts[1])
                            for head in ("mean", "median")
                        }
                        for model in MODELS
                    },
                }
                for horizon, counts in self.rows.items()
            ],
        }
