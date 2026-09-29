"""Bounded artifact-volume transfer for the offline MLflow backup controller."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path("/var/mlflow/artifacts")
MAX_FILES = 100_000
MAX_BYTES = 4 * 1024**3


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or "\\" in name
        or "\x00" in name
    ):
        raise ValueError("unsafe_artifact_name")
    return path


def source_files() -> list[Path]:
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise ValueError("artifact_volume_missing")
    files: list[Path] = []
    total = 0
    for path in sorted(ROOT.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("special_artifact_forbidden")
        safe_name(path.relative_to(ROOT).as_posix())
        files.append(path)
        total += path.stat().st_size
        if len(files) > MAX_FILES or total > MAX_BYTES:
            raise ValueError("artifact_volume_limit")
    return files


def export() -> None:
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        for path in source_files():
            name = path.relative_to(ROOT).as_posix()
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode = 0o600
            info.mtime = 0
            with path.open("rb") as stream:
                archive.addfile(info, stream)


def import_archive() -> None:
    if not ROOT.is_dir() or ROOT.is_symlink() or any(ROOT.iterdir()):
        raise ValueError("artifact_volume_not_empty")
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=ROOT) as temporary:
        stage = Path(temporary)
        seen: set[str] = set()
        total = 0
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
            for member in archive:
                name = safe_name(member.name).as_posix()
                if not member.isfile() or name in seen or member.size < 0:
                    raise ValueError("invalid_artifact_archive")
                seen.add(name)
                total += member.size
                if len(seen) > MAX_FILES or total > MAX_BYTES:
                    raise ValueError("artifact_archive_limit")
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("artifact_archive_read_failed")
                with destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024**2)
                destination.chmod(0o600)
                if destination.stat().st_size != member.size:
                    raise ValueError("artifact_archive_size_mismatch")
        for child in sorted(stage.iterdir()):
            os.rename(child, ROOT / child.name)
    print(json.dumps({"status": "restored", "files": len(seen), "bytes": total}))


def inspect() -> None:
    files = source_files()
    print(
        json.dumps(
            {
                "empty": not any(ROOT.iterdir()),
                "files": len(files),
                "bytes": sum(path.stat().st_size for path in files),
            }
        )
    )


if __name__ == "__main__":
    try:
        {"export": export, "import": import_archive, "inspect": inspect}[sys.argv[1]]()
    except (OSError, ValueError, KeyError, IndexError, tarfile.TarError):
        print("artifact_volume_transfer_failed", file=sys.stderr)
        raise SystemExit(1) from None
