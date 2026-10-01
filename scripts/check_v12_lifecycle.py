"""Disposable PostgreSQL/MLflow v12 acceptance using cached images; no builds or source-stack changes."""

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from retailops_ai.config import Settings
from retailops_ai.migrations.runner import migrate

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/ai05-v12-lifecycle-acceptance.json"
IMAGES = {
    "db": "pgvector/pgvector:0.8.6-pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b",
    "mlflow": "ghcr.io/mlflow/mlflow:v3.16.1@sha256:06058cc872276873e9759c635e773342aca4731e240f6d20d88ea43774083984",
}
OWNER_LABEL = "retailops.ai05.v12.acceptance_owner"


def docker(*arguments: str, required: bool = True) -> str:
    executable = shutil.which("docker")
    if executable is None:
        raise ValueError("v12_acceptance_docker_required")
    result = subprocess.run(  # noqa: S603 - fixed Docker command and task-owned resources
        [executable, *arguments], capture_output=True, text=True, timeout=60, check=False
    )
    if required and result.returncode:
        raise ValueError("v12_acceptance_docker_command_failed: " + result.stderr[-4096:])
    if result.returncode:
        return ""
    return (result.stdout + (result.stderr if arguments[0] == "logs" else "")).strip()


def port(name: str, container_port: str) -> int:
    value = docker("port", name, container_port)
    if not value.startswith("127.0.0.1:") or "\n" in value:
        raise ValueError("v12_acceptance_loopback_required")
    return int(value.rsplit(":", 1)[1])


def wait_ready(url: str, mlflow_port: int) -> None:
    engine = create_engine(url, connect_args={"connect_timeout": 1}, hide_parameters=True)
    try:
        for _ in range(60):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{mlflow_port}/health", timeout=2
                ) as response:  # noqa: S310 - ephemeral loopback service
                    if response.status == 200:
                        return
            except Exception:
                time.sleep(1)
        raise ValueError("v12_acceptance_services_not_ready")
    finally:
        engine.dispose()


def tests(
    invocation: Path,
    *,
    inspect: bool,
    work: Path,
    include_queue: bool = False,
    include_outputs: bool = False,
) -> None:
    env = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
    env["AI05_V12_PRIVATE_INVOCATION"] = str(invocation)
    if inspect:
        env["AI05_V12_RESTART_INSPECT"] = "1"
    name = "restart" if inspect else "acceptance"
    prefix = "ai05-v12-queue-" if include_queue else "ai05-v12-lifecycle-"
    if include_outputs:
        prefix = "ai05-v12-publication-"
    result = None
    try:
        with (work / (name + ".log")).open("wb") as log:
            result = subprocess.run(  # noqa: S603 - fixed explicit local acceptance test
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/check_v12_lifecycle.py",
                    *(["tests/check_v12_queue.py"] if include_queue else []),
                    *(["tests/check_v12_publication.py"] if include_outputs else []),
                    "--junitxml=" + str(ROOT / "reports" / (prefix + name + "-tests.xml")),
                ],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=log,
                timeout=600 if include_outputs else 300 if include_queue else 180,
                check=False,
            )
    finally:
        if result is None or result.returncode:
            destination = ROOT / "reports" / (prefix + name + ".log")
            destination.write_bytes((work / (name + ".log")).read_bytes())
            destination.chmod(0o600)
    if result.returncode:
        raise ValueError("v12_acceptance_tests_failed")


def main(*, include_queue: bool = False, include_outputs: bool = False) -> int:
    include_queue = include_queue or include_outputs
    owner = uuid.uuid4().hex
    names = {role: "retailops-ai05-v12-" + role + "-" + owner[:12] for role in IMAGES}
    engine = None
    cleaned = False
    report: dict[str, Any] = dict(status="running", purpose="isolated_v12_mechanics_only")
    report_path = (
        ROOT / "reports/ai05-v12-publication-acceptance.json"
        if include_outputs
        else ROOT / "reports/ai05-v12-queue-acceptance.json"
        if include_queue
        else REPORT
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report) + "\n")
    stage = "cached_images"
    password = ""
    try:
        pinned = {
            role: docker("image", "inspect", "--format", "{{.Id}}", image)
            for role, image in IMAGES.items()
        }
        for name in names.values():
            if docker("inspect", name, required=False):
                raise ValueError("v12_acceptance_name_already_exists")
        password = secrets.token_hex(24)
        stage = "start_postgresql"
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            names["db"],
            "--label",
            OWNER_LABEL + "=" + owner,
            "--memory=512m",
            "--cpus=1",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_USER=v12_test",
            "-e",
            "POSTGRES_PASSWORD=" + password,
            "-e",
            "POSTGRES_DB=v12_test",
            pinned["db"],
        )
        stage = "start_mlflow"
        docker(
            "run",
            "-d",
            "--pull=never",
            "--name",
            names["mlflow"],
            "--label",
            OWNER_LABEL + "=" + owner,
            "--memory=1g",
            "--cpus=1",
            "-p",
            "127.0.0.1::5000",
            pinned["mlflow"],
            "mlflow",
            "server",
            "--host",
            "0.0.0.0",  # noqa: S104 - isolated container; host mapping is loopback only
            "--port",
            "5000",
            "--workers",
            "1",
            "--backend-store-uri",
            "sqlite:////tmp/ai05-v12-mlflow.sqlite",
            "--artifacts-destination",
            "/tmp/ai05-v12-artifacts",  # noqa: S108 - task-owned isolated container filesystem
        )
        stage = "published_ports"
        db_port, mlflow_port = port(names["db"], "5432/tcp"), port(names["mlflow"], "5000/tcp")
        url = f"postgresql+psycopg://v12_test:{password}@127.0.0.1:{db_port}/v12_test"
        stage = "service_readiness"
        wait_ready(url, mlflow_port)
        print("Cached isolated PostgreSQL and MLflow ready.", flush=True)
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 3})
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA ai"))
            connection.execute(text("CREATE EXTENSION vector"))
        settings = Settings(APP_ENV="test", ARTIFACT_ROOT=ROOT / "reports", DATABASE_URL=url)
        stage = "database_migration"
        migrate(settings)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai.service_metadata(name,value) VALUES ('v12_acceptance_owner',CAST(:owner AS jsonb))"
                ),
                dict(owner=json.dumps(owner)),
            )
        with tempfile.TemporaryDirectory(prefix="ai05-v12-lifecycle-acceptance-") as temporary:
            work = Path(temporary).resolve()
            invocation = work / "invocation.json"
            state = work / "state.json"
            control = dict(
                owner=owner,
                database_url=url,
                mlflow_port=mlflow_port,
                state_file=str(state),
            )
            invocation.write_text(json.dumps(control))
            invocation.chmod(0o600)
            stage = "acceptance_tests"
            tests(
                invocation,
                inspect=False,
                work=work,
                include_queue=include_queue,
                include_outputs=include_outputs,
            )
            print("Registry/recovery/database guards passed; checking restart.", flush=True)
            stage = "restart"
            docker("restart", names["db"], names["mlflow"])
            # Docker can assign new host ports on restart when the mapping was ephemeral.
            db_port, mlflow_port = port(names["db"], "5432/tcp"), port(names["mlflow"], "5000/tcp")
            url = f"postgresql+psycopg://v12_test:{password}@127.0.0.1:{db_port}/v12_test"
            control.update(database_url=url, mlflow_port=mlflow_port)
            invocation.write_text(json.dumps(control))
            wait_ready(url, mlflow_port)
            stage = "restart_tests"
            tests(
                invocation,
                inspect=True,
                work=work,
                include_queue=include_queue,
                include_outputs=include_outputs,
            )
            report = json.loads(state.read_bytes())
        report.update(
            images=pinned, docker_builds=0, image_pulls=0, persistent_source_stack_changed=False
        )
        return_code = 0
    except Exception as error:
        report.update(status="failed", failed_stage=stage)
        diagnostics: dict[str, Any] = {
            "stage": stage,
            "error": str(error)[-4096:],
            "container_logs": {},
            "container_states": {},
        }
        for role, name in names.items():
            try:
                info = docker("inspect", name, required=False)
                if (
                    info
                    and json.loads(info)[0]["Config"].get("Labels", {}).get(OWNER_LABEL) == owner
                ):
                    diagnostics["container_states"][role] = json.loads(info)[0]["State"]
                    diagnostics["container_logs"][role] = docker("logs", "--tail", "80", name)
            except Exception:
                diagnostics["container_logs"][role] = "unavailable"
        destination = ROOT / "reports/ai05-v12-lifecycle-diagnostics.json"
        output = json.dumps(diagnostics, indent=2)
        if password:
            output = output.replace(password, "<ephemeral-test-password>")
        destination.write_text(output + "\n")
        destination.chmod(0o600)
        sys.stderr.write(
            f"v12_lifecycle_acceptance_failed at {stage}; inspect private reports/ai05-v12-lifecycle-diagnostics.json and test logs\n"
        )
        return_code = 1
    finally:
        if engine is not None:
            engine.dispose()
        cleaned = True
        for name in names.values():
            try:
                info = docker("inspect", name, required=False)
                if info:
                    item = json.loads(info)[0]
                    if item["Config"].get("Labels", {}).get(OWNER_LABEL) != owner:
                        cleaned = False
                        continue
                    docker("rm", "-f", "-v", name)
            except Exception:
                cleaned = False
        try:
            if docker("ps", "-aq", "--filter", "label=" + OWNER_LABEL + "=" + owner):
                cleaned = False
        except Exception:
            cleaned = False
        if report:
            report["owned_containers_and_anonymous_volumes_removed"] = cleaned
            if not cleaned:
                report["status"] = "cleanup_failed"
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if not cleaned:
        sys.stderr.write("v12_acceptance_owned_cleanup_incomplete\n")
        return 1
    if return_code == 0:
        print("V12 lifecycle + restart acceptance passed; task-owned containers removed.")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
