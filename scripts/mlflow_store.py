"""Offline PostgreSQL + artifact-volume backup and fresh-target restore for local MLflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import local_stack as stack
from mlflow_volume import MAX_BYTES, MAX_FILES, safe_name

from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

BACKUPS = stack.ROOT / ".local" / "mlflow-backups"
VOLUME_HELPER = stack.ROOT / "scripts" / "mlflow_volume.py"
PROJECT = re.compile(r"^retailops_ai_[a-z0-9_]{5,50}$")
MAX_BUNDLE_FILE = 4 * 1024**3
DB_DUMP = "metadata.dump"
ARTIFACTS = "artifacts.tar"
MANIFEST = "manifest.json"


def compose(project: str, *args: str) -> list[str]:
    if PROJECT.fullmatch(project) is None:
        raise ValueError("invalid_mlflow_project")
    docker = shutil.which("docker")
    if docker is None:
        raise ValueError("docker_unavailable")
    return [
        docker,
        "compose",
        "-p",
        project,
        "--env-file",
        str(stack.environment_file(create=False)),
        "-f",
        str(stack.ROOT / "compose.yaml"),
        *args,
    ]


def checked_run(command: list[str], *, stdin: Any = None, stdout: Any = subprocess.PIPE) -> bytes:
    result = subprocess.run(  # noqa: S603 - structured Docker Compose command
        command,
        cwd=stack.ROOT,
        stdin=stdin,
        stdout=stdout,
        stderr=subprocess.PIPE,
        check=False,
        timeout=900,
    )
    if result.returncode:
        raise ValueError("mlflow_store_compose_command_failed")
    return result.stdout or b""


def volume_command(project: str, action: str) -> list[str]:
    if action not in {"export", "import", "inspect"}:
        raise ValueError("invalid_volume_action")
    return compose(
        project,
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "-v",
        f"{VOLUME_HELPER}:/opt/retailops_mlflow_volume.py:ro",
        "--entrypoint",
        "python",
        "mlflow",
        "/opt/retailops_mlflow_volume.py",
        action,
    )


def database_command(project: str, action: str) -> list[str]:
    commands = {
        "dump": 'PGPASSWORD="$MLFLOW_DB_PASSWORD" exec pg_dump -Fc --no-owner --no-acl -h 127.0.0.1 -U mlflow_app -d retailops_mlflow',
        "restore": 'PGPASSWORD="$MLFLOW_DB_PASSWORD" exec pg_restore --no-owner --no-acl --exit-on-error --single-transaction -h 127.0.0.1 -U mlflow_app -d retailops_mlflow',
        "tables": 'PGPASSWORD="$MLFLOW_DB_PASSWORD" exec psql -h 127.0.0.1 -U mlflow_app -d retailops_mlflow -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_tables WHERE schemaname = \'public\'"',
    }
    return compose(project, "exec", "-T", "db", "sh", "-c", commands[action])


def tar_inventory(path: Path) -> tuple[int, int]:
    seen: set[str] = set()
    total = 0
    with tarfile.open(path, mode="r|*") as archive:
        for member in archive:
            name = safe_name(member.name).as_posix()
            if not member.isfile() or name in seen or member.size < 0:
                raise ValueError("invalid_mlflow_artifact_archive")
            seen.add(name)
            total += member.size
            if len(seen) > MAX_FILES or total > MAX_BYTES:
                raise ValueError("mlflow_artifact_archive_limit")
    return len(seen), total


def verify_bundle(root: Path) -> dict[str, Any]:
    checked_directory(root)
    document = read_json(root, MANIFEST)
    if (
        set(document)
        != {
            "schema_version",
            "kind",
            "backup_id",
            "created_at",
            "source_project",
            "compose_sha256",
            "dockerfile_sha256",
            "files",
            "artifact_files",
            "artifact_bytes",
        }
        or document["schema_version"] != "1.0.0"
        or document["kind"] != "mlflow_offline_store"
    ):
        raise ValueError("mlflow_backup_manifest_invalid")
    if (
        not isinstance(document["source_project"], str)
        or PROJECT.fullmatch(document["source_project"]) is None
        or any(
            not isinstance(document[name], str)
            or re.fullmatch(r"[0-9a-f]{64}", document[name]) is None
            for name in ("compose_sha256", "dockerfile_sha256")
        )
        or type(document["artifact_files"]) is not int
        or type(document["artifact_bytes"]) is not int
        or document["artifact_files"] < 0
        or document["artifact_bytes"] < 0
        or not isinstance(document["files"], dict)
    ):
        raise ValueError("mlflow_backup_source_invalid")
    created = datetime.fromisoformat(document["created_at"])
    offset = created.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("mlflow_backup_time_invalid")
    identity = {k: v for k, v in document.items() if k != "backup_id"}
    expected = (
        "mlflow-backup-sha256-"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    if document["backup_id"] != expected or root.name != expected:
        raise ValueError("mlflow_backup_identity_mismatch")
    inventory(root, {MANIFEST, DB_DUMP, ARTIFACTS})
    if set(document["files"]) != {DB_DUMP, ARTIFACTS}:
        raise ValueError("mlflow_backup_files_invalid")
    for name in (DB_DUMP, ARTIFACTS):
        receipt = document["files"][name]
        if (
            set(receipt) != {"size_bytes", "sha256"}
            or type(receipt["size_bytes"]) is not int
            or not 0 < receipt["size_bytes"] <= MAX_BUNDLE_FILE
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"])
            or file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"])
        ):
            raise ValueError("mlflow_backup_checksum_mismatch")
    with (root / DB_DUMP).open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("mlflow_backup_pg_format_invalid")
    files, total = tar_inventory(root / ARTIFACTS)
    if (files, total) != (document["artifact_files"], document["artifact_bytes"]):
        raise ValueError("mlflow_backup_artifact_count_mismatch")
    return document


def backup() -> Path:
    project = stack.project_name()
    if checked_run(database_command(project, "tables")).strip() == b"0":
        raise ValueError("mlflow_schema_not_initialized")
    running = bool(
        checked_run(compose(project, "ps", "--status", "running", "-q", "mlflow")).strip()
    )
    if running:
        checked_run(compose(project, "stop", "mlflow"))
    try:
        BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
        checked_directory(BACKUPS)
        if BACKUPS.stat().st_mode & 0o077:
            raise ValueError("mlflow_backup_directory_permissions")
        with tempfile.TemporaryDirectory(prefix=".mlflow-backup-", dir=BACKUPS) as temporary:
            root = Path(temporary)
            for name, command in (
                (DB_DUMP, database_command(project, "dump")),
                (ARTIFACTS, volume_command(project, "export")),
            ):
                fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as output:
                    checked_run(command, stdout=output)
            artifacts, artifact_bytes = tar_inventory(root / ARTIFACTS)
            identity: dict[str, Any] = {
                "schema_version": "1.0.0",
                "kind": "mlflow_offline_store",
                "created_at": datetime.now(UTC).isoformat(),
                "source_project": project,
                "compose_sha256": hashlib.sha256(
                    (stack.ROOT / "compose.yaml").read_bytes()
                ).hexdigest(),
                "dockerfile_sha256": hashlib.sha256(
                    (stack.ROOT / "Dockerfile.mlflow").read_bytes()
                ).hexdigest(),
                "files": {
                    name: dict(zip(("size_bytes", "sha256"), file_hash(root, name), strict=True))
                    for name in (DB_DUMP, ARTIFACTS)
                },
                "artifact_files": artifacts,
                "artifact_bytes": artifact_bytes,
            }
            backup_id = (
                "mlflow-backup-sha256-"
                + hashlib.sha256(
                    json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
            manifest = {**identity, "backup_id": backup_id}
            (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            os.chmod(root / MANIFEST, 0o600)
            fsync_tree(root)
            destination = BACKUPS / backup_id
            publish_noreplace(root, destination)
            verify_bundle(destination)
            return destination
    finally:
        if running:
            checked_run(compose(project, "up", "-d", "--wait", "mlflow"))


def restore(bundle: Path, project: str) -> dict[str, Any]:
    manifest = verify_bundle(bundle)
    if PROJECT.fullmatch(project) is None:
        raise ValueError("invalid_mlflow_project")
    if (
        manifest["compose_sha256"]
        != hashlib.sha256((stack.ROOT / "compose.yaml").read_bytes()).hexdigest()
        or manifest["dockerfile_sha256"]
        != hashlib.sha256((stack.ROOT / "Dockerfile.mlflow").read_bytes()).hexdigest()
    ):
        raise ValueError("mlflow_restore_configuration_mismatch")
    if checked_run(compose(project, "ps", "--status", "running", "-q", "mlflow")).strip():
        raise ValueError("mlflow_restore_requires_stopped_server")
    checked_run(compose(project, "up", "-d", "--wait", "db"))
    checked_run(compose(project, "build", "mlflow"))
    if checked_run(database_command(project, "tables")).strip() != b"0":
        raise ValueError("mlflow_restore_requires_empty_database")
    volume = json.loads(checked_run(volume_command(project, "inspect")))
    if volume != {"empty": True, "files": 0, "bytes": 0}:
        raise ValueError("mlflow_restore_requires_empty_volume")
    with (bundle / DB_DUMP).open("rb") as source:
        checked_run(database_command(project, "restore"), stdin=source)
    with (bundle / ARTIFACTS).open("rb") as source:
        result = json.loads(checked_run(volume_command(project, "import"), stdin=source))
    if (
        result
        != {
            "status": "restored",
            "files": manifest["artifact_files"],
            "bytes": manifest["artifact_bytes"],
        }
        or checked_run(database_command(project, "tables")).strip() == b"0"
    ):
        raise ValueError("mlflow_restore_postcondition_failed")
    return {
        "status": "restored",
        "backup_id": manifest["backup_id"],
        "target_project": project,
        "artifact_files": result["files"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    check = sub.add_parser("verify")
    check.add_argument("--bundle", type=Path, required=True)
    recovery = sub.add_parser("restore")
    recovery.add_argument("--bundle", type=Path, required=True)
    recovery.add_argument("--target-project", required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = {"status": "backed_up", "bundle": str(backup())}
        elif args.command == "verify":
            doc = verify_bundle(args.bundle)
            result = {
                "status": "verified",
                "backup_id": doc["backup_id"],
                "artifact_files": doc["artifact_files"],
            }
        else:
            result = restore(args.bundle, args.target_project)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        SnapshotError,
        subprocess.TimeoutExpired,
        tarfile.TarError,
    ):
        print('{"error":"mlflow_store_operation_failed"}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
