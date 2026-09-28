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
    else:
        data[True]["pull_request"] = {"paths": ["src/**"]}
    assert module.workflow_errors(data)
