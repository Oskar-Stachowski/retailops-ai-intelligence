"""Complete frozen inventory, URL confinement, byte identity and local disk reserve."""

import copy
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import recover_ai10_v12_archive as recovery  # noqa: E402


@pytest.fixture
def pin():
    return json.loads(recovery.PIN.read_bytes())


def temporary_url(pin, key):
    return (
        "https://"
        + pin["bucket"]
        + ".s3.eu-central-1.amazonaws.com/"
        + key
        + "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature="
        + "a" * 64
    )


def test_whole_original_archive_is_required_before_recovery(pin):
    recovery.validate_pin(pin)
    changed = copy.deepcopy(pin)
    changed["files"].pop(next(iter(changed["files"])))
    with pytest.raises(recovery.RecoveryError, match="complete_original_inventory"):
        recovery.validate_pin(changed)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "archive/a/../b", "a//b"])
def test_unconfined_output_paths_are_rejected(path):
    with pytest.raises(recovery.RecoveryError, match="relative_path"):
        recovery.relative(path)


@pytest.mark.parametrize("change", ["http", "host", "path", "port"])
def test_signed_urls_cannot_delegate_to_another_destination(pin, change):
    url = temporary_url(pin, "original.json")
    if change == "http":
        url = url.replace("https:", "http:")
    elif change == "host":
        url = url.replace(pin["bucket"], "foreign-bucket")
    elif change == "port":
        url = url.replace(".com/", ".com:443/")
    else:
        url = url.replace("original.json", "foreign.json")
    with pytest.raises(recovery.RecoveryError, match="scoped_read_url"):
        recovery.signed_url(url, pin, key="original.json")


def test_duplicate_or_nonfinite_private_handoff_json_is_rejected():
    for raw in (b'{"files":{},"files":{}}', b'{"value":NaN}'):
        with pytest.raises(recovery.RecoveryError):
            recovery.document(raw)


def test_disk_reserve_rejection_happens_before_any_output_or_download(pin, tmp_path, monkeypatch):
    monkeypatch.setattr(
        recovery.shutil, "disk_usage", lambda _path: SimpleNamespace(free=79 * 1024**3)
    )
    monkeypatch.setattr(recovery, "download", lambda *_args: pytest.fail("No download authorized"))
    output = tmp_path / "original"
    with pytest.raises(recovery.RecoveryError, match="disk_reserve_would_be_breached"):
        recovery.recover(pin, dict.fromkeys(pin["files"], "unopened"), output, 50)
    assert not output.exists()


def test_local_runner_cannot_silently_use_remote_disk_reserve(pin, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(recovery.RecoveryError, match="lower_reserve_requires_disposable"):
        recovery.recover(pin, dict.fromkeys(pin["files"], "unopened"), tmp_path / "original", 6)


def test_existing_original_evidence_is_never_replaced(pin, tmp_path, monkeypatch):
    monkeypatch.setattr(
        recovery.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100 * 1024**3)
    )
    output = tmp_path / "original"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("retain")
    with pytest.raises(FileExistsError):
        recovery.recover(pin, dict.fromkeys(pin["files"], "unopened"), output, 50)
    assert sentinel.read_text() == "retain"


@pytest.mark.parametrize("body", [b"short", b"original!", b"different"])
def test_changed_bytes_are_not_published(tmp_path, monkeypatch, body):
    output = tmp_path / "original"
    (output / ".partial").mkdir(parents=True)
    monkeypatch.setattr(
        recovery, "opener", lambda: SimpleNamespace(open=lambda *_a, **_k: io.BytesIO(body))
    )
    with pytest.raises(recovery.RecoveryError):
        recovery.download(
            "archive/original.json",
            {"size_bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest()},
            "unopened",
            output,
        )
    assert not (output / "archive/original.json").exists()


def test_matching_original_bytes_are_private_and_unchanged(tmp_path, monkeypatch):
    output = tmp_path / "original"
    (output / ".partial").mkdir(parents=True)
    raw = b"original"
    monkeypatch.setattr(
        recovery, "opener", lambda: SimpleNamespace(open=lambda *_a, **_k: io.BytesIO(raw))
    )
    recovery.download(
        "archive/original.json",
        {"size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "unopened",
        output,
    )
    path = output / "archive/original.json"
    assert path.read_bytes() == raw
    assert path.stat().st_mode & 0o777 == 0o600


def test_nested_original_run_root_is_accepted_by_actual_scratch_transport(tmp_path, monkeypatch):
    from retailops_ai.model_lifecycle import v12_scratch_tracking as transport
    from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt

    output = tmp_path / "original"
    output.mkdir(mode=0o700)
    (output / ".partial").mkdir(mode=0o700)
    raw = b"small original byte fixture; no model or archive qualification"
    monkeypatch.setattr(
        recovery, "opener", lambda: SimpleNamespace(open=lambda *_a, **_k: io.BytesIO(raw))
    )
    name = "archive/original-run/campaign/checkpoints/payload"
    receipt = V12ArtifactReceipt(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    recovery.download(name, receipt.model_dump(), "unopened", output)
    for directory in (output / name).parents:
        if directory == output:
            break
        assert directory.stat().st_uid == os.geteuid()
        assert directory.stat().st_mode & 0o777 == 0o700
    source = output / "archive/original-run"
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    monkeypatch.setattr(transport.sys, "platform", "linux")
    for key, value in (
        ("GITHUB_ACTIONS", "true"),
        ("RUNNER_ENVIRONMENT", "github-hosted"),
        ("RUNNER_OS", "Linux"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(transport.shutil, "disk_usage", lambda _: SimpleNamespace(free=7 * 1024**3))
    relative = "campaign/checkpoints/payload"
    route = transport.PREFIX + "1/" + "a" * 32 + "/artifacts/" + relative
    transport.ScratchTracking(store, source, 5010).upload(route, source, relative, receipt)
    target = store / route.removeprefix(transport.PREFIX)
    assert target.read_bytes() == raw
    assert target.stat().st_ino == (source / relative).stat().st_ino


@pytest.mark.parametrize("unsafe_parent", ["symlink", "public"])
def test_unsafe_archive_parent_is_refused_before_network_or_publish(
    tmp_path, monkeypatch, unsafe_parent
):
    output = tmp_path / "original"
    output.mkdir(mode=0o700)
    (output / ".partial").mkdir(mode=0o700)
    parent = output / "archive"
    if unsafe_parent == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir(mode=0o700)
        parent.symlink_to(outside, target_is_directory=True)
    else:
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
    monkeypatch.setattr(recovery, "opener", lambda: pytest.fail("unsafe parent must not download"))
    with pytest.raises(recovery.RecoveryError, match="private_owned_directory"):
        recovery.download(
            "archive/run/payload", {"size_bytes": 0, "sha256": "a" * 64}, "unopened", output
        )
    assert not (parent / "run").exists()
    assert not list((output / ".partial").iterdir())


def test_missing_signed_read_is_not_replaced_by_fixture(pin, tmp_path):
    with pytest.raises(recovery.RecoveryError, match="incomplete_read_map"):
        recovery.recover(pin, {}, tmp_path / "original", 50)
