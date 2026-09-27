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
    if any(
        "paths" in (events.get(event) or {}) or "paths-ignore" in (events.get(event) or {})
        for event in ("push", "pull_request")
    ):
        errors.append("foundation CI must not skip new paths")
    if workflow.get("permissions") != {"contents": "read"}:
        errors.append("workflow permissions must be contents:read")
    jobs = workflow.get("jobs", {})
    required = jobs.get("required-result", {})
    if set(required.get("needs", [])) != set(jobs) - {"required-result"}:
        errors.append("required-result must depend on every check")
    if required.get("if") != "always()":
        errors.append("required-result must run even after a failure")
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
