"""Real, disposable combined-store restore, interrupted backup and pending decision recovery."""

import json
import os
import re
import signal
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store

REPORT = stack.ROOT / "docs/evidence/05-03-store.json"
CHILD_REASONS = frozenset(
    {
        "lifecycle_maintenance_resume_required",
        "lifecycle_source_already_fenced",
        "lifecycle_backup_directory_permissions",
        "lifecycle_lock_permissions",
        "lifecycle_store_busy",
        "lifecycle_fence_record_invalid",
        "lifecycle_fence_sessions_remain",
        "mlflow_store_compose_command_failed",
        "docker_unavailable",
        "invalid_mlflow_project",
        "local_stack_not_initialized",
        "local_stack_permissions_too_open",
        "invalid_local_stack_configuration",
        "metadata_size_limit",
        "backup_interruption_signal_not_delivered",
        "details_redacted",
    }
)
CHILD_EXCEPTIONS = frozenset(
    {
        "ValueError",
        "SnapshotError",
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "ComposeCommandError",
        "TimeoutExpired",
        "ModuleNotFoundError",
        "RuntimeError",
        "KeyError",
        "TypeError",
        "other",
    }
)
CHILD_FRAMES = {
    "check_lifecycle_store.py": frozenset({"_backup_child", "command", "interrupt"}),
    "lifecycle_store.py": frozenset(
        {
            "backup",
            "controller_lock",
            "limits",
            "running_services",
            "write_private",
            "fence",
            "set_limits",
            "sql",
            "admin_command",
            "resume_maintenance",
        }
    ),
    "mlflow_store.py": frozenset({"compose", "checked_run"}),
    "local_stack.py": frozenset({"environment_file"}),
}
CHILD_FUNCTIONS = frozenset().union(*CHILD_FRAMES.values())


def acceptance_compose(
    original: Callable[..., list[str]], owned: tuple[str, ...]
) -> Callable[..., list[str]]:
    """Use the same disposable network-only services in parent and fresh child."""

    def command(project: str, *args: str) -> list[str]:
        base = original(project)
        if project in owned:
            base.extend(["-f", str(stack.ROOT / "infra/compose-acceptance.yaml")])
        return [*base, *args]

    return command


def _backup_child(project: str) -> int:
    original = store.compose
    store.compose = acceptance_compose(original, (project,))

    inventory = combined.database_inventory

    def interrupt(project: str) -> NoReturn:
        os.kill(os.getpid(), signal.SIGKILL)
        raise RuntimeError("backup_interruption_signal_not_delivered")

    combined.database_inventory = interrupt
    try:
        combined.backup(project)
        return 0
    except Exception as exc:
        # Only static codes, exception classes and owned-code positions. No
        # traceback text, command output, local values or credentials are emitted.
        frames: list[dict[str, str | int]] = []
        frame = exc.__traceback__
        while frame is not None and len(frames) < 8:
            code = frame.tb_frame.f_code
            if (
                code.co_name in CHILD_FRAMES.get(Path(code.co_filename).name, ())
                and 0 < frame.tb_lineno < 10000
            ):
                frames.append({"function": code.co_name, "line": frame.tb_lineno})
            frame = frame.tb_next
        diagnostic = {
            "error": "interrupted_backup_child_failed",
            "reason": str(exc) if str(exc) in CHILD_REASONS else "details_redacted",
            "exception_type": type(exc).__name__
            if type(exc).__name__ in CHILD_EXCEPTIONS
            else "other",
            "frames": frames,
        }
        print(json.dumps(diagnostic), file=sys.stderr)
        return 2
    finally:
        store.compose = original
        combined.database_inventory = inventory


class InterruptedBackupError(ValueError):
    def __init__(self, returncode: int, maintenance_present: bool, stderr: bytes) -> None:
        super().__init__("backup_interruption_not_observed")
        self.diagnostic: dict[str, Any] = {
            "returncode": returncode,
            "maintenance_present": maintenance_present,
        }
        for line in stderr[-8192:].splitlines():
            try:
                value = json.loads(line)
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if (
                isinstance(value, dict)
                and set(value) == {"error", "reason", "exception_type", "frames"}
                and value["error"] == "interrupted_backup_child_failed"
                and isinstance(value["reason"], str)
                and value["reason"] in CHILD_REASONS
                and isinstance(value["exception_type"], str)
                and value["exception_type"] in CHILD_EXCEPTIONS
                and isinstance(value["frames"], list)
                and len(value["frames"]) <= 8
                and all(
                    isinstance(item, dict)
                    and set(item) == {"function", "line"}
                    and isinstance(item["function"], str)
                    and item["function"] in CHILD_FUNCTIONS
                    and type(item["line"]) is int
                    and 0 < item["line"] < 10000
                    for item in value["frames"]
                )
            ):
                self.diagnostic["child"] = value


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
    code = "import sys; import check_lifecycle_store as smoke; sys.exit(smoke._backup_child(sys.argv[1]))"
    result = subprocess.run(  # noqa: S603 - fixed isolated acceptance child
        [sys.executable, "-c", code, project],
        cwd=stack.ROOT / "scripts",
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != -signal.SIGKILL or not combined.MAINTENANCE.exists():
        raise InterruptedBackupError(
            result.returncode, combined.MAINTENANCE.exists(), result.stderr
        )
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

    store.compose = acceptance_compose(original_compose, (source, target))
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
        failure: dict[str, Any] = {
            "error": "lifecycle_store_smoke_failed",
            "phase": phase,
            "reason": reason,
        }
        if isinstance(exc, InterruptedBackupError):
            failure["interruption"] = exc.diagnostic
        print(json.dumps(failure))
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
