"""Original AI Assistant -> outbox -> broker -> Source SQL/API/built UI, zero AWS.

Run explicitly with Source's private Python environment, never normal AI pytest.
The original pinned Source fixtures provision and delete their own containers.
"""

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
from app.main import app
from fastapi.testclient import TestClient
from psycopg import sql
from test_intelligence_checkpoint_durability import checkpoint, receipts, run
from test_intelligence_durability import (
    context as context,
)
from test_intelligence_durability import (
    intelligence_runtime as intelligence_runtime,
)
from test_intelligence_durability import (
    positions,
    produce,
    rows,
)
from test_intelligence_durability import (
    runtime as runtime,
)
from test_realtime_durability import docker, free_port, wait_db

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ["AI12_SOURCE_ROOT"]).resolve()
SOURCE_COMMIT = "5c05445e8105c378107099785bae7355388ed7e7"
IMAGE = "pgvector/pgvector:0.8.6-pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b"


def private_write(path, value):
    path.write_text(value)
    path.chmod(0o600)


def child(command, *, cwd=ROOT, env=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=180)
    assert result.returncode == 0, "owned acceptance child failed: " + Path(command[0]).name
    return result.stdout


def ai_env():
    value = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    return value | {
        "PYTHONPATH": os.pathsep.join((str(ROOT / "tests"), str(ROOT / "src"))),
        "AI12_UNPAID_ACCEPTANCE": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
    }


def ready(url, process):
    deadline = time.monotonic() + 30
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        assert process.poll() is None, "owned server stopped"
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise AssertionError("owned server readiness timeout")


def test_original_ai12_emitter_source_consumer_api_and_built_ui(context, tmp_path, monkeypatch):
    assert os.getenv("AI12_UNPAID_ACCEPTANCE") == "1"
    assert child(["git", "rev-parse", "HEAD"], cwd=SOURCE).decode().strip() == SOURCE_COMMIT
    monkeypatch.setenv("RETAILOPS_ENABLE_SUGGESTION_FIXTURE_TRANSPORT", "1")
    report = ROOT / "artifacts/ai12-source-e2e"
    report.mkdir(parents=True, exist_ok=True)
    tmp_path.chmod(0o700)
    password, read_password = secrets.token_hex(24), secrets.token_hex(24)
    port = free_port()
    container = "ai12-source-e2e-" + secrets.token_hex(6)
    private_write(tmp_path / "postgres.env", "POSTGRES_PASSWORD=" + password + "\n")
    processes = []
    started = datetime.now(UTC)
    try:
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--env-file",
            str(tmp_path / "postgres.env"),
            "-p",
            f"127.0.0.1:{port}:5432",
            IMAGE,
            "postgres",
            "-c",
            "timezone=UTC",
            "-c",
            "max_connections=20",
            "-c",
            "shared_buffers=32MB",
        )
        admin = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        wait_db(admin)
        ai_url = f"postgresql+psycopg://ai_app:{password}@127.0.0.1:{port}/retailops_ai"
        now = datetime.now(UTC) - timedelta(seconds=2)
        observed = dict(
            product_id="22222222-2222-4222-8222-222222222222",
            store_id="33333333-3333-4333-8333-333333333333",
            channel="store",
        )
        source_conn = psycopg.conninfo.conninfo_to_dict(context.db)
        with psycopg.connect(context.db, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE ai12_observer LOGIN PASSWORD {}").format(
                    sql.Literal(read_password)
                )
            )
            conn.execute("ALTER ROLE ai12_observer SET timezone TO 'UTC'")
            conn.execute("GRANT USAGE ON SCHEMA public TO ai12_observer")
            conn.execute("GRANT SELECT ON realtime_event_log TO ai12_observer")
            conn.execute(
                "INSERT INTO realtime_event_log(event_id,event_type,topic,schema_version,source,correlation_id,occurred_at,ingested_at,status,attempt_count,payload,created_at,updated_at) VALUES (%s,'sale_completed','retailops.sales.v1','1.0','ai12-unpaid-fixture','ai12-unpaid-fixture',%s,%s,'failed_dead_lettered',1,%s::jsonb,%s,%s)",
                (uuid4(), now, now, json.dumps(observed), now, now),
            )
        source_read = f"postgresql+psycopg://ai12_observer:{read_password}@127.0.0.1:{source_conn['port']}/{source_conn['dbname']}"
        private_write(
            tmp_path / "connections.json",
            json.dumps(dict(admin=admin, password=password, ai=ai_url, source_read=source_read)),
        )
        helper = ROOT / "tests/ai12_source_acceptance/emitter.py"
        for operation in ("provision", "emit"):
            child(
                [os.environ["AI12_CORE_PYTHON"], str(helper), operation, str(tmp_path)],
                env=ai_env(),
            )
        event = json.loads((tmp_path / "event.json").read_bytes())
        wire = (tmp_path / "event.json").read_bytes()
        payload = event["payload"]
        assert payload["requires_human_review"] is True
        assert payload["action"] == "Ask an operator to review scoped failed event processing."
        assert payload["recommendation_type"] == "refresh_source_data"
        assert payload["evidence_refs"]
        private_write(
            tmp_path / "broker.json",
            json.dumps({"bootstrap.servers": context.bootstrap, "security.protocol": "PLAINTEXT"}),
        )
        delivered = json.loads(
            child(
                [
                    os.environ["AI12_DELIVERY_PYTHON"],
                    str(ROOT / "scripts/intelligence_outbox.py"),
                    "--suggestions",
                    "--broker-config",
                    str(tmp_path / "broker.json"),
                    "--max-events",
                    "1",
                ],
                env=ai_env()
                | {
                    "APP_ENV": "test",
                    "DATABASE_URL": ai_url,
                    "ARTIFACT_ROOT": str(tmp_path / "artifacts"),
                },
            )
        )
        assert delivered == {"status": "completed", "delivered": 1}
        child([os.environ["AI12_CORE_PYTHON"], str(helper), "verify", str(tmp_path)], env=ai_env())
        run(context)
        first = receipts(context)[0]
        duplicate_offset = produce(context, wire, partition=first[0])
        run(context, bootstrap=False)
        assert [r[2] for r in receipts(context)] == ["projected", "duplicate"]
        assert all(r[3] == hashlib.sha256(wire).hexdigest() for r in receipts(context))
        assert (
            checkpoint(context, first[0])[1] == positions(context)[first[0]] == duplicate_offset + 1
        )
        assert rows(
            context,
            "SELECT count(*) FROM ai_recommendation_results WHERE recommendation_id=%s",
            (payload["recommendation_id"],),
        ) == [(1,)]
        assert rows(
            context,
            "SELECT count(*) FROM ai_recommendation_inbox WHERE event_id=%s",
            (event["event_id"],),
        ) == [(1,)]
        token = secrets.token_hex(32)
        policy = {
            "version": "retailops-suggestion-access-1.0",
            "principals": [
                {
                    "principal_id": "ai12-e2e-reader",
                    "credential_sha256": hashlib.sha256(token.encode()).hexdigest(),
                    "capabilities": ["suggestion:read"],
                    "product_ids": [payload["product_id"]],
                    "selling_location_ids": [payload["selling_location_id"]],
                    "channels": [payload["channel"]],
                    "policy_sha256s": [payload["policy_sha256"]],
                    "agent_config_versions": [payload["agent_config_version"]],
                    "model_release_refs": payload["model_release_refs"],
                }
            ],
        }
        private_write(tmp_path / "suggestion-access.json", json.dumps(policy))
        private_write(tmp_path / "suggestion-credential", token)
        monkeypatch.setenv(
            "RETAILOPS_INTELLIGENCE_SUGGESTION_ACCESS_POLICY",
            str(tmp_path / "suggestion-access.json"),
        )
        with TestClient(app) as client:
            endpoint = "/intelligence/v2/recommendations"
            assert client.get(endpoint).status_code == 401
            headers = {"Authorization": "Bearer " + token}
            assert (
                client.get(endpoint, headers=headers, params={"product_id": "foreign"}).status_code
                == 403
            )
            response = client.get(endpoint + "/" + payload["recommendation_id"], headers=headers)
            assert response.status_code == 200
            assert response.json()["suggestion"] == payload
            assert response.json()["execution_authorized"] is False
            assert response.headers["cache-control"] == "no-store"
        # Own standard frontend proxy ports only in a disposable CI runner;
        # binding failure leaves any existing listener unchanged.
        with socket.socket() as owned:
            owned.bind(("127.0.0.1", 8000))
            owned.listen()
            with (tmp_path / "api.log").open("wb") as log:
                api = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--fd",
                        str(owned.fileno()),
                        "--no-access-log",
                    ],
                    cwd=SOURCE / "services/api",
                    env=os.environ,
                    pass_fds=(owned.fileno(),),
                    stdout=log,
                    stderr=log,
                )
                processes.append(api)
                ready("http://127.0.0.1:8000/health", api)
        with (tmp_path / "frontend.log").open("wb") as log:
            frontend = subprocess.Popen(
                [
                    shutil.which("node") or "/usr/bin/node",
                    "node_modules/vite/bin/vite.js",
                    "preview",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "4173",
                    "--strictPort",
                ],
                cwd=SOURCE / "frontend",
                env=os.environ,
                stdout=log,
                stderr=log,
            )
            processes.append(frontend)
            ready("http://127.0.0.1:4173/recommendations", frontend)
        child(
            ["node", str(ROOT / "tests/ai12_source_acceptance/browser.cjs")],
            env=dict(os.environ)
            | {
                "AI12_CONTROL": str(tmp_path),
                "AI12_SCREENSHOT": str(report / "suggestion-evidence.png"),
            },
        )
        browser = json.loads((tmp_path / "browser.json").read_bytes())
        result = dict(
            status="passed",
            ai_commit=child(["git", "rev-parse", "HEAD"]).decode().strip(),
            source_commit=SOURCE_COMMIT,
            started_at=started.isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            aws_calls=0,
            llm="explicit_scripted_policy_fixture_not_quality_acceptance",
            source_observation="invented_sales_event_in_actual_Source_SQL_SELECT_only",
            source_transport="accepted_v1_fixture_transport_opt_in",
            actual_assistant_api=True,
            actual_ai_sql_outbox=True,
            actual_locked_publisher=True,
            actual_broker=True,
            exact_original_payload=True,
            exact_wire_sha256=hashlib.sha256(wire).hexdigest(),
            source_atomic_receipts=["projected", "duplicate"],
            one_business_row=True,
            source_api_anonymous=401,
            source_api_foreign_scope=403,
            execution_authorized=False,
            browser=browser,
            screenshot_sha256=hashlib.sha256(
                (report / "suggestion-evidence.png").read_bytes()
            ).hexdigest(),
        )
        (report / "receipt.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        subprocess.run(
            [shutil.which("docker") or "/usr/bin/docker", "rm", "-fv", container],
            capture_output=True,
            timeout=45,
        )
