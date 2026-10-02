"""Offline MLflow backup must reject damaged metadata and unsafe artifact archives."""

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_mlflow_store as smoke  # noqa: E402
import local_stack as stack  # noqa: E402
import mlflow_store as store  # noqa: E402

from retailops_ai.source_snapshot.files import SnapshotError, file_hash  # noqa: E402


def test_failed_command_retains_source_location_without_raw_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_output = "postgresql://operator:private-password@db/private"
    diagnostic = {
        "error": "model_lifecycle_acceptance_failed",
        "exception_type": "ValueError",
        "acceptance_frames": [{"function": "require", "line": 28}],
    }
    monkeypatch.setattr(
        store.subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(
            returncode=2,
            stdout=private_output.encode(),
            stderr=(private_output + "\n" + json.dumps(diagnostic)).encode(),
        ),
    )
    with pytest.raises(store.ComposeCommandError) as caught:
        store.checked_run(["docker", "compose", "run"])
    assert caught.value.diagnostic == diagnostic
    assert private_output not in str(caught.value) + json.dumps(caught.value.diagnostic)


@pytest.mark.parametrize(
    "mutation", ["message", "exception_type", "function", "line", "too_many_frames"]
)
def test_child_diagnostic_rejects_extra_or_untrusted_details(mutation: str) -> None:
    record = {
        "error": "model_lifecycle_acceptance_failed",
        "exception_type": "ValueError",
        "acceptance_frames": [{"function": "require", "line": 28}],
    }
    if mutation == "message":
        record["message"] = "private-password"
    elif mutation == "exception_type":
        record["exception_type"] = "private-password"
    elif mutation == "function":
        record["acceptance_frames"][0]["function"] = "private-password"
    elif mutation == "line":
        record["acceptance_frames"][0]["line"] = "private-password"
    else:
        record["acceptance_frames"] *= 9
    error = store.ComposeCommandError(json.dumps(record).encode())
    assert error.diagnostic is None
    assert "private-password" not in str(error)


def bundle(
    tmp_path: Path,
    *,
    tar_name: str = "experiment/artifact.txt",
    kind: bytes = tarfile.REGTYPE,
    matching_config: bool = True,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema_version": "1.0.0",
        "kind": "mlflow_offline_store",
        "created_at": "2026-09-29T00:00:00+00:00",
        "source_project": "retailops_ai_1234567890",
        "compose_sha256": (
            hashlib.sha256((ROOT / "compose.yaml").read_bytes()).hexdigest()
            if matching_config
            else "a" * 64
        ),
        "dockerfile_sha256": (
            hashlib.sha256((ROOT / "Dockerfile.mlflow").read_bytes()).hexdigest()
            if matching_config
            else "b" * 64
        ),
        "artifact_files": 1,
        "artifact_bytes": 5,
    }
    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / store.DB_DUMP).write_bytes(b"PGDMPtest")
    with tarfile.open(draft / store.ARTIFACTS, "w") as archive:
        info = tarfile.TarInfo(tar_name)
        info.type = kind
        info.size = 5 if kind == tarfile.REGTYPE else 0
        archive.addfile(info, io.BytesIO(b"proof") if kind == tarfile.REGTYPE else None)
    identity["files"] = {
        name: dict(zip(("size_bytes", "sha256"), file_hash(draft, name), strict=True))
        for name in (store.DB_DUMP, store.ARTIFACTS)
    }
    backup_id = (
        "mlflow-backup-sha256-"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    final = tmp_path / backup_id
    draft.rename(final)
    (final / store.MANIFEST).write_text(
        json.dumps({**identity, "backup_id": backup_id}, sort_keys=True)
    )
    return final


def test_backup_receipts_and_unsafe_archive_rejected(tmp_path: Path) -> None:
    good = bundle(tmp_path / "good")
    assert store.verify_bundle(good)["artifact_files"] == 1
    (good / store.DB_DUMP).write_bytes(b"PGDMPchanged")
    with pytest.raises(ValueError, match="checksum"):
        store.verify_bundle(good)
    with pytest.raises((ValueError, SnapshotError)):
        store.verify_bundle(bundle(tmp_path / "traversal", tar_name="../escape"))
    with pytest.raises((ValueError, SnapshotError)):
        store.verify_bundle(bundle(tmp_path / "symlink", kind=tarfile.SYMTYPE))


def test_restore_requires_empty_target_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = bundle(tmp_path)
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> bytes:
        commands.append(command)
        if "tables" in str(command) or "SELECT count" in str(command):
            return b"1\n"
        return b""

    monkeypatch.setattr(store, "checked_run", fake_run)
    with pytest.raises(ValueError, match="empty_database"):
        store.restore(archive, "retailops_ai_restoration_test")
    assert not any("pg_restore" in str(command) for command in commands)


def test_restore_requires_original_compose_and_image_recipe(tmp_path: Path) -> None:
    archive = bundle(tmp_path, matching_config=False)
    with pytest.raises(ValueError, match="configuration_mismatch"):
        store.restore(archive, "retailops_ai_restoration_test")


def test_volume_import_refuses_path_traversal_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow_volume as volume

    root = tmp_path / "volume"
    root.mkdir()
    monkeypatch.setattr(volume, "ROOT", root)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("../outside.txt")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"proof"))
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(raw.getvalue())})())
    with pytest.raises(ValueError, match="unsafe_artifact_name"):
        volume.import_archive()
    assert not list(root.iterdir())


def test_volume_import_keeps_restored_files_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow_volume as volume

    root = tmp_path / "volume"
    root.mkdir()
    monkeypatch.setattr(volume, "ROOT", root)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("experiment/proof.txt")
        info.size = 5
        archive.addfile(info, io.BytesIO(b"proof"))
    monkeypatch.setattr(sys, "stdin", type("Input", (), {"buffer": io.BytesIO(raw.getvalue())})())
    volume.import_archive()
    assert (root / "experiment/proof.txt").read_bytes() == b"proof"
    assert (root / "experiment").stat().st_mode & 0o777 == 0o700
    assert (root / "experiment/proof.txt").stat().st_mode & 0o777 == 0o600


def test_smoke_never_deletes_an_existing_target(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(stack, "main", lambda _args: 0)

    def fake_run(command: list[str], **_: Any) -> bytes:
        commands.append(command)
        return b"existing" if "volume" in command else b""

    monkeypatch.setattr(store, "checked_run", fake_run)
    assert smoke.main() == 1
    assert not any("-v" in command and "down" in command for command in commands)
