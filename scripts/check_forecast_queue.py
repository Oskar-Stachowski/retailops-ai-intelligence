"""Disposable, no-host-port real queue/worker acceptance and database crash recovery."""

import argparse
import json
import re
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store

REPORT = stack.ROOT / "docs/evidence/05-04-queue.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    project = "retailops_ai_queue_" + uuid.uuid4().hex[:10]
    owned = False
    target = project
    original = store.compose

    def isolated(project: str, *args: str) -> list[str]:
        base = original(project)
        if project == target:
            base.extend(["-f", str(stack.ROOT / "infra/compose-acceptance.yaml")])
        return [*base, *args]

    store.compose = isolated
    report: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "project": project}
    phase = "preflight"
    try:
        stack.environment_file(create=True)
        combined.require_fresh(project)
        store.require_fresh_test_images(project)
        owned = True
        phase = "setup"
        store.checked_run(store.compose(project, "build", "api", "mlflow"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db"))
        for service in ("api-migrate", "mlflow-migrate"):
            store.checked_run(store.compose(project, "run", "--rm", "-T", service))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "mlflow"))
        command = store.compose(
            project,
            "run",
            "--rm",
            "-T",
            "-e",
            "APP_ENV=test",
            "api-migrate",
            "python",
            "-m",
            "retailops_ai.forecast_jobs.acceptance",
        )
        phase = "queue_acceptance"
        result = subprocess.run(  # noqa: S603 - fixed disposable acceptance command
            command, cwd=stack.ROOT, capture_output=True, timeout=180, check=False
        )
        if result.returncode:
            # Print only our static error markers, never database URLs, SQL values or tokens.
            markers = re.findall(
                rb"(?:ValueError|RuntimeError): ([a-z][a-z_]{0,100})\s*$",
                result.stderr,
                re.MULTILINE,
            )
            print(json.dumps({"acceptance_errors": [v.decode() for v in markers]}))
            raise ValueError("queue_acceptance_failed")
        report.update(json.loads(result.stdout))
        before = json.loads(store.checked_run([*command, "--inspect"]))
        phase = "database_sigkill_restart"
        store.checked_run(store.compose(project, "kill", "-s", "SIGKILL", "db", "mlflow"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db", "mlflow"))
        after = json.loads(store.checked_run([*command, "--inspect"]))
        if before != after:
            raise ValueError("queue_state_changed_after_restart")
        report["counts"] = after["counts"]
        report["state_sha256"] = after["state_sha256"]
        report["checks"].extend(after["checks"])
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {"status": "passed", "purpose": report["purpose"], "report": str(args.report)}
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"error": "forecast_queue_smoke_failed", "phase": phase}))
        return 2
    finally:
        try:
            if owned:
                store.cleanup_test_stacks(project)
        finally:
            store.compose = original


if __name__ == "__main__":
    raise SystemExit(main())
