"""Real, disposable combined-store restore, interrupted backup and pending decision recovery."""

import json
import re
import signal
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store

REPORT = stack.ROOT / "docs/evidence/05-03-store.json"


def acceptance(project: str, module: str, *args: str) -> dict[str, Any]:
    return dict(
        json.loads(
            store.checked_run(
                store.compose(
                    project,
                    "run",
                    "--rm",
                    "-T",
                    "-e",
                    "APP_ENV=test",
                    "api-migrate",
                    "python",
                    "-m",
                    "retailops_ai.model_lifecycle." + module,
                    *args,
                )
            )
        )
    )


def interrupted_backup(project: str) -> None:
    # Kill only this dedicated controller after the actual database fence commits.
    code = """
import os, signal, sys
import lifecycle_store as store
def interrupt(project):
    os.kill(os.getpid(), signal.SIGKILL)
store.database_inventory = interrupt
store.backup(sys.argv[1])
"""
    result = subprocess.run(  # noqa: S603 - fixed isolated acceptance child
        [sys.executable, "-c", code, project],
        cwd=stack.ROOT / "scripts",
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != -signal.SIGKILL or not combined.MAINTENANCE.exists():
        raise ValueError("backup_interruption_not_observed")
    if combined.limits(project)["limits"] != dict.fromkeys(combined.DATABASES, 0):
        raise ValueError("backup_interruption_did_not_retain_fence")
    for database, (_, role) in combined.DATABASES.items():
        password = "AI_DB_PASSWORD" if role == "ai_app" else "MLFLOW_DB_PASSWORD"
        command = store.compose(
            project,
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            f'PGPASSWORD="${password}" exec psql -h 127.0.0.1 -U {role} -d {database} -At -c "SELECT 1"',
        )
        try:
            store.checked_run(command)
        except ValueError:
            pass
        else:
            raise ValueError("fenced_database_accepted_application_connection")
    with combined.controller_lock():
        combined.resume_maintenance()


def main() -> int:
    source = "retailops_ai_store_source_" + uuid.uuid4().hex[:10]
    target = "retailops_ai_store_target_" + uuid.uuid4().hex[:10]
    owned: list[str] = []
    original_compose = store.compose

    def isolated_compose(project: str, *args: str) -> list[str]:
        base = original_compose(project)
        if project in {source, target}:
            base.extend(["-f", str(stack.ROOT / "infra/compose-acceptance.yaml")])
        return [*base, *args]

    store.compose = isolated_compose
    report: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "source_project": source,
        "target_project": target,
        "purpose": "lifecycle_mechanics_only",
        "checks": [],
    }
    phase = "preflight"
    try:
        stack.environment_file(create=True)
        if combined.MAINTENANCE.exists():
            raise ValueError("preexisting_maintenance_requires_operator_resume")
        combined.require_fresh(source)
        combined.require_fresh(target)
        store.require_fresh_test_images(source)
        store.require_fresh_test_images(target)
        owned.append(source)
        phase = "source_setup"
        store.checked_run(store.compose(source, "build", "api", "mlflow"))
        store.checked_run(store.compose(source, "up", "-d", "--wait", "db"))
        for service in ("api-migrate", "mlflow-migrate"):
            store.checked_run(store.compose(source, "run", "--rm", "-T", service))
        store.checked_run(store.compose(source, "up", "-d", "--wait", "api", "mlflow"))
        phase = "source_lifecycle"
        original = acceptance(source, "acceptance")
        inspected = acceptance(source, "acceptance", "--inspect")
        if original["release_id"] != inspected["release_id"]:
            raise ValueError("source_release_inspection_mismatch")
        report["release_id"] = original["release_id"]
        phase = "interrupted_backup"
        interrupted_backup(source)
        if combined.running_services(source) != ["api", "mlflow"]:
            raise ValueError("backup_resume_did_not_restore_services")
        acceptance(source, "acceptance", "--inspect")
        report["checks"].append(
            "sigkill_backup_retains_fence_and_explicit_resume_restores_services"
        )
        report["checks"].append("both_fenced_databases_reject_application_connections")
        phase = "pending_registration"
        acceptance(source, "store_acceptance", "--prepare")
        phase = "combined_backup"
        archive = combined.backup(source)
        manifest = combined.verify_bundle(archive)
        if combined.running_services(source) != ["api", "mlflow"]:
            raise ValueError("backup_did_not_restore_prior_service_state")
        report.update(
            {
                "backup_id": manifest["backup_id"],
                "state_sha256": manifest["state_sha256"],
                "files": manifest["files"],
                "artifact_files": manifest["artifact_files"],
                "artifact_bytes": manifest["artifact_bytes"],
                "pins": manifest["pins"],
            }
        )
        report["checks"].append("coherent_offline_both_databases_and_all_artifacts_verified")
        store.checked_run(store.compose(source, "stop", "api", "mlflow"))
        # Fresh target was checked before any target mutation; cleanup owns only this UUID.
        phase = "fresh_target_restore"
        restored = combined.restore(archive, target, on_created=lambda: owned.append(target))
        if restored["state_sha256"] != manifest["state_sha256"] or combined.running_services(
            target
        ):
            raise ValueError("restore_postcondition_mismatch")
        report["checks"].append("fresh_target_all_table_rows_sequences_and_artifact_bytes_match")
        try:
            combined.restore(archive, target)
        except ValueError as exc:
            if str(exc) != "lifecycle_restore_requires_fresh_project":
                raise
        else:
            raise ValueError("restore_overwrote_existing_target")
        report["checks"].append("repeated_restore_refuses_existing_target")
        # Same credentials/config, recovered DB revision, no automatic migration over evidence.
        phase = "restored_lifecycle_recovery"
        store.checked_run(store.compose(target, "up", "-d", "--wait", "api", "mlflow"))
        recovery = acceptance(target, "store_acceptance")
        if recovery["release_id"] != original["release_id"]:
            raise ValueError("restored_release_mismatch")
        report["checks"].extend(recovery["checks"])
        phase = "restored_restart"
        store.checked_run(store.compose(target, "kill", "-s", "SIGKILL", "api", "mlflow", "db"))
        store.checked_run(store.compose(target, "up", "-d", "--wait", "db", "api", "mlflow"))
        # Registry and journal are re-read after restart; completed request replay is stable.
        restarted = acceptance(target, "store_acceptance", "--inspect")
        if restarted["release_id"] != original["release_id"]:
            raise ValueError("restored_release_changed_after_restart")
        report["checks"].append("restored_api_ready_and_dependencies_survive_sigkill_restart")
        report["status"] = "passed"
        report["forecast_quality_approved"] = False
        report["runtime_status"] = "not_integrated"
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"status": "passed", "report": str(REPORT)}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        reason = str(exc) if re.fullmatch(r"[a-z0-9_]{1,120}", str(exc)) else "details_redacted"
        print(
            json.dumps({"error": "lifecycle_store_smoke_failed", "phase": phase, "reason": reason})
        )
        return 2
    finally:
        try:
            if combined.MAINTENANCE.exists():
                record = json.loads(combined.MAINTENANCE.read_text())
                if record["project"] in owned:
                    with combined.controller_lock():
                        combined.resume_maintenance()
            store.cleanup_test_stacks(*reversed(owned))
        finally:
            store.compose = original_compose


if __name__ == "__main__":
    raise SystemExit(main())
