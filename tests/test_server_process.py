"""A real local socket process catches packaging, lifespan and server-log regressions."""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx2 as httpx

from retailops_ai.config import Settings


def test_cli_serves_real_http_and_exits_cleanly(tmp_path):
    token = "local-test-" + "x" * 40
    sentinel = "private-socket-value-must-not-appear"
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {f.validation_alias for f in Settings.model_fields.values()}
    }
    env.update(
        APP_ENV="test",
        ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        HTTP_PORT=str(port),
        METRICS_TOKEN=token,
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "retailops_ai", "serve"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as c:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    raise AssertionError(f"server exited: {process.returncode}; {stdout}; {stderr}")
                try:
                    health = c.get("/health")
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("server did not start") from None
                    time.sleep(0.025)
            assert health.status_code == 200
            assert "server" not in health.headers
            assert c.get("/ready").json()["status"] == "ready"
            assert c.get("/version").json()["model"] is None
            assert c.get("/metrics").status_code == 401
            assert (
                c.get("/metrics", headers={"Authorization": f"Bearer {token}"}).status_code == 200
            )
            assert c.get(f"/{sentinel}?secret={sentinel}").status_code == 404
            assert c.get("/health", headers={"X-Forwarded-For": sentinel}).status_code == 200
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    assert process.returncode == -signal.SIGTERM
    assert not stdout
    assert token not in stderr and sentinel not in stderr
    records = [json.loads(line) for line in stderr.splitlines()]
    assert any(r["event"] == "application_stopped" for r in records)
    requests = [r for r in records if r["event"] == "http_request"]
    assert len(requests) >= 7
    assert all(r["correlation_id"] and r["trace_id"] for r in requests)
    assert not (tmp_path / "artifacts").exists()


def test_local_credentials_and_scope_over_real_loopback(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    private = tmp_path / "access"
    initialized = subprocess.run(
        [
            sys.executable,
            "-m",
            "retailops_ai",
            "access-init",
            "--grants-file",
            str(root / "contracts/access/v1/grant-template.v1.example.json"),
            "--output-dir",
            str(private),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert initialized.returncode == 0
    assert json.loads(initialized.stdout) == {"status": "initialized"}
    credentials = {
        entry["principal_id"]: entry["bearer_token"]
        for entry in json.loads((private / "api-client-credentials.json").read_text())[
            "credentials"
        ]
    }
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {f.validation_alias for f in Settings.model_fields.values()}
    }
    env.update(
        APP_ENV="test",
        ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        HTTP_PORT=str(port),
        API_AUTH_FILE=str(private / "api-access-policy.json"),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "retailops_ai", "serve"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    request = {
        "schema_version": "1.0",
        "product_ids": ["p-101"],
        "selling_location_ids": ["s-03"],
        "channel": "store",
    }
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=1) as c:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None
                try:
                    assert c.get("/health").status_code == 200
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline:
                        raise AssertionError("authenticated server did not start") from None
                    time.sleep(0.025)
            viewer = {"Authorization": f"Bearer {credentials['local-viewer']}"}
            admin = {"Authorization": f"Bearer {credentials['local-admin']}"}
            missing = c.get("/api/v1/identity")
            assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
            assert (
                c.get("/api/v1/identity", headers=viewer).json()["principal_id"] == "local-viewer"
            )
            assert (
                c.post("/api/v1/access/forecast-check", json=request, headers=viewer).status_code
                == 200
            )
            for changes in (
                {"product_ids": ["p-other"]},
                {"selling_location_ids": ["s-other"]},
                {"channel": "online"},
            ):
                assert (
                    c.post(
                        "/api/v1/access/forecast-check", json={**request, **changes}, headers=viewer
                    ).status_code
                    == 403
                )
            assert c.get("/api/v1/admin/access-policy", headers=viewer).status_code == 403
            assert c.get("/api/v1/admin/access-policy", headers=admin).status_code == 200
            assert (
                c.post("/api/v1/access/forecast-check", json=request, headers=admin).status_code
                == 403
            )
            assert (
                c.post(
                    "/api/v1/access/forecast-check",
                    json={**request, "role": "admin"},
                    headers=viewer,
                ).status_code
                == 422
            )
            assert c.get("/ready").status_code == 200
    finally:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    assert process.returncode == -signal.SIGTERM and not stdout
    assert all(value not in stderr for value in credentials.values())
    assert str(private) not in stderr
    records = [json.loads(line) for line in stderr.splitlines()]
    assert any(record["event"] == "application_stopped" for record in records)
    assert not (tmp_path / "artifacts").exists()
