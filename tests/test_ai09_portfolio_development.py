"""Scope, temporal separation and failure accounting for the bounded real runner."""

import argparse
import importlib
import json
from datetime import date, timedelta

import pytest

from retailops_ai.forecasting.manifest_contract import FoldPlan
from scripts import run_ai09_portfolio_development as runner


def test_development_profile_preserves_history_maturity_and_equal_model_keys():
    plan = runner.frozen_plan()
    generation = plan["generation"]
    fold = FoldPlan.model_validate_json(json.dumps(plan["comparison"]["fold"]))
    assert (
        (
            date.fromisoformat(generation["end_date"])
            - date.fromisoformat(generation["start_date"])
        ).days
        + 1
        == generation["days"]
        == 365
    )
    assert (
        generation["days"] * generation["products"] * generation["stores"]
        == generation["max_daily_rows"]
    )
    for role in ("train", "validation", "development_holdout"):
        window = getattr(fold, role)
        assert fold.label_cutoff(role).date() >= window.end + timedelta(days=15)
    policy = runner.comparison_policy(plan)
    assert policy.tensorflow.initialization_seed == policy.trees.model.random_state == 42
    assert policy.selection == "none_one_frozen_configuration_per_family"
    assert not policy.final_test_accessed and not policy.promotion_allowed
    assert (fold.train.end - fold.train.start).days + 1 == 90
    assert 90 * generation["products"] * generation["stores"] <= policy.tensorflow.max_windows


@pytest.mark.parametrize(
    "field", ["final_generation_authorized", "final_test_opened", "stage_ready"]
)
def test_development_entrypoint_cannot_be_relabelled_as_final_or_ready(
    tmp_path, monkeypatch, field
):
    plan = runner.frozen_plan()
    plan[field] = True
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(runner, "PLAN", path)
    with pytest.raises(ValueError, match="scope_changed"):
        runner.frozen_plan()


def test_changed_plan_is_rejected_before_native_work(tmp_path):
    plan = runner.frozen_plan()
    plan["generation"]["seed"] = 137
    (tmp_path / "preparation-plan.json").write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="frozen_plan_changed"):
        runner.require_frozen_output(tmp_path)


def test_failed_forecast_keeps_measurements_and_consumed_intent(tmp_path, monkeypatch):
    probe = importlib.import_module("measure_ai09_development_capacity")
    controller = importlib.import_module("run_ai09_preparation")
    monkeypatch.setattr(probe, "require_remote", lambda: None)
    monkeypatch.setattr(controller, "initialize", lambda *a, **k: None)
    monkeypatch.setattr(controller, "phase", lambda *a, **k: None)
    monkeypatch.setattr(
        controller,
        "finalize",
        lambda *a: {
            "status": "complete",
            "completed_phases": 5,
            "charged_wall_seconds": 120,
            "events_sha256": "a" * 64,
        },
    )
    measured = {"status": "failed", "reason": "wall_limit", "wall_seconds": 5280}
    monkeypatch.setattr(probe, "monitor", lambda *a, **k: measured)
    args = argparse.Namespace(
        source=tmp_path / "source", output=tmp_path, producer_python=tmp_path / "python"
    )
    with pytest.raises(ValueError, match="forecast_failed"):
        runner.run(args)
    result = json.loads((tmp_path / "portfolio-development-result.json").read_bytes())
    assert result["status"] == "failed"
    assert result["forecast_resources"] == measured
    assert not result["stage_ready"] and not result["final_test_opened"]
    intent = json.loads((tmp_path / "forecast-intent.json").read_bytes())
    assert intent["maximum_attempts"] == 1
    # Even if an external caller bypassed the preparation guard, the fit intent
    # cannot be overwritten to silently obtain a fresh attempt.
    with pytest.raises((ValueError, FileExistsError)):
        runner.run(args)
