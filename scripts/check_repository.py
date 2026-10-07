"""Check documentation links and the minimum required-CI contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_GROUPS = {
    "ci-checks": "lint type-check docs-check forecast-runtime-check contracts-check agent-evaluate package compose-config",
    "ci-source-inputs": "handoff-check snapshot-import-check curated-check anomaly-inputs-check raw-dq-check return-inputs-check observation-replay-check",
    "ci-qualified-inputs": "qualified-anomaly-inputs-check",
    "ci-detectors": "anomaly-detectors-check day-qualification-check",
    "ci-forecast": "forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-remediation-check forecast-run-check forecast-acceptance-check full-raw-dq-check",
}


def make_ci_errors(makefile: str) -> list[str]:
    errors = []
    rules = {
        name: targets.split()
        for name, targets in re.findall(r"^([a-z][a-z-]+): ([^\n]+)$", makefile, re.MULTILINE)
    }
    # TensorFlow uses its own locked environment and isolated required job.
    targets = ["test", "tensorflow-check"]
    for name, expected in CI_GROUPS.items():
        if rules.get(name) != expected.split():
            errors.append(f"{name} must preserve every original acceptance gate")
        targets.extend(expected.split())
    if set(targets) != set(rules.get("check", [])) or len(targets) != len(set(targets)):
        errors.append("CI groups must cover every make check target exactly once")
    return errors


def workflow_errors(workflow: dict[str | bool, Any], makefile: str | None = None) -> list[str]:
    errors = []
    events: dict[str, Any] = workflow.get("on") or workflow.get(True) or {}
    if not all(event in events for event in ("push", "pull_request", "workflow_dispatch")):
        errors.append("CI must cover push, pull_request and workflow_dispatch")
    push = events.get("push") or {}
    pull_request = events.get("pull_request") or {}
    if push != {"branches": ["main"]}:
        errors.append("CI push must cover only main; PRs and dispatch cover development branches")
    if "types" in pull_request and not {"opened", "synchronize", "reopened"}.issubset(
        pull_request["types"]
    ):
        errors.append("CI must cover new, updated and reopened pull requests")
    if "branches-ignore" in push or any(
        key in pull_request for key in ("branches", "branches-ignore")
    ):
        errors.append("required CI must not exclude pull requests or push branches")
    if any(
        "paths" in (events.get(event) or {}) or "paths-ignore" in (events.get(event) or {})
        for event in ("push", "pull_request")
    ):
        errors.append("foundation CI must not skip new paths")
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("workflow permissions must be contents:read")
    jobs = workflow.get("jobs", {})
    tensorflow = jobs.get("tensorflow", {})
    if tensorflow.get("if") or not any(
        step.get("run") == "make bootstrap tensorflow-check" for step in tensorflow.get("steps", [])
    ):
        errors.append("tensorflow must execute locked CPU training and artifact reload acceptance")
    if not any(
        step.get("run")
        == "uv run --frozen python scripts/check_anomaly_oci.py --producer .local/ai07-ci-source"
        for step in jobs.get("anomaly-oci", {}).get("steps", [])
    ):
        errors.append("anomaly-oci must execute qualified native OCI and Pg16 acceptance")
    persistence_gates = {
        "persistence": {
            "make bootstrap compose-smoke": "real Compose",
            "make mlflow-store-smoke": "MLflow backup/restore",
            "make model-lifecycle-smoke": "model lifecycle recovery",
            "make lifecycle-store-smoke": "combined lifecycle backup/restore",
            "make forecast-queue-smoke": "forecast queue",
            "make v12-backup-smoke": "v12 coherent backup/restore and recovery",
        },
        "persistence-forecast": {
            "make forecast-input-store-smoke": "forecast input store",
            "make forecast-publication-smoke": "forecast publication",
            "make forecast-read-smoke": "forecast read",
            "make model-catalog-smoke": "model catalog",
            "make evaluations-smoke": "evaluation",
        },
    }
    outbox_steps = jobs.get("persistence-forecast", {}).get("steps", [])
    required_outbox_commands = (
        "make intelligence-delivery-bootstrap",
        "uv run --locked --project tools/intelligence-delivery python scripts/intelligence_outbox.py --help",
        "make integration-replay-test integration-failure-test",
    )
    if not any(
        not step.get("if")
        and all(command in step.get("run", "").splitlines() for command in required_outbox_commands)
        for step in outbox_steps
    ):
        errors.append(
            "persistence-forecast must execute actual AI10 transactional outbox acceptance"
        )
    for name, gates in persistence_gates.items():
        for command, description in gates.items():
            matching = [s for s in jobs.get(name, {}).get("steps", []) if s.get("run") == command]
            if len(matching) != 1 or matching[0].get("if"):
                errors.append(f"persistence must execute {description} acceptance")
    for name, job in jobs.items():
        if name in {"checks", "secrets", "required-result"}:
            continue
        if job.get("needs") != ["checks", "secrets"] or job.get("if"):
            errors.append(f"{name} must require passing checks and secrets before expensive work")
    errors.extend(make_ci_errors((ROOT / "Makefile").read_text()))
    if not any(
        step.get("run") == "make bootstrap ci-checks"
        for step in jobs.get("checks", {}).get("steps", [])
    ):
        errors.append("checks must run the complete ci-checks group")
    tests = jobs.get("tests", {})
    if tests.get("strategy") != {"fail-fast": False, "matrix": {"shard": [0, 1, 2, 3]}}:
        errors.append("tests must run all four complete native pytest shards")
    if tests.get("if") or not any(
        step.get("env") == {"CI_TEST_SHARD": "${{ matrix.shard }}"}
        and step.get("run")
        == 'uv run --frozen python -m scripts.run_ci_tests --shard "$CI_TEST_SHARD" --shards 4'
        for step in tests.get("steps", [])
    ):
        errors.append("tests must execute the full native collection partition")
    acceptance = jobs.get("acceptance", {})
    if acceptance.get("strategy") != {
        "fail-fast": False,
        "matrix": {"target": [name for name in CI_GROUPS if name != "ci-checks"]},
    }:
        errors.append("acceptance must run every complete make check group")
    if acceptance.get("if") or not any(
        step.get("env") == {"CI_ACCEPTANCE_TARGET": "${{ matrix.target }}"}
        and step.get("run") == 'make bootstrap "$CI_ACCEPTANCE_TARGET"'
        for step in acceptance.get("steps", [])
    ):
        errors.append("acceptance must execute every unchanged gate")
    required = jobs.get("required-result", {})
    if not any(
        step.get("run") == "make bootstrap observation-persistence-test"
        for step in jobs.get("observation-replay", {}).get("steps", [])
    ):
        errors.append("observation-replay must execute real PostgreSQL acceptance")
    if set(required.get("needs", [])) != set(jobs) - {"required-result"}:
        errors.append("required-result must depend on every check")
    if not any(
        step.get("run") == "make intelligence-delivery-bootstrap observation-broker-test"
        for step in jobs.get("observation-broker", {}).get("steps", [])
    ):
        errors.append("observation-broker must execute actual authenticated transport acceptance")
    makefile = (ROOT / "Makefile").read_text() if makefile is None else makefile
    broker_target = re.search(r"^observation-broker-test:\n\t([^\n]+)", makefile, re.MULTILINE)
    if broker_target is None or not all(
        required in broker_target.group(1)
        for required in (
            "REQUIRE_AI10_OBSERVATION_BROKER_TESTS=1",
            "AI10_OBSERVATION_BROKER_REPORT=artifacts/ai10-observation-broker.json",
            "tests/test_observation_broker_runtime.py",
            "--junitxml=artifacts/ai10-observation-broker-tests.xml",
        )
    ):
        errors.append("observation-broker target must require runtime execution and evidence")
    if required.get("if") != "always()":
        errors.append("required-result must run even after a failure")
    result_steps = required.get("steps", [])
    expected = {
        name.upper().replace("-", "_") + "_RESULT": "$" + "{{ needs." + name + ".result }}"
        for name in jobs
        if name != "required-result"
    }
    if not any(step.get("env") == expected for step in result_steps):
        errors.append("required-result must read the exact result expressions")
    for name in expected:
        if not any(f'test "${name}" = "success"' in step.get("run", "") for step in result_steps):
            errors.append(f"required-result must require {name} success")
    if not any(
        step.get("run")
        == "python3 scripts/update_ci_test_timings.py --reports reports/ci-shards --check"
        and not step.get("if")
        for step in result_steps
    ):
        errors.append("required-result must validate complete executed shard evidence")
    for job in jobs.values():
        if job.get("continue-on-error"):
            errors.append("jobs cannot ignore failures")
        for step in job.get("steps", []):
            if step.get("continue-on-error"):
                errors.append("steps cannot ignore failures")
            action = step.get("uses")
            if action and not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}", action
            ):
                errors.append("external actions must use a full commit SHA")
    return errors


def main() -> int:
    errors = []
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        content = re.sub(r"```.*?```", "", path.read_text(), flags=re.DOTALL)
        for target in re.findall(r"\]\(([^)]+)\)", content):
            target = target.split("#", 1)[0].strip("<>")
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).exists():
                errors.append(f"Missing local link in {path.relative_to(ROOT)}: {target}")
    workflow = yaml.safe_load((ROOT / ".github/workflows/required-ci.yml").read_text())
    errors.extend(workflow_errors(workflow))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Documentation links and required-CI contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
