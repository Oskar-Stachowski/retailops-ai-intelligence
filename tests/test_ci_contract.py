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


def test_development_push_is_not_a_second_full_pr_run():
    events = workflow()[True]
    assert events["push"] == {"branches": ["main"]}
    assert "pull_request" in events and "workflow_dispatch" in events


@pytest.mark.parametrize(
    "name",
    ["tests", "acceptance", "persistence", "persistence-forecast", "anomaly-oci", "tensorflow"],
)
def test_expensive_jobs_cannot_bypass_preflight(name):
    data = workflow()
    data["jobs"][name].pop("needs")
    assert module.workflow_errors(data)


def test_v12_and_full_collection_proofs_remain_mandatory():
    data = workflow()
    data["jobs"]["persistence"]["steps"] = [
        step
        for step in data["jobs"]["persistence"]["steps"]
        if step.get("run") != "make v12-backup-smoke"
    ]
    assert module.workflow_errors(data)
    data = workflow()
    data["jobs"]["required-result"]["steps"].pop()
    assert module.workflow_errors(data)


@pytest.mark.parametrize("mutation", ["missing-job", "bypassed-training", "skipped-job"])
def test_required_ci_preserves_real_tensorflow_acceptance(mutation):
    data = workflow()
    if mutation == "missing-job":
        data["jobs"].pop("tensorflow")
    elif mutation == "bypassed-training":
        data["jobs"]["tensorflow"]["steps"][-1]["run"] = "make bootstrap test"
    else:
        data["jobs"]["tensorflow"]["if"] = "false"
    assert (
        "tensorflow must execute locked CPU training and artifact reload acceptance"
        in module.workflow_errors(data)
    )


def test_required_ci_cannot_drop_mlflow_restore_smoke():
    data = workflow()
    data["jobs"]["persistence"]["steps"] = [
        step
        for step in data["jobs"]["persistence"]["steps"]
        if step.get("run") != "make mlflow-store-smoke"
    ]
    assert "persistence must execute MLflow backup/restore acceptance" in module.workflow_errors(
        data
    )


def test_required_ci_cannot_drop_model_lifecycle_recovery():
    data = workflow()
    data["jobs"]["persistence"]["steps"] = [
        step
        for step in data["jobs"]["persistence"]["steps"]
        if step.get("run") != "make model-lifecycle-smoke"
    ]
    assert "persistence must execute model lifecycle recovery acceptance" in module.workflow_errors(
        data
    )


def test_required_ci_cannot_drop_combined_lifecycle_restore():
    data = workflow()
    data["jobs"]["persistence"]["steps"] = [
        step
        for step in data["jobs"]["persistence"]["steps"]
        if step.get("run") != "make lifecycle-store-smoke"
    ]
    assert (
        "persistence must execute combined lifecycle backup/restore acceptance"
        in module.workflow_errors(data)
    )


def test_required_ci_cannot_drop_forecast_input_store_acceptance():
    data = workflow()
    data["jobs"]["persistence-forecast"]["steps"] = [
        step
        for step in data["jobs"]["persistence-forecast"]["steps"]
        if step.get("run") != "make forecast-input-store-smoke"
    ]
    assert "persistence must execute forecast input store acceptance" in module.workflow_errors(
        data
    )


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
    assert "forecast-remediation-check" in dependencies.split()
    assert "forecast-run-check" in dependencies.split()
    assert "forecast-runtime-check" in dependencies.split()
    assert "scripts/check_forecast_runtime.py" in makefile


@pytest.mark.parametrize(
    "event,settings",
    [
        ("push", {"branches": ["ai/**"]}),
        ("push", {"branches": ["main", "ai/**"]}),
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


def test_required_ci_cannot_drop_forecast_queue():
    data = workflow()
    data["jobs"]["persistence"]["steps"] = [
        step
        for step in data["jobs"]["persistence"]["steps"]
        if step.get("run") != "make forecast-queue-smoke"
    ]
    assert "persistence must execute forecast queue acceptance" in module.workflow_errors(data)
    assert "scripts/update_forecast_job_contracts.py --check" in (ROOT / "Makefile").read_text()


def test_required_ci_cannot_drop_forecast_publication_acceptance():
    data = workflow()
    data["jobs"]["persistence-forecast"]["steps"] = [
        step
        for step in data["jobs"]["persistence-forecast"]["steps"]
        if step.get("run") != "make forecast-publication-smoke"
    ]
    assert "persistence must execute forecast publication acceptance" in module.workflow_errors(
        data
    )


def test_required_ci_cannot_drop_forecast_read_acceptance():
    data = workflow()
    data["jobs"]["persistence-forecast"]["steps"] = [
        step
        for step in data["jobs"]["persistence-forecast"]["steps"]
        if step.get("run") != "make forecast-read-smoke"
    ]
    assert "persistence must execute forecast read acceptance" in module.workflow_errors(data)


def test_required_ci_cannot_drop_model_catalog_acceptance():
    data = workflow()
    data["jobs"]["persistence-forecast"]["steps"] = [
        step
        for step in data["jobs"]["persistence-forecast"]["steps"]
        if step.get("run") != "make model-catalog-smoke"
    ]
    assert "persistence must execute model catalog acceptance" in module.workflow_errors(data)


def test_required_ci_cannot_drop_evaluation_acceptance():
    data = workflow()
    data["jobs"]["persistence-forecast"]["steps"] = [
        step
        for step in data["jobs"]["persistence-forecast"]["steps"]
        if step.get("run") != "make evaluations-smoke"
    ]
    assert "persistence must execute evaluation acceptance" in module.workflow_errors(data)


@pytest.mark.parametrize(
    "mutation",
    ["missing-shard", "excluded-shard", "skipped-tests", "missing-group", "ignored-test-result"],
)
def test_parallel_ci_cannot_omit_tests_or_acceptance(mutation):
    data = workflow()
    if mutation == "missing-shard":
        data["jobs"]["tests"]["strategy"]["matrix"]["shard"].pop()
    elif mutation == "excluded-shard":
        data["jobs"]["tests"]["strategy"]["matrix"]["exclude"] = [{"shard": 3}]
    elif mutation == "skipped-tests":
        data["jobs"]["tests"]["if"] = "false"
    elif mutation == "missing-group":
        data["jobs"]["acceptance"]["strategy"]["matrix"]["target"].pop()
    else:
        step = data["jobs"]["required-result"]["steps"][0]
        step["run"] = step["run"].replace('test "$TESTS_RESULT" = "success"', "")
    assert module.workflow_errors(data)


def test_parallel_make_groups_cannot_drop_or_duplicate_a_gate():
    makefile = (ROOT / "Makefile").read_text()
    assert module.make_ci_errors(makefile) == []
    assert module.make_ci_errors(
        makefile.replace("ci-detectors: anomaly-detectors-check", "ci-detectors: docs-check")
    )
    assert module.make_ci_errors(makefile.replace("check: lint", "check: new-required-gate lint"))
