"""Disposable real model registry acceptance; no production forecast qualification."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import local_stack as stack
import mlflow_store as store

REPORT = stack.ROOT / "docs/evidence/05-03-lifecycle.json"


def main() -> int:
    project = "retailops_ai_lifecycle_" + uuid.uuid4().hex[:10]
    owned, source_stopped = False, False
    source = stack.project_name()
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "target_project": project,
    }
    stage = "initialize"
    try:
        stack.environment_file(create=True)
        docker = store.compose(project)[0]
        for command in (
            store.compose(project, "ps", "-a", "-q"),
            [
                docker,
                "volume",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
            [
                docker,
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
        ):
            if store.checked_run(command).strip():
                raise ValueError("lifecycle_target_already_exists")
        store.require_fresh_test_images(project)
        owned = True
        if store.checked_run(
            store.compose(source, "ps", "--status", "running", "-q", "mlflow")
        ).strip():
            store.checked_run(store.compose(source, "stop", "mlflow"))
            source_stopped = True
        stage = "build"
        store.checked_run(store.compose(project, "build", "api", "mlflow"))
        stage = "database_and_migrations"
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db"))
        store.checked_run(store.compose(project, "run", "--rm", "-T", "api-migrate"))
        store.checked_run(store.compose(project, "run", "--rm", "-T", "mlflow-migrate"))
        stage = "mlflow_start"
        store.checked_run(store.compose(project, "up", "-d", "--wait", "mlflow"))
        acceptance = store.compose(
            project,
            "run",
            "--rm",
            "-T",
            "-e",
            "APP_ENV=test",
            "api-migrate",
            "python",
            "-m",
            "retailops_ai.model_lifecycle.acceptance",
        )
        stage = "registry_decisions"
        report.update(json.loads(store.checked_run(acceptance)))
        stage = "restart"
        store.checked_run(store.compose(project, "kill", "-s", "SIGKILL", "mlflow", "db"))
        store.checked_run(store.compose(project, "up", "-d", "--wait", "db", "mlflow"))
        stage = "restart_inspection"
        inspection = json.loads(store.checked_run([*acceptance, "--inspect"]))
        if inspection["release_id"] != report["release_id"] or inspection["status"] != "passed":
            raise ValueError("lifecycle_restart_inspection_mismatch")
        report["checks"].extend(inspection["checks"])
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(
            json.dumps(
                {"status": "passed", "purpose": report["purpose"], "report": str(REPORT)},
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            json.dumps(
                {
                    "error": "model_lifecycle_smoke_failed",
                    "stage": stage,
                    "child_diagnostic": error.diagnostic
                    if isinstance(error, store.ComposeCommandError)
                    else None,
                }
            )
        )
        return 2
    finally:
        try:
            if owned:
                store.cleanup_test_stacks(project)
        finally:
            if source_stopped:
                store.checked_run(store.compose(source, "up", "-d", "--wait", "mlflow"))


if __name__ == "__main__":
    raise SystemExit(main())
