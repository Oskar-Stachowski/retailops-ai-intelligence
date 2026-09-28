import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from retailops_ai.adapters.database import EXPECTED_REVISION, DatabaseProbe
from retailops_ai.api.app import create_app
from retailops_ai.cli import main
from retailops_ai.config import Settings
from retailops_ai.domain.readiness import Dependency

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "synthetic-db-redaction-sentinel"  # noqa: S105
URL = f"postgresql+psycopg://ai_app:{SENTINEL}@127.0.0.1:1/retailops_ai"


def settings(**kwargs):
    return Settings(APP_ENV="test", ARTIFACT_ROOT="./artifacts", **kwargs)


@pytest.mark.parametrize(
    "url",
    ["sqlite:///test.db", "not-a-url", "postgresql://u:p@db/ai", "postgresql+psycopg://u@db/ai"],
)
def test_rejects_unsupported_or_incomplete_database_url(url):
    with pytest.raises(ValidationError):
        settings(DATABASE_URL=url)


def test_database_secret_and_binding_boundary():
    configured = settings(DATABASE_URL=URL, NETWORK_MODE="compose", HTTP_HOST="0.0.0.0")  # noqa: S104 - binding boundary test
    assert SENTINEL not in repr(configured)
    assert SENTINEL not in configured.model_dump_json()
    with pytest.raises(ValidationError):
        settings(HTTP_HOST="0.0.0.0", DATABASE_URL=URL)  # noqa: S104 - binding boundary test
    with pytest.raises(ValidationError):
        settings(NETWORK_MODE="compose")


def test_actual_unreachable_database_is_required_and_health_is_independent():
    # Real psycopg adapter, closed local port; no provider fake.
    with TestClient(create_app(settings(DATABASE_URL=URL)), base_url="http://127.0.0.1") as c:
        assert c.get("/health").status_code == 200
        response = c.get("/ready")
        assert response.status_code == 503
        assert response.json()["readiness"]["role"] == "ai_api"
        assert response.json()["readiness"]["dependencies"][1]["name"] == "ai_db"
        assert SENTINEL not in response.text


@pytest.mark.parametrize(
    "revision,extension,expected",
    [
        (EXPECTED_REVISION, 1, True),
        ("old_schema", 1, False),
        (EXPECTED_REVISION, None, False),
    ],
)
def test_database_probe_checks_revision_extension_and_table(revision, extension, expected):
    # Adapter unit fake; actual PostgreSQL acceptance is scripts/verify_local_stack.py.
    connection = AsyncMock()
    connection.scalar.side_effect = [revision, extension]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock()
    engine = MagicMock()
    engine.connect.return_value = context
    assert asyncio.run(DatabaseProbe(engine).check()) is expected
    assert connection.execute.await_count == (1 if expected else 0)


def test_reserved_probe_cannot_be_overridden():
    async def ok():
        return True

    with pytest.raises(ValueError):
        create_app(settings(), dependencies=(Dependency("ai_db", ok),))


def test_compose_host_is_explicit_and_not_general_dns():
    for mode, expected in [("local", 400), ("compose", 200)]:
        kwargs = {"NETWORK_MODE": mode}
        if mode == "compose":
            kwargs["DATABASE_URL"] = URL
        with TestClient(create_app(settings(**kwargs)), base_url="http://127.0.0.1") as c:
            assert c.get("/health", headers={"Host": "api:8081"}).status_code == expected
            assert c.get("/health", headers={"Host": "api.attacker.invalid"}).status_code == 400


def test_migration_failure_is_sanitized(monkeypatch, capsys):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ARTIFACT_ROOT", "./artifacts")
    monkeypatch.setenv("DATABASE_URL", URL)
    assert main(["migrate"]) == 1
    output = capsys.readouterr()
    assert output.err.strip() == '{"error":"database_migration_failed"}'
    assert SENTINEL not in output.err + output.out


def load_controller():
    spec = importlib.util.spec_from_file_location("local_stack", ROOT / "scripts/local_stack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_credentials_are_unique_reused_and_validate_permissions(tmp_path):
    controller = load_controller()
    controller.LOCAL = tmp_path / ".local/compose.env"
    controller.environment_file(create=True)
    original = controller.LOCAL.read_bytes()
    assert controller.LOCAL.stat().st_mode & 0o777 == 0o600
    controller.environment_file(create=True)
    assert controller.LOCAL.read_bytes() == original
    controller.LOCAL.chmod(0o644)
    with pytest.raises(ValueError):
        controller.environment_file(create=False)


def test_compose_no_operational_database_broker_or_public_ports():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    assert set(config["services"]) == {"db", "api", "mlflow", "api-migrate", "mlflow-migrate"}
    assert "ports" not in config["services"]["db"]
    assert config["networks"]["ai_backend"]["internal"] is True
    for name in ["api", "mlflow"]:
        assert all(p.startswith("127.0.0.1:") for p in config["services"][name]["ports"])
    for name in ["api-migrate", "mlflow-migrate"]:
        assert config["services"][name]["profiles"] == ["maintenance"]
        assert config["services"][name]["restart"] == "no"
    assert "MLFLOW_DB_PASSWORD" not in config["services"]["mlflow-migrate"]["command"][0]
    assert config["services"]["mlflow"]["environment"]["MLFLOW_BACKEND_STORE_URI"].startswith(
        "postgresql+psycopg2://mlflow_app@"
    )
