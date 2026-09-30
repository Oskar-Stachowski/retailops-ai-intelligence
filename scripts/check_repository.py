"""Check documentation links and the minimum required-CI contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


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
        step.get("run") == "make bootstrap check"
        for step in jobs.get("checks", {}).get("steps", [])
    ):
        errors.append("checks must run every make check gate")
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
