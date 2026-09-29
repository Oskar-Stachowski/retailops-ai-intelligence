import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_repository", ROOT / "scripts/check_repository.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def workflow():
    return yaml.safe_load((ROOT / ".github/workflows/required-ci.yml").read_text())


def test_workflow_is_covered_by_required_result():
    assert module.workflow_errors(workflow()) == []


@pytest.mark.parametrize(
    "mutation", ["tag", "ignored_failure", "missing_gate", "path_filter", "missing_persistence"]
)
def test_ci_guard_rejects_weakened_gates(mutation):
    data = deepcopy(workflow())
    if mutation == "tag":
        data["jobs"]["checks"]["steps"][0]["uses"] = "actions/checkout@main"
    elif mutation == "ignored_failure":
        data["jobs"]["checks"]["continue-on-error"] = True
    elif mutation == "missing_gate":
        data["jobs"]["required-result"]["needs"] = ["checks"]
    elif mutation == "missing_persistence":
        data["jobs"].pop("persistence")
    else:
        data[True]["pull_request"] = {"paths": ["src/**"]}
    assert module.workflow_errors(data)


@pytest.mark.parametrize("mutation", ["checks-bypass", "persistence-expression"])
def test_ci_rejects_bypassed_checks_or_malformed_result_expression(mutation):
    data = workflow()
    if mutation == "checks-bypass":
        data["jobs"]["checks"]["steps"][-1]["run"] = "make bootstrap test"
    else:
        data["jobs"]["required-result"]["steps"][0]["env"]["PERSISTENCE_RESULT"] = (
            "invalid-expression"
        )
    assert module.workflow_errors(data)


def test_make_check_includes_snapshot_gate():
    makefile = (ROOT / "Makefile").read_text()
    dependencies = makefile.split("check: ", 1)[1].splitlines()[0]
    assert "contracts-check" in dependencies.split()
    assert "scripts/update_intelligence_contracts.py --check" in makefile
    assert "scripts/update_access_contracts.py --check" in makefile
    assert "scripts/update_knowledge_contracts.py --check" in makefile
    assert "scripts/update_forecast_contracts.py --check" in makefile
    assert "forecast-calendar-check" in dependencies.split()
    assert "forecast-features-check" in dependencies.split()
    assert "forecast-manifests-check" in dependencies.split()
    assert "forecast-baselines-check" in dependencies.split()
    assert "forecast-models-check" in dependencies.split()
    assert "forecast-backtest-check" in dependencies.split()
    assert "forecast-quality-check" in dependencies.split()


@pytest.mark.parametrize(
    "event,settings",
    [
        ("push", {"branches": ["ai/**"]}),
        ("push", {"branches": ["main"]}),
        ("push", {"branches-ignore": ["main"]}),
        ("pull_request", {"types": ["opened"]}),
        ("pull_request", {"branches": ["develop"]}),
        ("pull_request", {"branches-ignore": ["main"]}),
    ],
)
def test_ci_rejects_missing_automatic_triggers(event, settings):
    data = workflow()
    data[True][event] = settings
    assert module.workflow_errors(data)
