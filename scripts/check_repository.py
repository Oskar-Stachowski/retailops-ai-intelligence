"""Check documentation links and the minimum required-CI contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_GROUPS = {
    "ci-checks": "lint type-check docs-check forecast-runtime-check contracts-check package compose-config",
    "ci-source-inputs": "handoff-check snapshot-import-check curated-check anomaly-inputs-check raw-dq-check return-inputs-check",
    "ci-qualified-inputs": "full-raw-dq-check day-qualification-check qualified-anomaly-inputs-check",
    "ci-detectors": "anomaly-detectors-check",
    "ci-forecast": "forecast-calendar-check forecast-features-check forecast-manifests-check forecast-baselines-check forecast-models-check forecast-backtest-check forecast-quality-check forecast-remediation-check forecast-run-check forecast-acceptance-check",
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


def workflow_errors(workflow: dict[str | bool, Any]) -> list[str]:
    errors = []
    events: dict[str, Any] = workflow.get("on") or workflow.get(True) or {}
    if not all(event in events for event in ("push", "pull_request", "workflow_dispatch")):
        errors.append("CI must cover push, pull_request and workflow_dispatch")
    push = events.get("push") or {}
    pull_request = events.get("pull_request") or {}
    if "branches" in push and not {"main", "ai/**"}.issubset(push["branches"]):
        errors.append("CI push must cover main and AI development branches")
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
    if not any(
        step.get("run") == "make bootstrap compose-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute real Compose acceptance")
    if not any(
        step.get("run") == "make mlflow-store-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute MLflow backup/restore acceptance")
    if not any(
        step.get("run") == "make model-lifecycle-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute model lifecycle recovery acceptance")
    if not any(
        step.get("run") == "make lifecycle-store-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute combined lifecycle backup/restore acceptance")
    if not any(
        step.get("run") == "make forecast-queue-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute forecast queue acceptance")
    if not any(
        step.get("run") == "make forecast-input-store-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute forecast input store acceptance")
    if not any(
        step.get("run") == "make forecast-publication-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute forecast publication acceptance")
    if not any(
        step.get("run") == "make forecast-read-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute forecast read acceptance")
    if not any(
        step.get("run") == "make model-catalog-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute model catalog acceptance")
    if not any(
        step.get("run") == "make evaluations-smoke"
        for step in jobs.get("persistence", {}).get("steps", [])
    ):
        errors.append("persistence must execute evaluation acceptance")
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
    if set(required.get("needs", [])) != set(jobs) - {"required-result"}:
        errors.append("required-result must depend on every check")
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
