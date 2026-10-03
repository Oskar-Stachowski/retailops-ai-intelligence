"""Only a confirmed known closure admits a null prediction; unknown calendars fail."""

from copy import deepcopy

import pytest

from retailops_ai.forecast_jobs.v12_contracts import V12Prediction
from retailops_ai.forecast_jobs.v12_executor import calendar_exclusion


def row(eligible=True, value=True):
    return dict(
        target_calendar_eligible=eligible,
        values=[
            dict(name="target_location_open", kind="calendar", status="available", value=value)
        ],
    )


def test_confirmed_open_and_closed_have_distinct_meaning():
    assert calendar_exclusion(row()) is None
    assert calendar_exclusion(row(False, False)) == "closed_target"


@pytest.mark.parametrize(
    "eligible,value", [(True, False), (False, True), (False, None), (True, 1), (False, 0)]
)
def test_unknown_or_contradictory_calendar_never_becomes_closed(eligible, value):
    with pytest.raises(ValueError):
        calendar_exclusion(row(eligible, value))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "status", "kind"])
def test_calendar_evidence_is_required(mutation):
    raw = row(False, False)
    if mutation == "missing":
        raw["values"] = []
    elif mutation == "duplicate":
        raw["values"] *= 2
    else:
        raw["values"][0][mutation] = "missing"
    with pytest.raises(ValueError):
        calendar_exclusion(raw)


def test_excluded_forecast_is_null_not_zero():
    raw = dict(
        key="calendar-contract-double",
        candidate=dict(median=None, mean=None, interval=None),
        baseline=dict(median=None, mean=None, interval=None),
        exclusion_reason="closed_target",
        metadata=dict(
            selected=None,
            baseline=None,
            mean_source="exact_baseline",
            exact_reference_median=True,
            exact_reference_interval=True,
            recipe_id="functional-v12-recipe-sha256-" + "a" * 64,
        ),
    )
    assert V12Prediction.model_validate(raw).exclusion_reason == "closed_target"
    changed = deepcopy(raw)
    changed["candidate"]["mean"] = 0
    with pytest.raises(ValueError):
        V12Prediction.model_validate(changed)
