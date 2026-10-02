"""Clone transport guards; the OS clone is a small explicit portable double here."""

import errno
import hashlib
import os
import shutil
from types import SimpleNamespace

import pytest

from retailops_ai.model_lifecycle import v12_clone_tracking as transport
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt


@pytest.fixture
def assets(tmp_path, monkeypatch):
    source, store = tmp_path / "source", tmp_path / "store"
    source.mkdir(mode=0o700)
    store.mkdir(mode=0o700)
    data = b"original-checkpoint" * 100
    (source / "payload").write_bytes(data)
    receipt = V12ArtifactReceipt(size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    def portable_clone(source_fd, destination_fd, name):
        # A fresh inode models the syscall boundary; no APFS claim is made by this double.
        os.lseek(source_fd, 0, os.SEEK_SET)
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=destination_fd)
        with os.fdopen(fd, "wb") as output:
            output.write(os.read(source_fd, len(data)))

    monkeypatch.setattr(transport, "clone_regular_file", portable_clone)
    return source, store, receipt, data


ROUTE = "/api/2.0/mlflow-artifacts/artifacts/1/run/artifacts/payload"


def test_verified_independent_artifact_and_existing_destination_are_preserved(assets):
    source, store, receipt, data = assets
    client = transport.CloneTracking(store, 5010, minimum_free_bytes=0)
    client.upload(ROUTE, source, "payload", receipt)
    copied = store / "1/run/artifacts/payload"
    assert copied.read_bytes() == data
    assert copied.stat().st_ino != (source / "payload").stat().st_ino
    with pytest.raises(FileExistsError):
        client.upload(ROUTE, source, "payload", receipt)
    copied.write_bytes(b"private-change")
    assert (source / "payload").read_bytes() == data
    assert not list(store.rglob(".clone-*"))


@pytest.mark.parametrize(
    "route", ["/wrong/payload", ROUTE + "/../escape", ROUTE + "/%2e%2e/escape"]
)
def test_untrusted_routes_are_refused_before_writes(assets, route):
    source, store, receipt, _ = assets
    with pytest.raises(ValueError):
        transport.CloneTracking(store, 5010, minimum_free_bytes=0).upload(
            route, source, "payload", receipt
        )
    assert not list(store.iterdir())


def test_space_guard_and_unavailable_clone_never_fall_back_to_copy(assets, monkeypatch):
    source, store, receipt, _ = assets
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="free_space"):
        transport.CloneTracking(store, 5010).upload(ROUTE, source, "payload", receipt)
    assert not list(store.iterdir())
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))

    def unavailable(*_):
        raise OSError(errno.ENOTSUP, "clone unsupported")

    monkeypatch.setattr(transport, "clone_regular_file", unavailable)
    with pytest.raises(OSError):
        transport.CloneTracking(store, 5010).upload(ROUTE, source, "payload", receipt)
    assert not list(store.rglob("payload"))
    assert not list(store.rglob(".clone-*"))


def test_source_corruption_or_symlink_cannot_publish_an_artifact(assets):
    source, store, receipt, _ = assets
    (source / "payload").write_bytes(b"changed")
    client = transport.CloneTracking(store, 5010, minimum_free_bytes=0)
    with pytest.raises(ValueError, match="changed"):
        client.upload(ROUTE, source, "payload", receipt)
    assert not list(store.rglob("payload"))
    alias = source / "alias"
    alias.symlink_to(source / "payload")
    with pytest.raises(OSError):
        client.upload(ROUTE, source, alias.name, receipt)
    assert not list(store.rglob(".clone-*"))


def test_symlink_parent_cannot_escape_operator_store(assets, tmp_path):
    source, store, receipt, _ = assets
    other = tmp_path / "other"
    other.mkdir()
    (store / "1").symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        transport.CloneTracking(store, 5010, minimum_free_bytes=0).upload(
            ROUTE, source, "payload", receipt
        )
    assert not list(other.iterdir())


def test_overlapping_export_and_store_are_rejected_in_both_directions(assets):
    source, store, receipt, _ = assets
    for export, artifact_store in (
        (source, source),
        (source, source / "nested"),
        (store / "nested", store),
    ):
        artifact_store.mkdir(mode=0o700, exist_ok=True)
        export.mkdir(mode=0o700, exist_ok=True)
        with pytest.raises(ValueError, match="overlaps"):
            transport.CloneTracking(artifact_store, 5010, minimum_free_bytes=0).upload(
                ROUTE, export, "payload", receipt
            )
