"""Only bounded timings, never private command output, may enter CI artifacts."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_anomaly_oci as runner  # noqa: E402


def test_private_output_stays_in_private_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    private_log = tmp_path / "build.log"
    assert (
        runner.command([sys.executable, "-c", "print('private-fixture-value')"], log=private_log)
        == "private-fixture-value"
    )
    assert "private-fixture-value" in private_log.read_text()
    report = (tmp_path / "reports/ci-anomaly-timings.jsonl").read_text()
    assert "private-fixture-value" not in report + capsys.readouterr().out
    assert json.loads(report)["status"] == "passed"


def test_timeout_is_recorded_and_propagated(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-command", 3000)

    monkeypatch.setattr(runner.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.command(["private-command"], log=tmp_path / "build.log")
    report = json.loads((tmp_path / "reports/ci-anomaly-timings.jsonl").read_text())
    assert report["status"] == "failed"
    assert set(report) == {"phase", "elapsed_seconds", "status"}


def test_failed_image_build_cannot_return_success(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="child_command_failed"):
        runner.command([sys.executable, "-c", "raise SystemExit(4)"], log=tmp_path / "build.log")
    assert (
        json.loads((tmp_path / "reports/ci-anomaly-timings.jsonl").read_text())["status"]
        == "failed"
    )
