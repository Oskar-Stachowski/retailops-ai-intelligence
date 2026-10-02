"""A future plan cannot turn an incomplete observation into a complete source declaration."""

from copy import deepcopy

import pytest

from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.source_snapshot.inventory_projection import Facts, verify_forecast_watermarks


def descriptor():
    return {
        "context": {"evaluated_at": "2026-10-02T00:00:00+00:00"},
        "resolved_parameters": {"end_date": "2026-10-01", "forecast_plan_days": 14},
        "forecast_watermarks": {
            "daily_demand_observations": {
                "as_of_time": "2026-10-02T00:00:00+00:00",
                "complete_through": "2026-10-01",
                "completeness_status": "complete",
                "meaning": "synthetic_sales_day_close_without_return_guarantee",
                "policy_version": "daily-demand-1.0.0",
            }
        },
    }


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "late",
        "incomplete",
        "future_date",
        "future_watermark",
        "wrong_cutoff",
        "wrong_policy",
        "empty",
    ],
)
def test_source_completeness_is_recomputed_from_typed_observations(tmp_path, fault):
    facts = Facts(tmp_path / "facts.sqlite")
    try:
        parent = descriptor()
        row = {
            "id": "observation",
            "business_date": "2026-10-01",
            "available_at": "2026-10-02T00:00:00+00:00",
            "source_data_complete": True,
        }
        if fault == "late":
            row["available_at"] = "2026-10-03T00:00:00+00:00"
        elif fault == "incomplete":
            row["source_data_complete"] = False
        elif fault == "future_date":
            row["business_date"] = "2026-10-02"
        elif fault in ("future_watermark", "wrong_cutoff", "wrong_policy"):
            key, value = {
                "future_watermark": ("complete_through", "2026-10-15"),
                "wrong_cutoff": ("as_of_time", "2026-10-03T00:00:00+00:00"),
                "wrong_policy": ("policy_version", "unknown"),
            }[fault]
            parent["forecast_watermarks"]["daily_demand_observations"][key] = value
        if fault != "empty":
            facts.add("daily_demand_observations", ["id"], row)
        if fault is None:
            verify_forecast_watermarks(facts, parent)
        else:
            with pytest.raises(SnapshotError, match="observed_completeness_mismatch"):
                verify_forecast_watermarks(facts, parent)
            if fault in ("late", "incomplete", "future_date", "empty"):
                parent["forecast_watermarks"]["daily_demand_observations"].update(
                    complete_through=None, completeness_status="not_ready"
                )
                verify_forecast_watermarks(facts, parent)
        legacy = deepcopy(parent)
        legacy.pop("forecast_watermarks")
        verify_forecast_watermarks(facts, legacy)
    finally:
        facts.db.close()
