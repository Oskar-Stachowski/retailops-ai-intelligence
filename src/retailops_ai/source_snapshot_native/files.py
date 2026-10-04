"""Bounded reads and safe paths; input file descriptors never follow symlinks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn


class SnapshotError(ValueError):
    """A public error code, without row contents or credentials."""


MAX_METADATA_BYTES = 4 * 1024 * 1024


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(c in value for c in ("\\", ":", "\x00"))
    ):
        raise SnapshotError("unsafe_artifact_path")
    return path


def checked_directory(path: Path) -> Path:
    absolute = path.absolute()
    if any(p.is_symlink() for p in (absolute, *absolute.parents)):
        raise SnapshotError("symlink_directory")
    if not absolute.is_dir():
        raise SnapshotError("directory_required")
    return absolute


@contextmanager
def directory_fd(path: Path) -> Iterator[int]:
    """Walk from / with O_NOFOLLOW, including every ancestor of the root."""
    absolute = path.absolute()
    if ".." in absolute.parts:
        raise SnapshotError("unsafe_directory_path")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def regular_file(root: Path, relative: str) -> Iterator[BinaryIO]:
    parts = relative_path(relative).parts
    with directory_fd(root) as root_fd:
        parent = os.dup(root_fd)
        descriptor: int | None = None
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SnapshotError("regular_artifact_required")
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            with stream:
                yield stream
        finally:
            os.close(parent)
            if descriptor is not None:
                os.close(descriptor)


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError("duplicate_json_key")
        result[key] = value
    return result


def nonfinite(value: str) -> NoReturn:
    raise SnapshotError("nonfinite_json_number")


def decode_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_METADATA_BYTES:
        raise SnapshotError("metadata_size_limit")
    try:
        result = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SnapshotError("invalid_json_document") from exc
    if not isinstance(result, dict):
        raise SnapshotError("json_object_required")
    return result


def read_bytes(root: Path, relative: str, limit: int = MAX_METADATA_BYTES) -> bytes:
    with regular_file(root, relative) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SnapshotError("metadata_size_limit")
    return raw


def read_json(root: Path, relative: str) -> dict[str, Any]:
    return decode_json(read_bytes(root, relative))


def file_hash(root: Path, relative: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with regular_file(root, relative) as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def inventory(root: Path, names: set[str]) -> None:
    checked_directory(root)
    directories = {
        parent.as_posix()
        for name in names
        for parent in relative_path(name).parents
        if parent.as_posix() != "."
    }
    found: set[str] = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base) / name
            mode = path.lstat().st_mode
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                if relative not in directories:
                    raise SnapshotError("unreferenced_directory")
            elif stat.S_ISREG(mode):
                if relative not in names:
                    raise SnapshotError("missing_or_extra_artifact")
                found.add(relative)
            else:
                raise SnapshotError("symlink_or_special_artifact")
    if found != names:
        raise SnapshotError("missing_or_extra_artifact")
