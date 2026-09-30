"""Conservative history-only sample bounds; a rejection precedes feature materialization."""

from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.contract import CalendarManifest
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_store import source_index
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin


def source_preflight(
    curated: Path, calendar: CalendarManifest, backtest_policy: BacktestPolicy
) -> dict[str, Any]:
    """Count every possible horizon, including closed/inactive/insufficient-history targets.

    This can only reject a necessary sample bound. It cannot approve eligibility,
    availability of target labels, model fits or quality. The full feature preflight
    remains mandatory whenever this conservative bound does not reject the source.
    """
    plan = plan_backtest(calendar.descriptor.origin_window, backtest_policy)
    policy = QualityPolicy()
    counts: Counter[tuple[str, str, str]] = Counter()
    with source_index(curated, calendar) as source:
        for origin in calendar.origins:
            roles = [
                (fold.name, role)
                for fold in plan.folds
                for role in ("validation", "development_holdout")
                if getattr(fold, role).start <= origin.origin_date <= getattr(fold, role).end
            ]
            if not roles:
                continue
            view = OriginFeatures(source.origin_tables(origin), origin)
            for series in sorted(view.assortment):
                history = view.history(series)
                known = [p.observed_units for p in history.points if p.observed_units is not None]
                bucket = volume_bin(float(mean(known)) if known else None, policy)
                for fold, role in roles:
                    for scope in (fold, "pooled"):
                        counts[scope, role, bucket] += len(origin.targets)
    gates = [
        {
            "fold": fold,
            "role": role,
            "volume": bucket,
            "potential_feature_rows_upper_bound": counts[fold, role, bucket],
            "minimum_rows": policy.minimum_segment_rows,
            "status": "not_rejected"
            if counts[fold, role, bucket] >= policy.minimum_segment_rows
            else "not_ready",
        }
        for fold in (*[f.name for f in plan.folds], "pooled")
        for role in ("validation", "development_holdout")
        for bucket in policy.required_volume_bins
    ]
    return {
        "status": "not_ready" if any(g["status"] == "not_ready" for g in gates) else "not_rejected",
        "scope": "origin-known history conservative sample upper bounds before target features",
        "calendar_id": calendar.calendar_id,
        "curated_dataset_id": calendar.descriptor.parent.curated_dataset_id,
        "backtest_policy": backtest_policy.model_dump(mode="json"),
        "gates": gates,
        "target_outcomes_evaluated": False,
        "target_labels_materialized": False,
        "source_integrity_index_may_contain_later_observations": True,
        "history_rows_selected_only_as_of_origin": True,
        "target_eligibility_assessed": False,
        "features_materialized": False,
        "full_feature_preflight_required_if_not_rejected": True,
        "model_fits": 0,
        "model_quality_qualified": False,
    }
