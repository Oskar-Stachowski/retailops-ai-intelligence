"""Coherent v12 backup/restore on cached PostgreSQL/MLflow images and private UUID projects."""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import lifecycle_store as combined
import local_stack as stack
import mlflow_store as store
from check_v12_lifecycle import IMAGES, ROOT, docker, tests, wait_ready
from sqlalchemy import create_engine, text
from v12_backup_fixture_stack import FixtureStack

from retailops_ai.config import Settings
from retailops_ai.migrations.runner import migrate

REPORT = ROOT / "reports/ai05-v12-backup-acceptance.json"


def native_tests(invocation: Path, work: Path, *, phase: str) -> None:
    inspect = phase != "source"
    tests(
        invocation,
        inspect=inspect,
        work=work,
        include_queue=True,
        include_outputs=True,
        include_metadata=True,
    )
    name = "restart" if inspect else "acceptance"
    for source, suffix in (
        (ROOT / "reports" / ("ai05-v12-metadata-" + name + "-tests.xml"), "-tests.xml"),
        (work / (name + ".log"), ".log"),
    ):
        destination = ROOT / "reports" / ("ai05-v12-backup-native-" + phase + suffix)
        shutil.copyfile(source, destination)
        destination.chmod(0o600)


def backup_test(invocation: Path, work: Path, *, phase: str) -> None:
    env = {k: os.environ[k] for k in ("PATH", "TMPDIR") if k in os.environ}
    env["AI05_V12_PRIVATE_INVOCATION"] = str(invocation)
    if phase == "prepare":
        env["AI05_V12_BACKUP_PREPARE"] = "1"
    if phase == "restart":
        env["AI05_V12_RESTART_INSPECT"] = "1"
    log = ROOT / "reports" / ("ai05-v12-backup-state-" + phase + ".log")
    receipt = ROOT / "reports" / ("ai05-v12-backup-state-" + phase + "-tests.xml")
    with log.open("wb") as stream:
        log.chmod(0o600)
        try:
            result = subprocess.run(  # noqa: S603 - fixed local test
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/check_v12_backup.py",
                    "--junitxml=" + str(receipt),
                ],
                cwd=ROOT,
                env=env,
                stdout=stream,
                stderr=stream,
                timeout=180,
                check=False,
            )
        finally:
            if receipt.exists():
                receipt.chmod(0o600)
    if result.returncode:
        raise ValueError("v12_backup_" + phase + "_test_failed")


def interrupt(work: Path) -> int:
    fixture = FixtureStack(work)
    fixture.install()

    def stop_at_fence(project: str) -> dict[str, Any]:
        os.kill(os.getpid(), signal.SIGKILL)
        raise RuntimeError("unreachable")

    combined.database_inventory = stop_at_fence
    combined.backup(fixture.projects["source"])
    return 1


def refused_connections(fixture: FixtureStack, project: str) -> None:
    for database, (_, role) in combined.DATABASES.items():
        key = "AI_DB_PASSWORD" if role == "ai_app" else "MLFLOW_DB_PASSWORD"
        command = fixture.compose(
            project,
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            'PGPASSWORD="$'
            + key
            + '" exec psql -h 127.0.0.1 -U '
            + role
            + " -d "
            + database
            + ' -At -c "SELECT 1"',
        )
        try:
            store.checked_run(command)
        except ValueError:
            continue
        raise ValueError("v12_backup_application_fence_not_enforced")


def main(mlflow_image: str) -> int:
    started = time.monotonic()
    report: dict[str, Any] = dict(status="running", purpose="isolated_v12_backup_mechanics_only")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report) + "\n")
    phase = "cached_images"
    owned = []
    fixture = None
    saved = (store.compose, stack.LOCAL, combined.LOCK, combined.MAINTENANCE, combined.BACKUPS)
    try:
        images = {
            r: docker("image", "inspect", "--format", "{{.Id}}", ref)
            for r, ref in {"db": IMAGES["db"], "mlflow": mlflow_image}.items()
        }
        versions = docker(
            "run",
            "--rm",
            "--pull=never",
            "--memory=256m",
            "--cpus=0.5",
            "--entrypoint",
            "python",
            images["mlflow"],
            "-c",
            "from importlib.metadata import version; print(version('mlflow'),version('psycopg2-binary'))",
        )
        if versions != "3.16.1 2.9.11":
            raise ValueError("v12_backup_cached_mlflow_versions")
        work = Path(tempfile.mkdtemp(prefix="ai05-v12-backup-")).resolve()
        fixture = FixtureStack.create(work, uuid.uuid4().hex, images)
        fixture.install()
        source, target, failed = (fixture.projects[r] for r in ("source", "target", "failed"))
        for project in fixture.projects.values():
            combined.require_fresh(project)
        owned.append(source)
        phase = "source_setup"
        store.checked_run(
            fixture.compose(source, "up", "--no-build", "--pull", "never", "-d", "--wait", "db")
        )
        url = fixture.url(source)
        migrate(Settings(APP_ENV="test", ARTIFACT_ROOT=ROOT / "reports", DATABASE_URL=url))
        engine = create_engine(url, hide_parameters=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ai.service_metadata(name,value) VALUES ('v12_acceptance_owner',CAST(:owner AS jsonb))"
                ),
                dict(owner=json.dumps(fixture.owner)),
            )
        engine.dispose()
        store.checked_run(
            fixture.compose(source, "up", "--no-build", "--pull", "never", "-d", "--wait", "mlflow")
        )
        invocation = fixture.private_invocation(source)
        wait_ready(url, fixture.mlflow_port(source))
        print("Cached isolated PostgreSQL AI and PostgreSQL MLflow ready.", flush=True)
        phase = "source_v12_acceptance"
        native_tests(invocation, work, phase="source")
        phase = "pending_state"
        backup_test(invocation, work, phase="prepare")
        fixture.write_artifact(source, b"retained-unreferenced-fixture-artifact")
        phase = "interrupted_backup"
        child = subprocess.run(  # noqa: S603 - kill only this child at its own fence
            [sys.executable, str(Path(__file__).resolve()), "--interrupt-work", str(work)],
            capture_output=True,
            cwd=ROOT,
            timeout=120,
            check=False,
        )
        if child.returncode != -signal.SIGKILL or not combined.MAINTENANCE.exists():
            raise ValueError("v12_backup_interruption_not_observed")
        if combined.limits(source)["limits"] != dict.fromkeys(combined.DATABASES, 0):
            raise ValueError("v12_backup_interruption_fence_lost")
        refused_connections(fixture, source)
        with combined.controller_lock():
            combined.resume_maintenance()
        if combined.running_services(source) != ["mlflow"]:
            raise ValueError("v12_backup_resume_service_mismatch")
        phase = "coherent_backup"
        bundle = combined.backup(source)
        manifest = combined.verify_bundle(bundle)
        before = json.loads((bundle / combined.STATE).read_bytes())
        # Shutdown only the fixture source before opening another target.
        store.checked_run(fixture.compose(source, "stop", "mlflow", "db"))
        phase = "partial_restore_fence"
        original_run = store.checked_run

        def corrupt_import(command: list[str], **kwargs: Any) -> bytes:
            result = original_run(command, **kwargs)
            if "import" in command and "/opt/retailops_mlflow_volume.py" in command:
                fixture.write_artifact(failed, b"explicit-fixture-corruption")
            return result

        store.checked_run = corrupt_import
        try:
            try:
                combined.restore(
                    bundle, failed, on_created=lambda: owned.append(failed), build_images=False
                )
            except ValueError as error:
                if str(error) != "lifecycle_restore_artifact_checksum_mismatch":
                    raise
            else:
                raise ValueError("v12_backup_partial_restore_was_accepted")
        finally:
            store.checked_run = original_run
        assert_fenced = combined.limits(failed)["limits"] == dict.fromkeys(combined.DATABASES, 0)
        if not assert_fenced or combined.running_services(failed):
            raise ValueError("v12_backup_failed_target_was_unfenced")
        refused_connections(fixture, failed)
        fixture.cleanup(failed)
        owned.remove(failed)
        phase = "fresh_target_restore"
        restored = combined.restore(
            bundle, target, on_created=lambda: owned.append(target), build_images=False
        )
        if restored["state_sha256"] != manifest["state_sha256"] or combined.running_services(
            target
        ):
            raise ValueError("v12_backup_restore_postcondition")
        try:
            combined.restore(bundle, target, build_images=False)
        except ValueError as error:
            if str(error) != "lifecycle_restore_requires_fresh_project":
                raise
        else:
            raise ValueError("v12_backup_existing_target_was_overwritten")
        print(
            "Both databases, sequences and all artifact bytes restored; checking recovery.",
            flush=True,
        )
        phase = "restored_recovery"
        store.checked_run(
            fixture.compose(target, "up", "--no-build", "--pull", "never", "-d", "--wait", "mlflow")
        )
        invocation = fixture.private_invocation(target)
        backup_test(invocation, work, phase="restored")
        native_tests(invocation, work, phase="restored")
        phase = "restored_restart"
        store.checked_run(fixture.compose(target, "kill", "-s", "SIGKILL", "mlflow", "db"))
        store.checked_run(
            fixture.compose(
                target, "up", "--no-build", "--pull", "never", "-d", "--wait", "db", "mlflow"
            )
        )
        invocation = fixture.private_invocation(target)
        backup_test(invocation, work, phase="restart")
        native_tests(invocation, work, phase="restart")
        report = json.loads((work / "state.json").read_bytes())
        stockout_report = json.loads((work / "state.json.stockout.json").read_bytes())
        if stockout_report["status"] != "passed":
            raise ValueError("stockout_backup_database_recovery_incomplete")
        report["stockout_database_mechanics"] = stockout_report
        report.update(
            status="passed",
            purpose="isolated_v12_backup_mechanics_only",
            backup_id=manifest["backup_id"],
            state_sha256=manifest["state_sha256"],
            backup_files=manifest["files"],
            artifact_files=manifest["artifact_files"],
            artifact_bytes=manifest["artifact_bytes"],
            backed_up_tables={d: before[d]["tables"] for d in combined.DATABASES},
            backed_up_sequences={d: before[d]["sequences"] for d in combined.DATABASES},
            images=images,
            mlflow_test_metadata_backend="postgresql",
            ai_test_database_backend="postgresql",
            docker_builds=0,
            image_pulls=0,
            persistent_source_stack_changed=False,
            test_compose_sha256=hashlib.sha256((work / "compose.yaml").read_bytes()).hexdigest(),
        )
        report["checks"].extend(
            [
                "v12_sigkill_backup_retains_both_database_fences_and_explicit_resume_restores_prior_service",
                "v12_coherent_backup_verifies_all_ai_and_mlflow_postgresql_tables_sequences_and_artifact_bytes",
                "v12_corrupt_unreferenced_artifact_keeps_partial_target_offline_and_both_database_connections_fenced",
                "v12_fresh_target_restore_matches_entire_database_state_and_all_artifacts_before_startup",
                "v12_existing_target_restore_is_refused_before_mutation",
                "v12_post_restore_registry_queue_publication_catalog_evaluations_and_recovery_survive_sigkill_restart",
            ]
        )
        # Retain the actual, verified small fixture bundle as private evidence.
        retained = ROOT / ".local/v12-backup-acceptance" / bundle.name
        retained.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if retained.exists():
            raise ValueError("v12_backup_evidence_destination_exists")
        shutil.copytree(bundle, retained)
        combined.verify_bundle(retained)
        report["private_fixture_bundle_retained"] = True
        # Resources are cleaned while the private controller and its config exist.
        for project in reversed(owned):
            fixture.cleanup(project)
        owned.clear()
        shutil.rmtree(work)
        result = 0
    except Exception:
        report.update(status="failed", failed_stage=phase)
        if fixture is not None:
            # Keep diagnostics and controller files private until owned cleanup ends.
            diagnostic = fixture.work / "error.log"
            diagnostic.write_text(traceback.format_exc())
            diagnostic.chmod(0o600)
            report["private_diagnostic_workspace"] = str(fixture.work)
        sys.stderr.write(
            "v12_backup_acceptance_failed at "
            + phase
            + "; inspect private reports/ai05-v12-backup logs\n"
        )
        result = 1
    finally:
        clean = True
        for project in reversed(owned):
            try:
                if fixture is not None:
                    fixture.cleanup(project)
            except Exception:
                clean = False

        store.compose, stack.LOCAL, combined.LOCK, combined.MAINTENANCE, combined.BACKUPS = saved
        report["owned_test_resources_removed"] = clean
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        if not clean:
            report["status"] = "cleanup_failed"
        REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if result == 0 and clean:
        print("V12 coherent backup/restore and recovery passed; owned test resources removed.")
    return result if clean else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mlflow-image", default=stack.project_name() + "-mlflow:local")
    parser.add_argument("--interrupt-work", type=Path)
    arguments = parser.parse_args()
    raise SystemExit(
        interrupt(arguments.interrupt_work)
        if arguments.interrupt_work
        else main(arguments.mlflow_image)
    )
