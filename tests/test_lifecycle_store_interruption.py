"""Keep the real child kill and fence gates; disclose only fixed diagnostic codes."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_lifecycle_store as smoke  # noqa: E402
import lifecycle_store as combined  # noqa: E402
import mlflow_store as store  # noqa: E402

PROJECT = "retailops_ai_store_source_1234567890"


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(combined, "LOCK", tmp_path / "controller.lock")
    monkeypatch.setattr(combined, "MAINTENANCE", tmp_path / "maintenance.json")
    monkeypatch.setattr(smoke.stack, "LOCAL", tmp_path / "compose.env")
    smoke.stack.environment_file(create=True)
    monkeypatch.setattr(
        store.shutil, "which", lambda name: "/unit-test/docker" if name == "docker" else None
    )


def test_acceptance_overlay_survives_fresh_child_and_is_owned_only(
    isolated: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = store.compose
    inventory = combined.database_inventory
    commands: list[list[str]] = []

    def backup(project: str) -> Path:
        commands.append(store.compose(project, "stop", "api", "mlflow"))
        raise ValueError("lifecycle_fence_sessions_remain")

    monkeypatch.setattr(combined, "backup", backup)
    parent = smoke.acceptance_compose(original, (PROJECT,))
    assert smoke._backup_child(PROJECT) == 2
    assert commands == [parent(PROJECT, "stop", "api", "mlflow")]
    assert parent("retailops_ai_persistent", "ps") == original("retailops_ai_persistent", "ps")
    assert store.compose is original
    assert combined.database_inventory is inventory
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["reason"] == "lifecycle_fence_sessions_remain"
    assert diagnostic["frames"] == [
        {"function": "_backup_child", "line": diagnostic["frames"][0]["line"]}
    ]


def test_child_failure_redacts_command_and_exception_text_and_restores_hooks(
    isolated: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = store.compose
    inventory = combined.database_inventory

    def backup(_project: str) -> Path:
        raise ValueError("private-target-value https://private.invalid/grant")

    monkeypatch.setattr(combined, "backup", backup)
    assert smoke._backup_child(PROJECT) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "private" not in captured.err
    assert "grant" not in captured.err
    diagnostic = json.loads(captured.err)
    assert diagnostic["reason"] == "details_redacted"
    assert diagnostic["exception_type"] == "ValueError"
    assert store.compose is original
    assert combined.database_inventory is inventory


def test_actual_owned_child_is_sigkilled_after_maintenance_before_resume(tmp_path: Path) -> None:
    # The actual backup controller and OS signal run in our new process. Only the
    # database operations are fake; this is not Docker/PostgreSQL acceptance.
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import check_lifecycle_store as smoke
import lifecycle_store as combined
root = Path(sys.argv[2])
smoke.stack.LOCAL = root / 'compose.env'
smoke.stack.environment_file(create=True)
combined.LOCK = root / 'controller.lock'
combined.MAINTENANCE = root / 'maintenance.json'
combined.BACKUPS = root / 'backups'
combined.limits = lambda project: {'system_identifier': '123', 'limits': dict.fromkeys(combined.DATABASES, -1)}
combined.running_services = lambda project: []
def fence(project, previous):
    (root / 'fenced').write_text(project)
combined.fence = fence
def resume():
    (root / 'resumed').write_text('unexpected')
combined.resume_maintenance = resume
sys.exit(smoke._backup_child(sys.argv[3]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "scripts"), str(tmp_path), PROJECT],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == -signal.SIGKILL
    assert not result.stdout and not result.stderr
    assert (tmp_path / "fenced").read_text() == PROJECT
    assert json.loads((tmp_path / "maintenance.json").read_text())["project"] == PROJECT
    assert not (tmp_path / "resumed").exists()


@pytest.mark.parametrize(
    "bad_field,value",
    [
        ("reason", []),
        ("reason", {"secret": "private"}),
        ("reason", "private"),
        ("exception_type", []),
        ("frames", [{"function": "private", "line": 10}]),
        ("frames", [{"function": "backup", "line": True}]),
        ("frames", [{"function": "backup", "line": 10000}]),
        ("frames", [{"function": "backup", "line": 10, "secret": "private"}]),
        ("frames", [{"function": "backup", "line": 10}] * 9),
    ],
)
def test_untrusted_child_diagnostic_is_ignored(bad_field: str, value: Any) -> None:
    diagnostic = {
        "error": "interrupted_backup_child_failed",
        "reason": "details_redacted",
        "exception_type": "ValueError",
        "frames": [{"function": "backup", "line": 10}],
    }
    diagnostic[bad_field] = value
    error = smoke.InterruptedBackupError(2, False, json.dumps(diagnostic).encode())
    assert error.diagnostic == {"returncode": 2, "maintenance_present": False}


def test_bounded_diagnostic_accepts_only_fixed_values_and_never_raw_output() -> None:
    safe = {
        "error": "interrupted_backup_child_failed",
        "reason": "lifecycle_fence_sessions_remain",
        "exception_type": "ValueError",
        "frames": [{"function": "fence", "line": 193}],
    }
    error = smoke.InterruptedBackupError(
        2, False, b"private grant\n" + b"noise" * 4096 + b"\n" + json.dumps(safe).encode()
    )
    assert error.diagnostic["child"] == safe
    assert "private" not in json.dumps(error.diagnostic)
    nested = b"[" * 2000 + b"]" * 2000
    assert "child" not in smoke.InterruptedBackupError(2, False, nested).diagnostic


@pytest.mark.parametrize("returncode,maintenance", [(0, True), (2, True), (-9, False)])
def test_successful_return_or_missing_maintenance_never_passes(
    isolated: None, monkeypatch: pytest.MonkeyPatch, returncode: int, maintenance: bool
) -> None:
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], returncode, b"", b"private"),
    )
    if maintenance:
        combined.MAINTENANCE.write_text("{}")
    monkeypatch.setattr(combined, "limits", lambda _: pytest.fail("must fail before DB access"))
    with pytest.raises(smoke.InterruptedBackupError, match="backup_interruption_not_observed"):
        smoke.interrupted_backup(PROJECT)


@pytest.mark.parametrize(
    "bad_fence,accepted_connection", [(True, False), (False, True), (False, False)]
)
def test_both_fences_and_rejected_app_connections_are_required_before_resume(
    isolated: None,
    monkeypatch: pytest.MonkeyPatch,
    bad_fence: bool,
    accepted_connection: bool,
) -> None:
    combined.MAINTENANCE.write_text("{}")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], -signal.SIGKILL, b"", b""),
    )
    limits = dict.fromkeys(combined.DATABASES, 0)
    if bad_fence:
        limits["retailops_mlflow"] = -1
    monkeypatch.setattr(combined, "limits", lambda _: {"limits": limits})
    commands: list[list[str]] = []
    resumed: list[bool] = []

    def checked(command: list[str]) -> bytes:
        commands.append(command)
        if accepted_connection:
            return b"1"
        raise ValueError("application_connection_rejected")

    monkeypatch.setattr(store, "checked_run", checked)
    monkeypatch.setattr(combined, "resume_maintenance", lambda: resumed.append(True))
    if bad_fence or accepted_connection:
        reason = (
            "backup_interruption_did_not_retain_fence"
            if bad_fence
            else "fenced_database_accepted_application_connection"
        )
        with pytest.raises(ValueError, match=reason):
            smoke.interrupted_backup(PROJECT)
        assert not resumed
    else:
        smoke.interrupted_backup(PROJECT)
        assert len(commands) == 2
        assert resumed == [True]
