"""Backup acceptance cannot delete foreign resources or reuse stale success."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_v12_backup as runner  # noqa: E402
import v12_backup_fixture_stack as fixture  # noqa: E402


def test_missing_cached_image_replaces_previous_success_without_cleanup(tmp_path, monkeypatch):
    report = tmp_path / "reports/result.json"
    report.parent.mkdir()
    report.write_text('{"status":"passed"}')
    monkeypatch.setattr(runner, "REPORT", report)

    def missing(*args):
        raise ValueError("explicit_fixture_missing_image")

    monkeypatch.setattr(runner, "docker", missing)
    assert runner.main("explicit_fixture_image") == 1
    result = json.loads(report.read_text())
    assert result["status"] == "failed" and result["failed_stage"] == "cached_images"
    assert result["owned_test_resources_removed"] is True


def control(tmp_path):
    tmp_path.chmod(0o700)
    owner = "a" * 32
    result = {
        "owner": owner,
        "projects": {
            role: "retailops_ai_v12_" + role + "_" + owner[:10]
            for role in ("source", "target", "failed")
        },
        "images": {role: "sha256:" + "b" * 64 for role in ("db", "mlflow")},
    }
    return result


def save_control(tmp_path, value):
    path = tmp_path / "control.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)


@pytest.mark.parametrize("bad", ["owner", "project", "extra_project", "image"])
def test_private_control_requires_uuid_projects_and_immutable_images(tmp_path, bad):
    value = control(tmp_path)
    if bad == "owner":
        value["owner"] = "not_a_uuid"
    elif bad == "project":
        value["projects"]["source"] = "retailops_ai_foreign_source"
    elif bad == "extra_project":
        value["projects"]["foreign"] = "retailops_ai_foreign_source"
    else:
        value["images"]["db"] = "latest"
    save_control(tmp_path, value)
    with pytest.raises(ValueError):
        fixture.FixtureStack(tmp_path)


def test_foreign_project_and_wrong_owner_never_reach_down(tmp_path, monkeypatch):
    value = control(tmp_path)
    save_control(tmp_path, value)
    monkeypatch.setattr(fixture.shutil, "which", lambda _: "/fixture/docker")
    instance = fixture.FixtureStack(tmp_path)
    mutations = []
    monkeypatch.setattr(fixture.store, "checked_run", lambda cmd: mutations.append(cmd))
    with pytest.raises(ValueError, match="foreign_cleanup"):
        instance.cleanup("retailops_ai_foreign_source")
    with pytest.raises(ValueError, match="foreign_project"):
        instance.compose("retailops_ai_foreign_source", "up")

    def docker(*args):
        if args[0] == "ps":
            return "foreign-container"
        if args[0] == "inspect":
            return json.dumps([{"Config": {"Labels": {fixture.OWNER_LABEL: "foreign"}}}])
        pytest.fail("No resource removal may follow an ownership mismatch")

    monkeypatch.setattr(fixture, "docker", docker)
    with pytest.raises(ValueError, match="cleanup_owner_mismatch"):
        instance.cleanup(instance.projects["source"])
    assert not mutations


@pytest.mark.parametrize("phase", ["restored", "restart"])
def test_state_and_native_receipts_do_not_overwrite_each_other(tmp_path, monkeypatch, phase):
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def state_run(command, **kwargs):
        receipt = next(arg for arg in command if arg.startswith("--junitxml="))
        Path(receipt.split("=", 1)[1]).write_text("explicit_state_test_receipt")
        kwargs["stdout"].write(b"explicit_state_test_log")
        return subprocess.CompletedProcess(command, 0)

    def native(invocation, **kwargs):
        assert kwargs["inspect"] is True
        (reports / "ai05-v12-metadata-restart-tests.xml").write_text("explicit_native_receipt")
        (tmp_path / "restart.log").write_bytes(b"explicit_native_log")

    monkeypatch.setattr(runner.subprocess, "run", state_run)
    monkeypatch.setattr(runner, "tests", native)
    invocation = tmp_path / "invocation.json"
    runner.backup_test(invocation, tmp_path, phase=phase)
    runner.native_tests(invocation, tmp_path, phase=phase)
    assert (reports / f"ai05-v12-backup-state-{phase}-tests.xml").read_text() == (
        "explicit_state_test_receipt"
    )
    assert (reports / f"ai05-v12-backup-native-{phase}-tests.xml").read_text() == (
        "explicit_native_receipt"
    )
    state_log = reports / f"ai05-v12-backup-state-{phase}.log"
    native_log = reports / f"ai05-v12-backup-native-{phase}.log"
    assert state_log.read_bytes() == b"explicit_state_test_log"
    assert native_log.read_bytes() == b"explicit_native_log"
    assert state_log.stat().st_mode & 0o777 == native_log.stat().st_mode & 0o777 == 0o600
    assert (reports / f"ai05-v12-backup-state-{phase}-tests.xml").stat().st_mode & 0o777 == 0o600
