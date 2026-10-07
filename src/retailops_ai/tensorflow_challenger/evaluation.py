"""Use the existing v2 evaluator on the identical development population."""

from typing import Any

from retailops_ai.evaluation_campaign.comparison import (
    forecast_key_bytes,
    require_same_forecast_keys,
)
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import ChallengerPrediction
from retailops_ai.tensorflow_challenger.dataset import Window


def compare_development(
    windows: tuple[Window, ...], predictions: tuple[ChallengerPrediction, ...]
) -> dict[str, Any]:
    rows = [s.row for w in windows for s in w.samples]
    count, keys_sha256 = require_same_forecast_keys(rows, predictions)
    by_key = {forecast_key_bytes(p): p for p in predictions}
    observations: list[ProtocolObservation] = []
    horizons: dict[int, list[ProtocolObservation]] = {}
    for window in windows:
        for sample in window.samples:
            if sample.membership.role != "validation":
                raise SnapshotError("tensorflow_comparison_requires_development_validation")
            prediction = by_key[forecast_key_bytes(sample.row)]
            points, bands = empirical_baselines(sample.row, window.history)
            band = bands["history28"]
            eligible = sample.membership.eligible
            observation = ProtocolObservation(
                key=forecast_key_bytes(sample.row).decode(),
                actual=sample.label.observed_sales_units,
                exclusion_reasons=sample.membership.reasons,
                candidate=prediction.value,
                baseline=FunctionalForecast(
                    median=points["history28:median"] if eligible else None,
                    mean=points["history28:mean"] if eligible else None,
                    interval=CentralInterval(lower=band[0], upper=band[1])
                    if eligible and band is not None
                    else None,
                ),
            )
            observations.append(observation)
            horizons.setdefault(sample.row.horizon_days, []).append(observation)
    return {
        "scope": "development_diagnostic_on_early_stopping_validation_not_independent_quality_acceptance",
        "reference": "fixed_history28_not_a_new_baseline_selection",
        "forecast_keys_sha256": keys_sha256,
        "prediction_rows": count,
        "global": assess_segment_v2(observations, dimension="global"),
        "horizons": {
            str(h): assess_segment_v2(obs, dimension="horizon")
            for h, obs in sorted(horizons.items())
        },
        "promotion_allowed": False,
        "final_test_accessed": False,
    }
