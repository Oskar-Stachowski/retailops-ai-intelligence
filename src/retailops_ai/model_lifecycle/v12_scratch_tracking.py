"""Explicit shared-inode transport for disposable, owned GitHub-hosted Linux scratch only."""

import os
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt
from retailops_ai.model_lifecycle.v12_mlflow import LocalTracking
from retailops_ai.source_snapshot.files import (
    checked_directory,
    directory_fd,
    file_hash,
    regular_file,
    relative_path,
)

PREFIX = "/api/2.0/mlflow-artifacts/artifacts/"


class ScratchTracking(LocalTracking):
    """Link recovered scratch bytes into MLflow; the importer still GET-checks every byte.

    This is explicitly not an independent archive or backup. Both directories belong to
    one disposable hosted run. Original S3 objects and the macOS clone transport are untouched.
    """

    def __init__(self, store: Path, source: Path, port: int) -> None:
        super().__init__(port)
        if (
            sys.platform != "linux"
            or os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux"
        ):
            raise ValueError("v12_scratch_transport_hosted_linux_only")
        self.root: Path = checked_directory(store)
        self.source: Path = checked_directory(source)
        for directory in (self.root, self.source):
            info = directory.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("v12_scratch_private_owned_directory_required")
        if self.root.is_relative_to(self.source) or self.source.is_relative_to(self.root):
            raise ValueError("v12_scratch_store_overlaps_export")
        if self.root.stat().st_dev != self.source.stat().st_dev:
            raise ValueError("v12_scratch_same_filesystem_required")

    def upload(self, path: str, root: Path, name: str, receipt: V12ArtifactReceipt) -> None:
        if checked_directory(root) != self.source or not path.startswith(PREFIX):
            raise ValueError("v12_scratch_exact_source_and_route_required")
        source_name = relative_path(name)
        target = relative_path(urllib.parse.unquote(path.removeprefix(PREFIX)))
        escaped = "/".join(urllib.parse.quote(part, safe="") for part in target.parts)
        if (
            path != PREFIX + escaped
            or len(target.parts) != len(source_name.parts) + 3
            or re.fullmatch(r"[0-9]+", target.parts[0]) is None
            or re.fullmatch(r"[0-9a-f]{32}", target.parts[1]) is None
            or target.parts[2] != "artifacts"
            or target.parts[3:] != source_name.parts
        ):
            raise ValueError("v12_scratch_artifact_path_binding")
        if shutil.disk_usage(self.root).free < 6 * 1024**3:
            raise ValueError("v12_scratch_free_space_limit")
        expected = (receipt.size_bytes, receipt.sha256)
        if file_hash(self.source, name) != expected:
            raise ValueError("v12_scratch_source_changed")
        parent = self.root
        for component in target.parts[:-1]:
            parent /= component
            parent.mkdir(mode=0o700, exist_ok=True)
            checked_directory(parent)
            info = parent.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("v12_scratch_private_parent_required")
        source_parent = self.source.joinpath(*source_name.parts[:-1])
        with (
            regular_file(self.source, name) as source,
            directory_fd(source_parent) as source_directory,
            directory_fd(parent) as destination,
        ):
            info = os.fstat(source.fileno())
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("v12_scratch_private_source_required")
            # Create-only, no copy fallback; same-inode mutation is detected by both hashes
            # and by the unchanged importer's full HTTP checksum and final source census.
            os.link(
                source_name.name,
                target.name,
                src_dir_fd=source_directory,
                dst_dir_fd=destination,
                follow_symlinks=False,
            )
            try:
                linked = os.stat(target.name, dir_fd=destination, follow_symlinks=False)
                if (
                    (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino)
                    or file_hash(self.source, name) != expected
                    or file_hash(parent, target.name) != expected
                ):
                    raise ValueError("v12_scratch_link_or_source_changed")
                os.fsync(destination)
            except BaseException:
                os.unlink(target.name, dir_fd=destination)
                raise
