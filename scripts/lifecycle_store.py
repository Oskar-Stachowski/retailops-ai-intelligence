"""Offline coherent MLflow + AI application-state backup, with fresh-project restore."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import local_stack as stack
import mlflow_store as store
from mlflow_volume import MAX_BYTES, MAX_FILES

from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    inventory,
    read_json,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

BACKUPS = stack.ROOT / ".local/lifecycle-backups"
MAINTENANCE = stack.ROOT / ".local/lifecycle-maintenance.json"
LOCK = stack.ROOT / ".local/lifecycle-store.lock"
AI_DUMP = "ai.dump"
MLFLOW_DUMP = "mlflow.dump"
STATE = "state.json"
FILES = {AI_DUMP, MLFLOW_DUMP, store.ARTIFACTS, STATE}
DATABASES = {"retailops_ai": ("ai", "ai_app"), "retailops_mlflow": ("public", "mlflow_app")}
PIN_FILES = ("compose.yaml", "Dockerfile.api", "Dockerfile.mlflow", "uv.lock", "pyproject.toml")
PINS = (*PIN_FILES, "application_sha256")
MAX_ROWS = 1_000_000
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def pins() -> dict[str, str]:
    result = {
        name: hashlib.sha256((stack.ROOT / name).read_bytes()).hexdigest() for name in PIN_FILES
    }
    application = {
        path.relative_to(stack.ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in (stack.ROOT / "src", stack.ROOT / "contracts")
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix in {".py", ".json"}
    }
    result["application_sha256"] = digest(application)
    return result


@contextmanager
def controller_lock() -> Iterator[None]:
    stack.environment_file(create=False)
    descriptor = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("lifecycle_lock_permissions")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("lifecycle_store_busy") from None
        yield
    finally:
        os.close(descriptor)


def admin_command(project: str, database: str, *args: str) -> list[str]:
    if database not in {*DATABASES, "postgres"}:
        raise ValueError("invalid_lifecycle_database")
    # Credentials expand only inside the container, never in host argv or output.
    return store.compose(
        project,
        "exec",
        "-T",
        "db",
        "sh",
        "-c",
        'PGPASSWORD="$POSTGRES_PASSWORD" exec "$@"',
        "lifecycle-db",
        *args,
        "-h",
        "127.0.0.1",
        "-U",
        "retailops_admin",
        "-d",
        database,
    )


def sql(project: str, database: str, statement: str, *, stdout: Any = subprocess.PIPE) -> bytes:
    with tempfile.TemporaryFile() as source:
        source.write(statement.encode())
        source.seek(0)
        return store.checked_run(
            admin_command(project, database, "psql", "-X", "-At", "-v", "ON_ERROR_STOP=1"),
            stdin=source,
            stdout=stdout,
        )


def limits(project: str) -> dict[str, Any]:
    return dict(
        json.loads(
            sql(
                project,
                "postgres",
                """
SELECT json_build_object(
 'system_identifier', (SELECT system_identifier::text FROM pg_control_system()),
 'limits', (SELECT json_object_agg(datname,datconnlimit) FROM pg_database
            WHERE datname IN ('retailops_ai','retailops_mlflow')));
""",
            )
        )
    )


def set_limits(project: str, expected: dict[str, Any], target: dict[str, int]) -> None:
    if set(expected) != {"system_identifier", "limits"} or set(target) != set(DATABASES):
        raise ValueError("lifecycle_fence_record_invalid")
    if (
        not re.fullmatch(r"[0-9]{1,20}", expected["system_identifier"])
        or set(expected["limits"]) != set(DATABASES)
        or any(
            type(v) is not int or not -1 <= v <= 2**31 - 1
            for v in [*expected["limits"].values(), *target.values()]
        )
    ):
        raise ValueError("lifecycle_fence_record_invalid")
    conditions = " AND ".join(
        f"(SELECT datconnlimit FROM pg_database WHERE datname='{name}')={value}"  # noqa: S608 - fixed database keys and validated integers
        for name, value in expected["limits"].items()
    )
    commands = "\n".join(
        f"ALTER DATABASE {name} CONNECTION LIMIT {value};" for name, value in target.items()
    )
    sql(
        project,
        "postgres",
        f"""  -- Fixed database keys, validated numeric identifier/limits.
BEGIN;
SELECT pg_advisory_xact_lock(505030);
DO $$ BEGIN
 IF NOT ((SELECT system_identifier::text FROM pg_control_system())='{expected["system_identifier"]}'
 AND {conditions}) THEN RAISE EXCEPTION 'lifecycle_fence_state_changed'; END IF;
END $$;
{commands}
COMMIT;
""",  # noqa: S608 - identifiers and integers are strictly validated above
    )


def fence(project: str, previous: dict[str, Any]) -> None:
    set_limits(project, previous, dict.fromkeys(DATABASES, 0))
    sql(
        project,
        "postgres",
        """
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('retailops_ai','retailops_mlflow') AND pid<>pg_backend_pid();
""",
    )
    if (
        sql(
            project,
            "postgres",
            """
SELECT count(*) FROM pg_stat_activity
 WHERE datname IN ('retailops_ai','retailops_mlflow');
""",
        ).strip()
        != b"0"
    ):
        raise ValueError("lifecycle_fence_sessions_remain")


def running_services(project: str) -> list[str]:
    return [
        name
        for name in ("api", "mlflow")
        if store.checked_run(
            store.compose(project, "ps", "--status", "running", "-q", name)
        ).strip()
    ]


def write_private(path: Path, value: Any) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def resume_maintenance() -> dict[str, Any]:
    if MAINTENANCE.is_symlink() or MAINTENANCE.stat().st_mode & 0o077:
        raise ValueError("lifecycle_maintenance_permissions")
    record = read_json(MAINTENANCE.parent, MAINTENANCE.name)
    if (
        set(record) != {"schema_version", "project", "previous", "running"}
        or record["schema_version"] != "1.0.0"
        or store.PROJECT.fullmatch(record["project"]) is None
        or not isinstance(record["running"], list)
        or len(record["running"]) != len(set(record["running"]))
        or set(record["running"]) - {"api", "mlflow"}
    ):
        raise ValueError("lifecycle_maintenance_record_invalid")
    project = record["project"]
    actual = limits(project)
    previous = record["previous"]
    if actual != previous:
        if actual != {**previous, "limits": dict.fromkeys(DATABASES, 0)}:
            raise ValueError("lifecycle_maintenance_state_changed")
        set_limits(project, actual, previous["limits"])
    if record["running"]:
        store.checked_run(store.compose(project, "up", "-d", "--wait", *record["running"]))
    MAINTENANCE.unlink()
    return {"status": "resumed", "source_project": project}


def database_inventory(project: str) -> dict[str, Any]:
    """Hash canonical sorted row images on disk, including sequence semantics."""
    result: dict[str, Any] = {}
    total_rows, total_bytes = 0, 0
    for database, (schema, _) in DATABASES.items():
        if (
            sql(
                project,
                database,
                f"SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema','{schema}');",  # noqa: S608 - fixed schema map
            ).strip()
            != b"0"
        ):  # noqa: S608 - schema comes from fixed DATABASES map
            raise ValueError("lifecycle_unmanaged_schema_tables")
        tables = json.loads(
            sql(
                project,
                database,
                f"""  -- Schema comes from the fixed DATABASES map.
SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]'::json)
 FROM pg_tables WHERE schemaname='{schema}';
""",  # noqa: S608 - fixed schema
            )
        )
        if not tables or len(tables) > 256:
            raise ValueError("lifecycle_schema_not_initialized")
        if any(not isinstance(name, str) or IDENTIFIER.fullmatch(name) is None for name in tables):
            raise ValueError("lifecycle_table_invalid")
        counts_query = " UNION ALL ".join(
            f"SELECT '{name}' name,count(*) n FROM {schema}.\"{name}\""  # noqa: S608 - fixed schema and strict IDENTIFIER
            for name in tables
        )
        counts = json.loads(
            sql(project, database, "SELECT json_object_agg(name,n) FROM (" + counts_query + ") c;")  # noqa: S608 - fixed schema and strict IDENTIFIER
        )
        if set(counts) != set(tables) or any(type(n) is not int or n < 0 for n in counts.values()):
            raise ValueError("lifecycle_table_count_invalid")
        total_rows += sum(counts.values())
        if total_rows > MAX_ROWS:
            raise ValueError("lifecycle_inventory_row_limit")
        rows: dict[str, Any] = {}
        for name in tables:
            with tempfile.TemporaryFile() as output:
                sql(
                    project,
                    database,
                    f'COPY (SELECT to_jsonb(t)::text FROM {schema}."{name}" t ORDER BY 1) TO STDOUT;',  # noqa: S608 - fixed schema and strict IDENTIFIER
                    stdout=output,
                )
                total_bytes += output.tell()
                if total_bytes > store.MAX_BUNDLE_FILE:
                    raise ValueError("lifecycle_inventory_byte_limit")
                output.seek(0)
                checksum = hashlib.file_digest(output, "sha256").hexdigest()
            rows[name] = {"rows": counts[name], "sha256": checksum}
        sequences = json.loads(
            sql(
                project,
                database,
                f"""  -- Schema comes from the fixed DATABASES map.
SELECT coalesce(json_agg(sequencename ORDER BY sequencename),'[]'::json)
 FROM pg_sequences WHERE schemaname='{schema}';
""",  # noqa: S608 - fixed schema
            )
        )
        values = {}
        for name in sequences:
            if not isinstance(name, str) or IDENTIFIER.fullmatch(name) is None:
                raise ValueError("lifecycle_sequence_invalid")
            values[name] = json.loads(
                sql(
                    project,
                    database,
                    f"SELECT json_build_object('last_value',last_value,'is_called',is_called) FROM {schema}.\"{name}\";",  # noqa: S608 - fixed schema and strict IDENTIFIER
                )
            )
        result[database] = {"tables": rows, "sequences": values}
    result["ai_revision"] = (
        sql(project, "retailops_ai", "SELECT version_num FROM ai.alembic_version;").decode().strip()
    )
    return result


def transfer_command(project: str, database: str, action: str) -> list[str]:
    schema, role = DATABASES[database]
    if action == "dump":
        args = ["pg_dump", "-Fc", "--no-owner", "--no-acl", f"--schema={schema}"]
    elif action == "restore":
        args = ["pg_restore", "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"]
    else:
        raise ValueError("invalid_lifecycle_transfer")
    return admin_command(project, database, *args, f"--role={role}")


def verify_bundle(root: Path) -> dict[str, Any]:
    checked_directory(root)
    doc = read_json(root, store.MANIFEST)
    if (
        set(doc)
        != {
            "schema_version",
            "kind",
            "backup_id",
            "created_at",
            "source_project",
            "pins",
            "files",
            "artifact_files",
            "artifact_bytes",
            "state_sha256",
        }
        or doc["schema_version"] != "1.0.0"
        or doc["kind"] != "ai_mlflow_offline_lifecycle"
        or not isinstance(doc["source_project"], str)
        or store.PROJECT.fullmatch(doc["source_project"]) is None
        or not isinstance(doc["pins"], dict)
        or set(doc["pins"]) != set(PINS)
        or any(
            not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
            for v in doc["pins"].values()
        )
        or not isinstance(doc["files"], dict)
        or set(doc["files"]) != FILES
        or type(doc["artifact_files"]) is not int
        or not 0 <= doc["artifact_files"] <= MAX_FILES
        or type(doc["artifact_bytes"]) is not int
        or not 0 <= doc["artifact_bytes"] <= MAX_BYTES
    ):
        raise ValueError("lifecycle_backup_manifest_invalid")
    created = datetime.fromisoformat(doc["created_at"])
    offset = created.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("lifecycle_backup_time_invalid")
    identity = {k: v for k, v in doc.items() if k != "backup_id"}
    if (
        doc["backup_id"] != "lifecycle-backup-sha256-" + digest(identity)
        or root.name != doc["backup_id"]
    ):
        raise ValueError("lifecycle_backup_identity_mismatch")
    inventory(root, {*FILES, store.MANIFEST})
    for name in FILES:
        receipt = doc["files"][name]
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"size_bytes", "sha256"}
            or type(receipt["size_bytes"]) is not int
            or not 0 < receipt["size_bytes"] <= store.MAX_BUNDLE_FILE
            or not isinstance(receipt["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"]) is None
            or file_hash(root, name) != (receipt["size_bytes"], receipt["sha256"])
        ):
            raise ValueError("lifecycle_backup_checksum_mismatch")
    for name in (AI_DUMP, MLFLOW_DUMP):
        with (root / name).open("rb") as stream:
            if stream.read(5) != b"PGDMP":
                raise ValueError("lifecycle_backup_pg_format_invalid")
    if store.tar_inventory(root / store.ARTIFACTS) != (
        doc["artifact_files"],
        doc["artifact_bytes"],
    ):
        raise ValueError("lifecycle_backup_artifact_count_mismatch")
    state = read_json(root, STATE)
    if set(state) != {*DATABASES, "ai_revision"} or digest(state) != doc["state_sha256"]:
        raise ValueError("lifecycle_backup_state_invalid")
    validate_state(state)
    return doc


def validate_state(state: dict[str, Any]) -> None:
    if (
        not isinstance(state["ai_revision"], str)
        or re.fullmatch(r"[0-9]{4}_[a-z0-9_]{1,55}", state["ai_revision"]) is None
    ):
        raise ValueError("lifecycle_backup_revision_invalid")
    total = 0
    for database in DATABASES:
        value = state[database]
        if not isinstance(value, dict) or set(value) != {"tables", "sequences"}:
            raise ValueError("lifecycle_backup_database_inventory_invalid")
        tables, sequences = value["tables"], value["sequences"]
        if (
            not isinstance(tables, dict)
            or not 1 <= len(tables) <= 256
            or not isinstance(sequences, dict)
            or len(sequences) > 256
        ):
            raise ValueError("lifecycle_backup_table_inventory_invalid")
        for name, receipt in tables.items():
            if (
                IDENTIFIER.fullmatch(name) is None
                or not isinstance(receipt, dict)
                or set(receipt) != {"rows", "sha256"}
                or type(receipt["rows"]) is not int
                or receipt["rows"] < 0
                or not isinstance(receipt["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"]) is None
            ):
                raise ValueError("lifecycle_backup_table_receipt_invalid")
            total += receipt["rows"]
        for name, sequence in sequences.items():
            if (
                IDENTIFIER.fullmatch(name) is None
                or not isinstance(sequence, dict)
                or set(sequence) != {"last_value", "is_called"}
                or type(sequence["last_value"]) is not int
                or not -(2**63) <= sequence["last_value"] < 2**63
                or type(sequence["is_called"]) is not bool
            ):
                raise ValueError("lifecycle_backup_sequence_invalid")
    if total > MAX_ROWS:
        raise ValueError("lifecycle_backup_inventory_row_limit")


def backup(project: str | None = None) -> Path:
    project = project or stack.project_name()
    with controller_lock():
        if MAINTENANCE.exists() or MAINTENANCE.is_symlink():
            raise ValueError("lifecycle_maintenance_resume_required")
        previous = limits(project)
        if set(previous["limits"]) != set(DATABASES) or 0 in previous["limits"].values():
            raise ValueError("lifecycle_source_already_fenced")
        running = running_services(project)
        write_private(
            MAINTENANCE,
            {
                "schema_version": "1.0.0",
                "project": project,
                "previous": previous,
                "running": running,
            },
        )
        try:
            if running:
                store.checked_run(store.compose(project, "stop", *running))
            fence(project, previous)
            BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
            checked_directory(BACKUPS)
            if BACKUPS.stat().st_mode & 0o077:
                raise ValueError("lifecycle_backup_directory_permissions")
            with tempfile.TemporaryDirectory(prefix=".lifecycle-backup-", dir=BACKUPS) as temporary:
                root = Path(temporary)
                state = database_inventory(project)
                write_private(root / STATE, state)
                for name, command in (
                    (AI_DUMP, transfer_command(project, "retailops_ai", "dump")),
                    (MLFLOW_DUMP, transfer_command(project, "retailops_mlflow", "dump")),
                    (store.ARTIFACTS, store.volume_command(project, "export")),
                ):
                    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "wb") as output:
                        store.checked_run(command, stdout=output)
                if database_inventory(project) != state:
                    raise ValueError("lifecycle_source_changed_during_backup")
                count, size = store.tar_inventory(root / store.ARTIFACTS)
                identity = {
                    "schema_version": "1.0.0",
                    "kind": "ai_mlflow_offline_lifecycle",
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_project": project,
                    "pins": pins(),
                    "state_sha256": digest(state),
                    "files": {
                        name: dict(
                            zip(("size_bytes", "sha256"), file_hash(root, name), strict=True)
                        )
                        for name in sorted(FILES)
                    },
                    "artifact_files": count,
                    "artifact_bytes": size,
                }
                backup_id = "lifecycle-backup-sha256-" + digest(identity)
                write_private(root / store.MANIFEST, {**identity, "backup_id": backup_id})
                fsync_tree(root)
                destination = BACKUPS / backup_id
                publish_noreplace(root, destination)
                verify_bundle(destination)
                return destination
        finally:
            resume_maintenance()


def require_fresh(project: str) -> None:
    docker = store.compose(project)[0]
    commands = [store.compose(project, "ps", "-a", "-q")]
    for kind in ("volume", "network"):
        commands.append(
            [docker, kind, "ls", "-q", "--filter", "label=com.docker.compose.project=" + project]
        )
    # Also detect unlabelled conflicting named volumes; compose labels alone are insufficient.
    commands.append([docker, "volume", "ls", "--format", "{{.Name}}"])
    for command in commands:
        output = store.checked_run(command).decode().splitlines()
        if (
            command == commands[-1]
            and any(
                name in {project + "_postgres_data", project + "_mlflow_artifacts"}
                for name in output
            )
        ) or (command != commands[-1] and output):
            raise ValueError("lifecycle_restore_requires_fresh_project")


def restore(
    bundle: Path,
    project: str,
    *,
    on_created: Callable[[], None] | None = None,
    build_images: bool = True,
) -> dict[str, Any]:
    manifest = verify_bundle(bundle)
    if manifest["pins"] != pins():
        raise ValueError("lifecycle_restore_configuration_mismatch")
    if project == manifest["source_project"] or project == stack.project_name():
        raise ValueError("lifecycle_restore_source_forbidden")
    with controller_lock():
        require_fresh(project)
        if on_created is not None:
            on_created()
        if build_images:
            store.checked_run(store.compose(project, "build", "api", "mlflow"))
        cache_options = [] if build_images else ["--no-build", "--pull", "never"]
        store.checked_run(store.compose(project, "up", *cache_options, "-d", "--wait", "db"))
        previous = limits(project)
        fence(project, previous)
        # This schema is created empty by init.sh. No CASCADE: refuse unexpected content.
        sql(project, "retailops_ai", "DROP SCHEMA ai;")
        sql(project, "retailops_mlflow", "DROP SCHEMA public;")
        for database, name in (("retailops_ai", AI_DUMP), ("retailops_mlflow", MLFLOW_DUMP)):
            with (bundle / name).open("rb") as source:
                store.checked_run(transfer_command(project, database, "restore"), stdin=source)
        with (bundle / store.ARTIFACTS).open("rb") as source:
            restored = json.loads(
                store.checked_run(store.volume_command(project, "import"), stdin=source)
            )
        if restored != {
            "status": "restored",
            "files": manifest["artifact_files"],
            "bytes": manifest["artifact_bytes"],
        }:
            raise ValueError("lifecycle_restore_artifacts_mismatch")
        # A second archive export verifies bytes, including files not referenced by a release.
        with tempfile.TemporaryFile() as output:
            store.checked_run(store.volume_command(project, "export"), stdout=output)
            size = output.tell()
            output.seek(0)
            checksum = hashlib.file_digest(output, "sha256").hexdigest()
        if (size, checksum) != (
            manifest["files"][store.ARTIFACTS]["size_bytes"],
            manifest["files"][store.ARTIFACTS]["sha256"],
        ):
            raise ValueError("lifecycle_restore_artifact_checksum_mismatch")
        state = database_inventory(project)
        if digest(state) != manifest["state_sha256"]:
            raise ValueError("lifecycle_restore_database_state_mismatch")
        # Only a fully verified target is unfenced. Failed/partial targets remain offline.
        set_limits(project, {**previous, "limits": dict.fromkeys(DATABASES, 0)}, previous["limits"])
        return {
            "status": "restored",
            "backup_id": manifest["backup_id"],
            "target_project": project,
            "state_sha256": manifest["state_sha256"],
            "artifact_files": manifest["artifact_files"],
            "services_started": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    sub.add_parser("resume")
    for name in ("verify", "restore"):
        command = sub.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        if name == "restore":
            command.add_argument("--target-project", required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = {"status": "backed_up", "bundle": str(backup())}
        elif args.command == "verify":
            manifest = verify_bundle(args.bundle)
            result = {
                "status": "verified",
                "backup_id": manifest["backup_id"],
                "state_sha256": manifest["state_sha256"],
            }
        elif args.command == "resume":
            with controller_lock():
                result = resume_maintenance()
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
        print('{"error":"lifecycle_store_operation_failed"}', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
