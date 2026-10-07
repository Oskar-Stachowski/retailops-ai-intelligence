"""Actual small hardlinks validate disposable scratch guards; no archive/backup claim."""

import errno
import hashlib
import os
from types import SimpleNamespace

import pytest

from retailops_ai.model_lifecycle import v12_scratch_tracking as transport
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt

ROUTE = "/api/2.0/mlflow-artifacts/artifacts/1/" + "a" * 32 + "/artifacts/campaign/payload"


@pytest.fixture
def assets(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(mode=0o700)
    store.mkdir(mode=0o700)
    (source / "campaign").mkdir(mode=0o700)
    data = b"owned disposable recovered bytes"
    path = source / "campaign/payload"
    path.write_bytes(data)
    path.chmod(0o600)
    monkeypatch.setattr(transport.sys, "platform", "linux")
    for key, value in (
        ("GITHUB_ACTIONS", "true"),
        ("RUNNER_ENVIRONMENT", "github-hosted"),
        ("RUNNER_OS", "Linux"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(transport.shutil, "disk_usage", lambda _: SimpleNamespace(free=7 * 1024**3))
    receipt = V12ArtifactReceipt(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return source, store, receipt


def test_shared_scratch_bytes_and_create_only_destination(assets):
    source, store, receipt = assets
    client = transport.ScratchTracking(store, source, 5010)
    client.upload(ROUTE, source, "campaign/payload", receipt)
    target = store / ROUTE.removeprefix(transport.PREFIX)
    assert target.stat().st_ino == (source / "campaign/payload").stat().st_ino
    with pytest.raises(FileExistsError):
        client.upload(ROUTE, source, "campaign/payload", receipt)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == receipt.sha256


@pytest.mark.parametrize("key", ["GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "RUNNER_OS"])
def test_local_or_self_hosted_use_is_refused(assets, monkeypatch, key):
    source, store, _ = assets
    monkeypatch.delenv(key)
    with pytest.raises(ValueError, match="hosted_linux_only"):
        transport.ScratchTracking(store, source, 5010)
    assert not list(store.iterdir())


@pytest.mark.parametrize(
    "route", ["/wrong", ROUTE + "/../escape", ROUTE.replace("/campaign/", "/different/")]
)
def test_route_or_source_name_cannot_escape_or_alias(assets, route):
    source, store, receipt = assets
    with pytest.raises(ValueError):
        transport.ScratchTracking(store, source, 5010).upload(
            route, source, "campaign/payload", receipt
        )
    assert not list(store.iterdir())


def test_mutation_symlink_and_another_source_are_refused(assets, tmp_path):
    source, store, receipt = assets
    client = transport.ScratchTracking(store, source, 5010)
    other = tmp_path / "another"
    other.mkdir()
    with pytest.raises(ValueError, match="exact_source"):
        client.upload(ROUTE, other, "campaign/payload", receipt)
    (source / "campaign/payload").write_bytes(b"changed")
    with pytest.raises(ValueError, match="source_changed"):
        client.upload(ROUTE, source, "campaign/payload", receipt)
    (source / "campaign/payload").unlink()
    (source / "campaign/payload").symlink_to(other / "payload")
    with pytest.raises((ValueError, OSError)):
        client.upload(ROUTE, source, "campaign/payload", receipt)
    assert not list(store.iterdir())


def test_no_space_or_cross_device_link_never_falls_back_to_copy(assets, monkeypatch):
    source, store, receipt = assets
    client = transport.ScratchTracking(store, source, 5010)
    monkeypatch.setattr(transport.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="free_space"):
        client.upload(ROUTE, source, "campaign/payload", receipt)
    monkeypatch.setattr(transport.shutil, "disk_usage", lambda _: SimpleNamespace(free=7 * 1024**3))

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "fixture unavailable hardlink")

    monkeypatch.setattr(os, "link", cross_device)
    with pytest.raises(OSError):
        client.upload(ROUTE, source, "campaign/payload", receipt)
    assert not list(store.rglob("payload"))


def test_symlink_destination_parent_and_overlapping_roots_are_refused(assets, tmp_path):
    source, store, receipt = assets
    other = tmp_path / "outside"
    other.mkdir()
    (store / "1").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        transport.ScratchTracking(store, source, 5010).upload(
            ROUTE, source, "campaign/payload", receipt
        )
    assert not list(other.iterdir())
    with pytest.raises(ValueError, match="overlaps"):
        transport.ScratchTracking(source, source, 5010)
