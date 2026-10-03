"""A failed disposable acceptance must never preserve an old passing report."""

import importlib.util
import json
import subprocess
from pathlib import Path


def runner():
    path = Path(__file__).resolve().parents[1] / "scripts/check_v12_lifecycle.py"
    spec = importlib.util.spec_from_file_location("v12_acceptance_runner_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_early_runner_failure_replaces_previous_success(tmp_path, monkeypatch):
    module = runner()
    report = tmp_path / "reports/ai05-v12-publication-acceptance.json"
    report.parent.mkdir()
    report.write_text('{"status":"passed"}')
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def docker(*arguments, **kwargs):
        if arguments[0] == "image":
            raise ValueError("explicit_fixture_cached_image_missing")
        return ""

    monkeypatch.setattr(module, "docker", docker)
    assert module.main(include_outputs=True) == 1
    result = json.loads(report.read_text())
    assert result["status"] == "failed" and result["failed_stage"] == "cached_images"
    assert result["owned_containers_and_anonymous_volumes_removed"] is True


def test_timeout_preserves_private_logs_and_keeps_separate_test_deadline(tmp_path, monkeypatch):
    module = runner()
    (tmp_path / "reports").mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    def timeout(command, **kwargs):
        assert kwargs["timeout"] == 600
        kwargs["stdout"].write(b"explicit_fixture_timeout_diagnostic")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", timeout)
    import pytest

    with pytest.raises(subprocess.TimeoutExpired):
        module.tests(
            tmp_path / "private-invocation.json",
            inspect=False,
            work=tmp_path,
            include_queue=True,
            include_outputs=True,
        )
    log = tmp_path / "reports/ai05-v12-publication-acceptance.log"
    assert log.read_bytes() == b"explicit_fixture_timeout_diagnostic"
    assert log.stat().st_mode & 0o777 == 0o600
