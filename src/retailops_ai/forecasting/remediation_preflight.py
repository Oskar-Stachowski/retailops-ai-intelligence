"""Reject structurally missing critical samples before fitting or reading target outcomes."""

from collections import Counter
from pathlib import Path
from typing import Any

from retailops_ai.forecasting.backtest_contract import BacktestPolicy, plan_backtest
from retailops_ai.forecasting.calendar import load_calendar
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifests import input_models, verify_feature_set
from retailops_ai.forecasting.quality_contract import QualityPolicy
from retailops_ai.forecasting.quality_metrics import volume_bin
from retailops_ai.source_snapshot.files import SnapshotError


def preflight(features: Path, backtest_policy: BacktestPolicy) -> dict[str, Any]:
    manifest = verify_feature_set(features)
    calendar = load_calendar(features / "inputs/calendar_manifest.json")
    plan = plan_backtest(calendar.descriptor.origin_window, backtest_policy)
    policy = QualityPolicy()
    counts: Counter[tuple[str, str, str]] = Counter()
    excluded: Counter[tuple[str, str]] = Counter()
    for row in input_models(features, "features"):
        if not isinstance(row, InputRow):
            raise SnapshotError("remediation_preflight_input_schema")
        values = {value.name: value.value for value in row.values}
        historical = values["rolling_mean_28"]
        if historical is not None and type(historical) is not float:
            raise SnapshotError("remediation_preflight_volume_schema")
        bucket = volume_bin(historical, policy)
        for fold in plan.folds:
            for role in ("validation", "development_holdout"):
                window = getattr(fold, role)
                if not window.start <= row.forecast_origin.date() <= window.end:
                    continue
                for scope in (fold.name, "pooled"):
                    if row.insufficient_history or not row.target_calendar_eligible:
                        excluded[scope, role] += 1
                    else:
                        counts[scope, role, bucket] += 1
    gates = []
    for scope in (*[f.name for f in plan.folds], "pooled"):
        for role in ("validation", "development_holdout"):
            for bucket in policy.required_volume_bins:
                n = counts[scope, role, bucket]
                gates.append(
                    {
                        "fold": scope,
                        "role": role,
                        "volume": bucket,
                        "feature_eligible_rows": n,
                        "minimum_rows": policy.minimum_segment_rows,
                        "status": "passed" if n >= policy.minimum_segment_rows else "not_ready",
                    }
                )
    return {
        "status": "passed" if all(g["status"] == "passed" for g in gates) else "not_ready",
        "scope": "origin-known feature sample upper bounds before labels or training",
        "feature_set_id": manifest.feature_set_id,
        "backtest_policy": backtest_policy.model_dump(mode="json"),
        "gates": gates,
        "excluded_feature_rows": {":".join(k): v for k, v in sorted(excluded.items())},
        "target_outcomes_read": False,
        "model_fits": 0,
        "model_quality_qualified": False,
    }
