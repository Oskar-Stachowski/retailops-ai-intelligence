"""Local negative guards never start Docker or mutate an unrelated handoff receipt."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def subject():
    path = Path(__file__).resolve().parents[1] / "scripts/check_ai10_source_sql_handoff.py"
    spec = importlib.util.spec_from_file_location("ai10_source_handoff_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_run_refuses_before_tools_or_file_access(subject, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        subject.shutil, "which", lambda *_: pytest.fail("No local Docker/tool access")
    )
    with pytest.raises(ValueError, match="owned_hosted_runner_required"):
        subject.run(SimpleNamespace())


@pytest.mark.parametrize("kind", ["file", "dangling_symlink"])
def test_existing_unrelated_receipt_is_preserved_before_runtime(
    subject, monkeypatch, tmp_path, kind
):
    report = tmp_path / "receipt.json"
    if kind == "file":
        report.write_bytes(b"unchanged unrelated original receipt")
    else:
        report.symlink_to(tmp_path / "missing")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    pin = json.loads(
        (subject.ROOT / "docs/reference/ai10-source-capture-producer.json").read_bytes()
    )
    monkeypatch.setattr(subject.shutil, "which", lambda command: "/usr/bin/" + command)
    monkeypatch.setattr(
        subject,
        "command",
        lambda _, *args: "a" * 40 if str(subject.ROOT) in args else pin["commit"],
    )
    with pytest.raises(ValueError, match="create_only_report"):
        subject.run(SimpleNamespace(report=report, source_root=tmp_path))
    if kind == "file":
        assert report.read_bytes() == b"unchanged unrelated original receipt"
    else:
        assert report.is_symlink() and not report.exists()
