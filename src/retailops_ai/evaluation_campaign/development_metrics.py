"""Identical complete forecast populations and explicit functionals in every segment."""

from collections import defaultdict
from typing import Any, cast

from retailops_ai.evaluation_campaign.comparison import (
    forecast_key_bytes,
    require_same_forecast_keys,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS, DevelopmentPrediction
from retailops_ai.forecasting.functional_recipe import empirical_baselines
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.forecasting.quality_v2 import assess_segment_v2
from retailops_ai.forecasting.quality_v2_contract import (
    CentralInterval,
    FunctionalForecast,
    ProtocolObservation,
)
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.dataset import Window


def baseline_predictions(
    validation: tuple[Window, ...],
) -> dict[str, tuple[DevelopmentPrediction, ...]]:
    output: dict[str, list[DevelopmentPrediction]] = {name: [] for name in MODELS[:3]}
    for window in validation:
        for sample in window.samples:
            points, bands = empirical_baselines(sample.row, window.history)
            for name, rows in output.items():
                band = bands[name]
                eligible = sample.membership.eligible
                rows.append(
                    DevelopmentPrediction(
                        **sample.row.model_dump(include=set(DevelopmentPrediction.model_fields)),
                        value=FunctionalForecast(
                            mean=points[name + ":mean"] if eligible else None,
                            median=points[name + ":median"] if eligible else None,
                            interval=CentralInterval(lower=band[0], upper=band[1])
                            if eligible and band is not None
                            else None,
                        ),
                    )
                )
    return {name: tuple(rows) for name, rows in output.items()}


def comparison_metrics(
    validation: tuple[Window, ...],
    predictions: dict[str, tuple[DevelopmentPrediction, ...]],
    train: tuple[Window, ...] = (),
) -> dict[str, Any]:
    if set(predictions) != set(MODELS):
        raise SnapshotError("development_comparison_model_inventory")
    samples = [s for w in validation for s in w.samples]
    if any(s.membership.role != "validation" for s in samples):
        raise SnapshotError("development_comparison_requires_validation")
    keys = [s.row for s in samples]
    indexes = {}
    for name, values in predictions.items():
        count, population = require_same_forecast_keys(keys, values)
        indexes[name] = {forecast_key_bytes(p): p.value for p in values}
        for sample in samples:
            value = indexes[name][forecast_key_bytes(sample.row)]
            if not sample.membership.eligible and any(
                getattr(value, component) is not None
                for component in ("mean", "median", "interval")
            ):
                raise SnapshotError("development_comparison_excluded_prediction")
            if name == "rf_mean" and (value.median is not None or value.interval is not None):
                raise SnapshotError("development_comparison_rf_mean_not_median")
    dimensions: dict[str, set[str]] = {
        "horizon": {str(h) for h in range(1, 15)},
        "volume": set(QualityPolicy().required_volume_bins),
        "category": set(),
        "channel": set(),
    }

    def groups(sample: Any) -> dict[str, str]:
        values = {v.name: v.value for v in sample.row.values}
        return {
            "horizon": str(sample.row.horizon_days),
            "category": str(values["category_id"]),
            "channel": sample.row.channel,
            "volume": volume_bin(cast(float | None, values["rolling_mean_28"]), QualityPolicy()),
        }

    for window in (*train, *validation):
        for sample in window.samples:
            for dimension, group_value in groups(sample).items():
                dimensions[dimension].add(group_value)
    output = {}
    for name in MODELS:
        global_rows = []
        segments: dict[str, dict[str, list[ProtocolObservation]]] = {
            d: defaultdict(list) for d in dimensions
        }
        for sample in samples:
            key = forecast_key_bytes(sample.row)
            observation = ProtocolObservation(
                key=key.decode(),
                actual=sample.label.observed_sales_units,
                exclusion_reasons=sample.membership.reasons,
                candidate=indexes[name][key],
                baseline=indexes["history28"][key],
            )
            global_rows.append(observation)
            for dimension, group_value in groups(sample).items():
                segments[dimension][group_value].append(observation)
        output[name] = {
            "global": assess_segment_v2(
                global_rows, dimension="global", retained_median_baseline=name == "history28"
            ),
            "segments": {
                d: {
                    value: assess_segment_v2(
                        segments[d][value],
                        dimension=cast(Any, d),
                        retained_median_baseline=name == "history28",
                    )
                    for value in sorted(values)
                }
                for d, values in dimensions.items()
            },
        }
    return {
        "scope": "early_stopping_validation_diagnostic_not_independent_acceptance",
        "reference": "fixed_history28_declared_before_fitting",
        "forecast_population_sha256": population,
        "prediction_rows_per_model": count,
        "models": output,
        "functional_limits": {
            "rf_mean": "conditional_mean_only_median_and_interval_unsupported",
            "hgb": "raw_mean_and_median_no_calibrated_interval",
            "tensorflow": "raw_mean_and_median_no_calibrated_interval",
            "baselines": "as_of_empirical_statistics_and_raw_uncalibrated_intervals",
        },
        "deployment_status": "not_ready",
        "final_test_accessed": False,
        "promotion_allowed": False,
    }
