import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailops_ai.config import Settings, load_settings
from retailops_ai.contracts import ApplicationInfo

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args, env_file=None, extra_env=None):
    env = {
        k: v for k, v in os.environ.items() if k not in ("APP_ENV", "ARTIFACT_ROOT", "LOG_LEVEL")
    }
    env.update(extra_env or {})
    command = [sys.executable, "-m", "retailops_ai", *args]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    return subprocess.run(command, env=env, text=True, capture_output=True, check=False)


def test_help_and_version_work_without_configuration():
    assert run_cli("--help").returncode == 0
    result = run_cli("version")
    assert result.returncode == 0
    assert ApplicationInfo.model_validate_json(result.stdout).version == "0.1.0.dev0"


def test_missing_configuration_has_actionable_field_names():
    result = run_cli("config-check")
    assert result.returncode == 2
    assert json.loads(result.stderr) == {
        "error": "invalid_configuration",
        "fields": ["APP_ENV", "ARTIFACT_ROOT"],
    }
    assert not result.stdout


def test_settings_validation_does_not_create_artifacts(tmp_path):
    artifact_root = tmp_path / "not-created"
    result = run_cli(
        "config-check", extra_env={"APP_ENV": "test", "ARTIFACT_ROOT": str(artifact_root)}
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "valid", "app_env": "test"}
    assert not artifact_root.exists()


def test_invalid_configuration_never_prints_input_values(tmp_path):
    sentinel = "private-value-must-not-appear"
    env_file = tmp_path / ".env"
    env_file.write_text(f"APP_ENV={sentinel}\nARTIFACT_ROOT=./artifacts\n")
    result = run_cli("config-check", env_file=env_file)
    assert result.returncode == 2
    assert sentinel not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert json.loads(result.stderr)["fields"] == ["APP_ENV"]


def test_unknown_dotenv_key_is_rejected_without_its_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=local\nARTIFACT_ROOT=./artifacts\nUNKNOWN=private-value\n")
    result = run_cli("config-check", env_file=env_file)
    assert result.returncode == 2
    assert "private-value" not in result.stdout + result.stderr


def test_missing_explicit_dotenv_is_an_error(tmp_path):
    result = run_cli("config-check", env_file=tmp_path / "absent")
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "configuration_file_unavailable"


def test_environment_overrides_explicit_dotenv(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=local\nARTIFACT_ROOT=./artifacts\n")
    result = run_cli("config-check", env_file=env_file, extra_env={"APP_ENV": "test"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["app_env"] == "test"


@pytest.mark.parametrize("value", ["", " ", "\x00"])
def test_empty_or_invalid_artifact_root_is_rejected(value):
    with pytest.raises(ValidationError):
        Settings(APP_ENV="local", ARTIFACT_ROOT=value)


def test_model_contract_and_schema_match_reviewed_files():
    example = (ROOT / "contracts/application-info.v1.example.json").read_text()
    assert ApplicationInfo.model_validate_json(example).service == "retailops-ai-intelligence"
    assert ApplicationInfo.model_json_schema() == json.loads(
        (ROOT / "contracts/application-info.v1.schema.json").read_text()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": "999"},
        {"implementation_status": "production_ready"},
        {"service": "retailops"},
        {"version": ""},
        {"extra": "unversioned"},
    ],
)
def test_incompatible_metadata_contract_is_rejected(changes):
    payload = json.loads((ROOT / "contracts/application-info.v1.example.json").read_text())
    with pytest.raises(ValidationError):
        ApplicationInfo.model_validate({**payload, **changes})


def test_dotenv_example_is_valid(monkeypatch):
    for key in ("APP_ENV", "ARTIFACT_ROOT", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    assert load_settings(ROOT / ".env.example").app_env == "local"
