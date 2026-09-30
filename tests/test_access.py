import asyncio
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from retailops_ai.adapters.telemetry import JsonFormatter
from retailops_ai.api.app import create_app
from retailops_ai.api.middleware import MAX_ACCESS_BODY, access_body
from retailops_ai.api.models import Problem
from retailops_ai.api.schema import contract_openapi
from retailops_ai.cli import main
from retailops_ai.config import Settings
from retailops_ai.security.local import LocalAccess, load_private_policy, token_fingerprint
from retailops_ai.security.models import AccessPolicy, GrantTemplate
from retailops_ai.security.provision import provision

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/access/v1"
PRIVATE = "untrusted-private-marker"
REQUEST = {
    "schema_version": "1.0",
    "product_ids": ["p-101"],
    "selling_location_ids": ["s-03"],
    "channel": "store",
}


def policy_file(tmp_path, mutate=None):
    template = json.loads((CONTRACTS / "grant-template.v1.example.json").read_text())
    template["grants"].append(
        {
            "principal_id": "local-operator",
            "roles": ["operator"],
            "capabilities": ["forecast:read"],
            "scope": {
                "product_ids": ["p-202"],
                "selling_location_ids": ["s-04"],
                "channels": ["online"],
            },
        }
    )
    now = datetime.now(UTC)
    tokens = {g["principal_id"]: secrets.token_urlsafe(32) for g in template["grants"]}
    value = {
        **template,
        "credentials": [
            {
                "principal_id": principal,
                "token_sha256": token_fingerprint(token),
                "not_before": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "revoked": False,
            }
            for principal, token in tokens.items()
        ],
    }
    if mutate:
        mutate(value)
    path = tmp_path / "api-access-policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, tokens


def client(path=None, **overrides):
    settings = Settings(
        APP_ENV="test", ARTIFACT_ROOT="./artifacts", API_AUTH_FILE=path, **overrides
    )
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def problem(response, status):
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    parsed = Problem.model_validate(response.json())
    assert str(parsed.correlation_id) == response.headers["x-correlation-id"]
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_absent_policy_denies_application_endpoints_but_preserves_diagnostics():
    with client() as c:
        problem(c.get("/api/v1/identity"), 401)
        problem(c.get("/api/v1/admin/access-policy"), 401)
        problem(c.post("/api/v1/access/forecast-check", json=REQUEST), 401)
        for path in ("/health", "/ready", "/version"):
            assert c.get(path).status_code == 200


def test_verified_principal_and_scope_come_from_server_policy(tmp_path):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        result = c.get(
            "/api/v1/identity",
            headers=bearer(tokens["local-viewer"]),
            params={"user_id": "local-admin", "role": "admin", "principal_id": PRIVATE},
        )
        assert result.status_code == 200
        assert result.json()["principal_id"] == "local-viewer"
        assert result.json()["roles"] == ["viewer"]
        assert result.json()["capabilities"] == ["forecast:read"]
        accepted = c.post(
            "/api/v1/access/forecast-check", json=REQUEST, headers=bearer(tokens["local-viewer"])
        )
        assert accepted.status_code == 200
        assert accepted.json()["scope"] == REQUEST
        assert accepted.json()["principal_id"] == "local-viewer"
        assert "allowed" in accepted.json() and accepted.json()["allowed"] is True
        problem(
            c.get(
                "/api/v1/admin/access-policy",
                headers={
                    **bearer(tokens["local-viewer"]),
                    "X-Role": "admin",
                    "X-User-ID": "local-admin",
                },
            ),
            403,
        )


def test_admin_capability_is_explicit_and_does_not_inherit_viewer(tmp_path):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        admin = bearer(tokens["local-admin"])
        response = c.get("/api/v1/admin/access-policy", headers=admin)
        assert response.status_code == 200
        assert response.json() == {
            "schema_version": "1.0",
            "policy_id": "local-example-v1",
            "principal_count": 3,
            "credential_count": 3,
        }
        for credential in tokens.values():
            assert (
                credential not in response.text
                and token_fingerprint(credential) not in response.text
            )
        assert "local-viewer" not in response.text
        problem(c.post("/api/v1/access/forecast-check", json=REQUEST, headers=admin), 403)


@pytest.mark.parametrize(
    "changes",
    [
        {"product_ids": ["p-202"]},
        {"product_ids": ["p-101", "p-202"]},
        {"selling_location_ids": ["s-04"]},
        {"selling_location_ids": ["s-03", "s-04"]},
        {"channel": "online"},
    ],
)
def test_whole_requested_scope_is_denied_without_silent_filtering(tmp_path, changes):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        problem(
            c.post(
                "/api/v1/access/forecast-check",
                json={**REQUEST, **changes},
                headers=bearer(tokens["local-viewer"]),
            ),
            403,
        )
        operator_request = {
            **REQUEST,
            "product_ids": ["p-202"],
            "selling_location_ids": ["s-04"],
            "channel": "online",
        }
        assert (
            c.post(
                "/api/v1/access/forecast-check",
                json=operator_request,
                headers=bearer(tokens["local-operator"]),
            ).status_code
            == 200
        )
        problem(
            c.post(
                "/api/v1/access/forecast-check",
                json=REQUEST,
                headers=bearer(tokens["local-operator"]),
            ),
            403,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"principal_id": "local-admin"},
        {"requested_by": PRIVATE},
        {"role": "admin"},
        {"schema_version": "2.0"},
        {"product_ids": ["*"]},
        {"product_ids": []},
        {"product_ids": ["p-101"] * 2},
        {"product_ids": ["p-101"] * 21},
        {"selling_location_ids": ["s-03"] * 6},
        {"channel": "all"},
        {"product_ids": [101]},
    ],
)
def test_client_claims_and_unbounded_or_invalid_request_are_rejected(tmp_path, changes):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        problem(
            c.post(
                "/api/v1/access/forecast-check",
                json={**REQUEST, **changes},
                headers=bearer(tokens["local-viewer"]),
            ),
            422,
        )


@pytest.mark.parametrize(
    "authorization",
    [
        "",
        "Basic no-login",
        "Bearer short",
        "Bearer " + "z" * 43,
        "Bearer " + "z" * 129,
        "Bearer  " + "z" * 43,
        "Bearer " + "z" * 43 + " ",
    ],
)
def test_invalid_bearer_never_creates_identity(tmp_path, authorization):
    path, _ = policy_file(tmp_path)
    with client(path) as c:
        problem(c.get("/api/v1/identity", headers={"Authorization": authorization}), 401)


def test_duplicate_headers_cookies_query_and_forwarded_identity_are_not_credentials(tmp_path):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        value = f"Bearer {tokens['local-viewer']}"
        problem(
            c.get("/api/v1/identity", headers=[("Authorization", value), ("Authorization", value)]),
            401,
        )
        problem(
            c.get(
                "/api/v1/identity",
                params={"access_token": tokens["local-admin"]},
                headers={"X-Forwarded-User": "local-admin", "Cookie": f"Authorization={value}"},
            ),
            401,
        )
        assert (
            c.get(
                "/api/v1/identity", headers={"Authorization": value.replace("Bearer", "bearer")}
            ).status_code
            == 200
        )


def test_metrics_credentials_are_independent_in_both_directions(tmp_path):
    path, tokens = policy_file(tmp_path)
    metrics = secrets.token_urlsafe(32)
    with client(path, METRICS_TOKEN=metrics) as c:
        problem(c.get("/api/v1/identity", headers=bearer(metrics)), 401)
        problem(c.get("/metrics", headers=bearer(tokens["local-viewer"])), 401)
        assert c.get("/metrics", headers=bearer(metrics)).status_code == 200
    with pytest.raises(ValueError, match="access_policy_unavailable"):
        client(path, METRICS_TOKEN=tokens["local-viewer"])


@pytest.mark.parametrize("state", ["expired", "future", "revoked"])
def test_inactive_credentials_are_denied_by_real_file_adapter(tmp_path, state):
    def change(value):
        record = value["credentials"][0]
        now = datetime.now(UTC)
        if state == "expired":
            record.update(
                not_before=(now - timedelta(hours=2)).isoformat(),
                expires_at=(now - timedelta(hours=1)).isoformat(),
            )
        elif state == "future":
            record.update(
                not_before=(now + timedelta(hours=1)).isoformat(),
                expires_at=(now + timedelta(hours=2)).isoformat(),
            )
        else:
            record["revoked"] = True

    path, tokens = policy_file(tmp_path, change)
    with client(path) as c:
        problem(c.get("/api/v1/identity", headers=bearer(tokens["local-viewer"])), 401)


def test_exact_expiry_boundary_and_policy_reload_are_explicit(tmp_path):
    path, tokens = policy_file(tmp_path)
    policy = load_private_policy(path)
    credential = policy.credentials[0]
    authority = LocalAccess(policy)
    header = f"Bearer {tokens['local-viewer']}"
    assert authority.authenticate(header, now=credential.not_before).principal_id == "local-viewer"
    assert (
        authority.authenticate(header, now=credential.expires_at - timedelta(microseconds=1))
        is not None
    )
    assert authority.authenticate(header, now=credential.expires_at) is None
    value = json.loads(path.read_text())
    value["credentials"][0]["revoked"] = True
    path.write_text(json.dumps(value))
    assert (
        authority.authenticate(header) is not None
    )  # Existing process holds its startup snapshot.
    assert LocalAccess(load_private_policy(path)).authenticate(header) is None


@pytest.mark.parametrize(
    "kind", ["permissions", "symlink", "directory", "fifo", "duplicate", "malformed", "oversized"]
)
def test_private_file_fails_closed_with_safe_error(tmp_path, kind):
    path, _ = policy_file(tmp_path)
    if kind == "permissions":
        path.chmod(0o644)
    elif kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif kind == "directory":
        path = tmp_path
    elif kind == "fifo":
        path = tmp_path / "fifo"
        os.mkfifo(path, 0o600)
    elif kind == "duplicate":
        path.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    elif kind == "malformed":
        path.write_text(PRIVATE)
    else:
        path.write_bytes(b"x" * 131073)
    with pytest.raises(ValueError, match="^access_policy_unavailable$"):
        load_private_policy(path)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "capability",
        "scope",
        "version",
        "lifetime",
        "principal",
        "fingerprint",
        "plaintext",
    ],
)
def test_policy_contract_rejects_unsafe_or_ambiguous_grants(tmp_path, mutation):
    path, _ = policy_file(tmp_path)
    value = json.loads(path.read_text())
    if mutation == "owner":
        value["grants"].append(deepcopy(value["grants"][0]))
    elif mutation == "capability":
        value["grants"][0]["capabilities"].append("access:admin")
    elif mutation == "scope":
        value["grants"][0]["scope"]["product_ids"] = ["*"]
    elif mutation == "version":
        value["schema_version"] = "1.1"
    elif mutation == "lifetime":
        value["credentials"][0]["expires_at"] = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    elif mutation == "principal":
        value["credentials"][0]["principal_id"] = "unknown"
    elif mutation == "fingerprint":
        value["credentials"].append(deepcopy(value["credentials"][0]))
    else:
        value["credentials"][0]["bearer_token"] = PRIVATE
    with pytest.raises(ValidationError):
        AccessPolicy.model_validate_json(json.dumps(value))


def test_foreign_file_owner_is_rejected(tmp_path, monkeypatch):
    path, _ = policy_file(tmp_path)
    monkeypatch.setattr("retailops_ai.security.local.os.geteuid", lambda: 999999)
    with pytest.raises(ValueError, match="access_policy_unavailable"):
        load_private_policy(path)


def test_offline_provisioning_creates_unique_private_files_without_replacing_existing_credentials(
    tmp_path, capsys
):
    output = tmp_path / "new"
    assert (
        main(
            [
                "access-init",
                "--grants-file",
                str(CONTRACTS / "grant-template.v1.example.json"),
                "--output-dir",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {"status": "initialized"}
    policy_path = output / "api-access-policy.json"
    client_path = output / "api-client-credentials.json"
    assert output.stat().st_mode & 0o777 == 0o700
    assert policy_path.stat().st_mode & 0o777 == 0o600
    assert client_path.stat().st_mode & 0o777 == 0o600
    clients = json.loads(client_path.read_text())["credentials"]
    assert len({entry["bearer_token"] for entry in clients}) == 2
    assert all(entry["bearer_token"] not in policy_path.read_text() for entry in clients)
    authority = LocalAccess(load_private_policy(policy_path))
    for entry in clients:
        assert (
            authority.authenticate(f"Bearer {entry['bearer_token']}").principal_id
            == entry["principal_id"]
        )
    previous = client_path.read_bytes()
    assert (
        main(
            [
                "access-init",
                "--grants-file",
                str(CONTRACTS / "grant-template.v1.example.json"),
                "--output-dir",
                str(output),
            ]
        )
        == 2
    )
    assert client_path.read_bytes() == previous
    assert json.loads(capsys.readouterr().err) == {"error": "access_initialization_failed"}


@pytest.mark.parametrize("ttl", [0, 25])
def test_provisioning_lifetime_budget_does_not_leave_files(tmp_path, ttl):
    with pytest.raises(ValueError):
        provision(CONTRACTS / "grant-template.v1.example.json", tmp_path / "new", ttl)
    assert not (tmp_path / "new").exists()


def test_cli_bad_policy_is_sanitized_in_config_check_and_serve(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ARTIFACT_ROOT", "./artifacts")
    monkeypatch.setenv("API_AUTH_FILE", str(tmp_path / PRIVATE))
    for command in ("config-check", "serve"):
        assert main([command]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert json.loads(captured.err) == {"error": "access_policy_unavailable"}
    configured = Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts")
    assert PRIVATE not in repr(configured) and PRIVATE not in configured.model_dump_json()


def test_application_body_budget_and_duplicate_json_are_enforced(tmp_path):
    path, tokens = policy_file(tmp_path)
    with client(path) as c:
        for raw, status in (
            (b"x" * (MAX_ACCESS_BODY + 1), 413),
            (b'{"schema_version":"1.0","schema_version":"1.0"}', 422),
            (b'{"value":NaN}', 422),
        ):
            problem(
                c.post(
                    "/api/v1/access/forecast-check",
                    content=raw,
                    headers={**bearer(tokens["local-viewer"]), "Content-Type": "application/json"},
                ),
                status,
            )


def test_streamed_body_budget_and_deadline_do_not_trust_content_length(monkeypatch):
    async def chunks():
        return {"type": "http.request", "body": b"x" * MAX_ACCESS_BODY, "more_body": True}

    assert asyncio.run(access_body(chunks))[1] == 413
    monkeypatch.setattr("retailops_ai.api.middleware.ACCESS_BODY_TIMEOUT", 0.01)

    async def slow():
        await asyncio.sleep(10)

    assert asyncio.run(access_body(slow))[1] == 408


def test_credentials_payloads_paths_and_foreign_claims_do_not_enter_logs_or_responses(
    tmp_path, caplog
):
    path, tokens = policy_file(tmp_path)
    metrics = secrets.token_urlsafe(32)
    caplog.set_level(logging.INFO, logger="retailops_ai.http")
    with client(path, METRICS_TOKEN=metrics) as c:
        problem(
            c.post(
                "/api/v1/access/forecast-check",
                json={**REQUEST, "principal_id": PRIVATE},
                headers=bearer(tokens["local-viewer"]),
            ),
            422,
        )
        problem(
            c.get(
                "/api/v1/admin/access-policy",
                headers=bearer(tokens["local-viewer"]),
                params={"secret": PRIVATE},
            ),
            403,
        )
        rendered = c.get("/metrics", headers=bearer(metrics)).text
    lines = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for value in [
        *tokens.values(),
        *(token_fingerprint(t) for t in tokens.values()),
        PRIVATE,
        str(path),
    ]:
        assert value not in lines and value not in rendered
    assert 'route="/api/v1/access/forecast-check"' in rendered


def test_versioned_access_snapshots_and_openapi_security_are_reviewed():
    app = create_app(Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts"))
    snapshot = json.loads((CONTRACTS / "access.openapi.json").read_text())
    assert contract_openapi(app.openapi(), access=True) == snapshot
    assert len(snapshot["paths"]) == 14
    assert "/api/v1/forecasts" in snapshot["paths"]
    assert "/api/v1/models" in snapshot["paths"]
    assert "/api/v1/models/{model_name}/versions" in snapshot["paths"]
    for path in snapshot["paths"].values():
        for operation in path.values():
            assert operation["security"] == [{"apiBearer": []}]
            assert {"401", "403"} <= operation["responses"].keys()
    for path in CONTRACTS.glob("*.v1.example.json"):
        value = json.loads(path.read_text())
        schema = json.loads(path.with_name(path.name.replace(".example.", ".schema.")).read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    assert GrantTemplate.model_validate_json(
        (CONTRACTS / "grant-template.v1.example.json").read_bytes()
    )
    assert not list(CONTRACTS.glob("*credentials*"))


def test_generated_private_filenames_are_ignored_by_git():
    result = subprocess.run(
        [shutil.which("git"), "check-ignore", "--stdin"],
        input="api-access-policy.json\napi-client-credentials.json\n",
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) == 2


def test_snapshot_gate_catches_removed_security_and_does_not_rewrite_it(tmp_path):
    command = [
        sys.executable,
        str(ROOT / "scripts/update_access_contracts.py"),
        "--output",
        str(tmp_path),
    ]
    assert subprocess.run(command, capture_output=True, check=False).returncode == 0
    path = tmp_path / "access.openapi.json"
    snapshot = json.loads(path.read_text())
    snapshot["paths"]["/api/v1/admin/access-policy"]["get"].pop("security")
    path.write_text(json.dumps(snapshot))
    original = path.read_bytes()
    result = subprocess.run([*command, "--check"], capture_output=True, check=False)
    assert result.returncode == 1
    assert path.read_bytes() == original
