"""Provision one disposable PostgreSQL container for AI12 offline acceptance.

Requires an already running Docker daemon. Never starts Compose or global services.
Credentials stay in a private temporary env file and child environment.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from retailops_ai.config import Settings
from retailops_ai.migrations.runner import migrate

IMAGE = "pgvector/pgvector:0.8.6-pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b"


def main() -> int:
    container = None
    docker = shutil.which("docker")
    if docker is None:
        print("Docker executable is required for this dedicated acceptance.")
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="ai12-native-offline-") as temporary:
            path = Path(temporary) / "postgres.env"
            admin_password, ai_password, reader_password = [
                secrets.token_urlsafe(36) for _ in range(3)
            ]
            path.write_text("POSTGRES_PASSWORD=" + admin_password + "\n")
            path.chmod(0o600)
            container = subprocess.check_output(  # noqa: S603 - fixed owned container and private env file
                [
                    docker,
                    "run",
                    "--detach",
                    "--rm",
                    "--label",
                    "retailops.ai12-native-offline=true",
                    "--name",
                    "ai12-native-offline-" + secrets.token_hex(6),
                    "--env-file",
                    str(path),
                    "-p",
                    "127.0.0.1::5432",
                    IMAGE,
                    "postgres",
                    "-c",
                    "shared_buffers=32MB",
                    "-c",
                    "max_connections=20",
                    "-c",
                    "max_parallel_workers=0",
                    "-c",
                    "timezone=UTC",
                    "-c",
                    "log_timezone=UTC",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if len(container) != 64 or any(c not in "0123456789abcdef" for c in container):
                raise RuntimeError("invalid_owned_container_id")
            bindings = json.loads(
                subprocess.check_output(  # noqa: S603 - validated owned container ID
                    [docker, "inspect", "--format", "{{json .NetworkSettings.Ports}}", container],
                    text=True,
                )
            )
            port = int(bindings["5432/tcp"][0]["HostPort"])

            def connect(database: str) -> psycopg.Connection[tuple[Any, ...]]:
                return psycopg.connect(
                    host="127.0.0.1",
                    port=port,
                    user="postgres",
                    password=admin_password,
                    dbname=database,
                    autocommit=True,
                    connect_timeout=1,
                )

            deadline = time.monotonic() + 30
            while True:
                try:
                    connection = connect("postgres")
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("owned_postgres_not_ready") from None
                    time.sleep(0.2)
            with connection as conn:
                conn.execute(
                    sql.SQL("CREATE ROLE ai_app LOGIN PASSWORD {}").format(sql.Literal(ai_password))
                )
                conn.execute("CREATE DATABASE retailops_ai OWNER ai_app")
                conn.execute(
                    sql.SQL("CREATE ROLE ai12_producer_read LOGIN PASSWORD {}").format(
                        sql.Literal(reader_password)
                    )
                )
                conn.execute("CREATE DATABASE retailops_producer_ai12")
            with connect("retailops_ai") as conn:
                conn.execute("CREATE EXTENSION vector")
                conn.execute("CREATE SCHEMA ai AUTHORIZATION ai_app")
            with connect("retailops_producer_ai12") as conn:
                conn.execute(
                    "CREATE TABLE realtime_event_log(event_id uuid PRIMARY KEY,schema_version text,event_type text,payload jsonb,status text,ingested_at timestamptz,processed_at timestamptz,updated_at timestamptz)"
                )
                conn.execute("REVOKE ALL ON realtime_event_log FROM PUBLIC")
                conn.execute("GRANT SELECT ON realtime_event_log TO ai12_producer_read")
            ai_url = f"postgresql+psycopg://ai_app:{ai_password}@127.0.0.1:{port}/retailops_ai"
            reader_url = f"postgresql+psycopg://ai12_producer_read:{reader_password}@127.0.0.1:{port}/retailops_producer_ai12"
            admin_url = f"postgresql+psycopg://postgres:{admin_password}@127.0.0.1:{port}/retailops_producer_ai12"
            migrate(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", DATABASE_URL=ai_url))
            env = os.environ | {
                "AI12_NATIVE_DATABASE_URL": ai_url,
                "AI12_NATIVE_PRODUCER_DATABASE_URL": reader_url,
                "AI12_NATIVE_PRODUCER_ADMIN_URL": admin_url,
                "REQUIRE_AI12_NATIVE_POSTGRES": "1",
                "AI12_NATIVE_REPORT": str(
                    Path("artifacts/ai12-native-offline-postgres.json").absolute()
                ),
                "AI12_SUGGESTION_REPORT": str(
                    Path("artifacts/ai12-suggestion-outbox.json").absolute()
                ),
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "VECLIB_MAXIMUM_THREADS": "1",
            }
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/test_native_offline_postgres.py",
                    "tests/test_suggestion_outbox_postgres.py",
                    "-q",
                ],
                env=env,
                check=False,
            ).returncode
    except Exception as error:
        print(
            json.dumps(
                {"error": "ai12_native_offline_acceptance_failed", "type": type(error).__name__}
            )
        )
        return 1
    finally:
        if container and len(container) == 64 and all(c in "0123456789abcdef" for c in container):
            subprocess.run(  # noqa: S603 - delete only this invocation's validated container ID
                [docker, "rm", "--force", container],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    raise SystemExit(main())
