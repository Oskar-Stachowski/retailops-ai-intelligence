"""Coherent backup boundary: corruption, overwrite, interruption and fail-closed recovery."""

import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import lifecycle_store as store  # noqa: E402
import mlflow_store as legacy  # noqa: E402

from retailops_ai.source_snapshot.files import SnapshotError, file_hash  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "LOCK", tmp_path / "controller.lock")
    monkeypatch.setattr(store, "MAINTENANCE", tmp_path / "maintenance.json")


def bundle(
    tmp_path: Path,
    *,
    member: str = "experiment/lifecycle/model.json",
    kind: bytes = tarfile.REGTYPE,
) -> Path:
    draft = tmp_path / "draft"
    draft.mkdir()
    for name in (store.AI_DUMP, store.MLFLOW_DUMP):
        (draft / name).write_bytes(b"PGDMP-test")
    with tarfile.open(draft / legacy.ARTIFACTS, "w") as archive:
        info = tarfile.TarInfo(member)
        info.type = kind
        info.size = 5 if kind == tarfile.REGTYPE else 0
        archive.addfile(info, io.BytesIO(b"proof") if kind == tarfile.REGTYPE else None)
    state = {
        database: {"tables": {"proof": {"rows": 1, "sha256": "a" * 64}}, "sequences": {}}
        for database in store.DATABASES
    }
    state["ai_revision"] = "0009_model_lifecycle"
    (draft / store.STATE).write_text(json.dumps(state))
    identity = {
        "schema_version": "1.0.0",
        "kind": "ai_mlflow_offline_lifecycle",
        "created_at": "2026-09-30T00:00:00+00:00",
        "source_project": "retailops_ai_source_test",
        "pins": store.pins(),
        "state_sha256": store.digest(state),
        "artifact_files": 1,
        "artifact_bytes": 5,
        "files": {
            name: dict(zip(("size_bytes", "sha256"), file_hash(draft, name), strict=True))
            for name in store.FILES
        },
    }
    backup_id = "lifecycle-backup-sha256-" + store.digest(identity)
    (draft / legacy.MANIFEST).write_text(json.dumps({**identity, "backup_id": backup_id}))
    destination = tmp_path / backup_id
    draft.rename(destination)
    return destination


@pytest.mark.parametrize("name", [store.AI_DUMP, store.MLFLOW_DUMP, store.STATE, legacy.ARTIFACTS])
def test_every_bundle_component_is_checked_before_restore(tmp_path: Path, name: str) -> None:
    archive = bundle(tmp_path)
    assert store.verify_bundle(archive)["kind"] == "ai_mlflow_offline_lifecycle"
    with (archive / name).open("ab") as output:
        output.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        store.verify_bundle(archive)


@pytest.mark.parametrize(
    "member,kind",
    [
        ("../escape", tarfile.REGTYPE),
        ("model.json", tarfile.SYMTYPE),
        ("/absolute", tarfile.REGTYPE),
    ],
)
def test_untrusted_artifact_paths_never_reach_docker(
    tmp_path: Path, member: str, kind: bytes
) -> None:
    with pytest.raises((ValueError, SnapshotError)):
        store.verify_bundle(bundle(tmp_path, member=member, kind=kind))


def test_bundle_does_not_follow_symlink_or_accept_extra_file(tmp_path: Path) -> None:
    archive = bundle(tmp_path)
    (archive / "unexpected").write_text("extra")
    with pytest.raises(SnapshotError):
        store.verify_bundle(archive)
    (archive / "unexpected").unlink()
    dump = archive / store.AI_DUMP
    external = tmp_path / "external"
    dump.rename(external)
    dump.symlink_to(external)
    with pytest.raises(SnapshotError):
        store.verify_bundle(archive)


@pytest.mark.parametrize("existing", ["containers", "volume", "network", "unlabelled"])
def test_restore_refuses_all_existing_resources_before_any_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: str
) -> None:
    archive = bundle(tmp_path)
    target = "retailops_ai_target_test"
    commands: list[list[str]] = []

    def run(command: list[str], **_: Any) -> bytes:
        commands.append(command)
        if existing == "containers" and "ps" in command:
            return b"existing\n"
        if existing in {"volume", "network"} and existing in command and "--filter" in command:
            return b"existing\n"
        if existing == "unlabelled" and "--format" in command:
            return (target + "_postgres_data\n").encode()
        return b""

    monkeypatch.setattr(legacy, "checked_run", run)
    with pytest.raises(ValueError, match="fresh_project"):
        store.restore(archive, target)
    assert not any(
        "build" in command or "up" in command or "pg_restore" in command for command in commands
    )


def test_configuration_mismatch_and_source_restore_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = bundle(tmp_path)
    with pytest.raises(ValueError, match="source_forbidden"):
        store.restore(archive, "retailops_ai_source_test")
    monkeypatch.setattr(store, "pins", lambda: {})
    with pytest.raises(ValueError, match="configuration_mismatch"):
        store.restore(archive, "retailops_ai_target_test")


def maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: dict[str, Any]
) -> dict[str, Any]:
    path = tmp_path / "maintenance.json"
    previous = {"system_identifier": "123456", "limits": dict.fromkeys(store.DATABASES, -1)}
    store.write_private(
        path,
        {
            "schema_version": "1.0.0",
            "project": "retailops_ai_source_test",
            "previous": previous,
            "running": ["mlflow"],
        },
    )
    monkeypatch.setattr(store, "MAINTENANCE", path)
    monkeypatch.setattr(store, "limits", lambda _project: actual)
    return previous


def test_interrupted_maintenance_restores_only_prior_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = {"system_identifier": "123456", "limits": dict.fromkeys(store.DATABASES, 0)}
    previous = maintenance(tmp_path, monkeypatch, actual)
    transitions: list[Any] = []
    commands: list[Any] = []
    monkeypatch.setattr(
        store, "set_limits", lambda p, before, after: transitions.append((p, before, after))
    )
    monkeypatch.setattr(legacy, "checked_run", lambda command, **_: commands.append(command))
    assert store.resume_maintenance()["status"] == "resumed"
    assert transitions == [("retailops_ai_source_test", actual, previous["limits"])]
    assert commands[0][-1] == "mlflow" and "api" not in commands[0]
    assert not store.MAINTENANCE.exists()


@pytest.mark.parametrize(
    "actual",
    [
        {"system_identifier": "987654", "limits": dict.fromkeys(store.DATABASES, 0)},
        {"system_identifier": "123456", "limits": {"retailops_ai": 0, "retailops_mlflow": 9}},
    ],
)
def test_resume_refuses_recreated_database_or_unexpected_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: dict[str, Any]
) -> None:
    maintenance(tmp_path, monkeypatch, actual)
    with pytest.raises(ValueError, match="state_changed"):
        store.resume_maintenance()
    assert store.MAINTENANCE.exists()


def test_partial_restore_never_unfences_or_starts_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = bundle(tmp_path)
    commands: list[list[str]] = []
    monkeypatch.setattr(store, "require_fresh", lambda _project: None)
    monkeypatch.setattr(
        store,
        "limits",
        lambda _project: {
            "system_identifier": "123456",
            "limits": dict.fromkeys(store.DATABASES, -1),
        },
    )
    monkeypatch.setattr(store, "fence", lambda *_: None)
    monkeypatch.setattr(store, "sql", lambda *_: b"")

    def run(command: list[str], **_: Any) -> bytes:
        commands.append(command)
        if "pg_restore" in command:
            raise ValueError("simulated_restore_failure")
        return b""

    monkeypatch.setattr(legacy, "checked_run", run)

    def forbidden(*_: Any) -> None:
        pytest.fail("partial restore must keep connection fence")

    monkeypatch.setattr(store, "set_limits", forbidden)
    with pytest.raises(ValueError, match="simulated_restore_failure"):
        store.restore(archive, "retailops_ai_target_test")
    assert not any("up" in command and "mlflow" in command for command in commands)


def test_fence_never_interpolates_unvalidated_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="record_invalid"):
        store.set_limits(
            "retailops_ai_source_test",
            {
                "system_identifier": "123456",
                "limits": {"retailops_ai": "0; DROP DATABASE postgres", "retailops_mlflow": -1},
            },
            dict.fromkeys(store.DATABASES, 0),
        )


def test_smoke_never_deletes_preexisting_source(monkeypatch: pytest.MonkeyPatch) -> None:
    import check_lifecycle_store as smoke

    commands: list[list[str]] = []

    def refuse(_project: str) -> None:
        raise ValueError("lifecycle_restore_requires_fresh_project")

    monkeypatch.setattr(store, "require_fresh", refuse)
    monkeypatch.setattr(legacy, "checked_run", lambda command, **_: commands.append(command))
    assert smoke.main() == 2
    assert not commands


def test_controller_lock_serializes_operations_and_releases_after_error() -> None:
    with store.controller_lock(), pytest.raises(ValueError, match="busy"):
        with store.controller_lock():
            pytest.fail("second controller must not enter")
    with store.controller_lock():
        pass


def test_new_backup_keeps_interrupted_maintenance_record(monkeypatch: pytest.MonkeyPatch) -> None:
    store.write_private(store.MAINTENANCE, {"interrupted": True})
    commands: list[Any] = []
    monkeypatch.setattr(legacy, "checked_run", lambda command, **_: commands.append(command))
    with pytest.raises(ValueError, match="resume_required"):
        store.backup("retailops_ai_source_test")
    assert store.MAINTENANCE.exists() and not commands


@pytest.mark.parametrize("bad", [True, -1, 1_000_001])
def test_state_inventory_rejects_invalid_or_unbounded_row_counts(bad: Any) -> None:
    state = {
        database: {"tables": {"proof": {"rows": bad, "sha256": "a" * 64}}, "sequences": {}}
        for database in store.DATABASES
    }
    state["ai_revision"] = "0009_model_lifecycle"
    with pytest.raises(ValueError):
        store.validate_state(state)
