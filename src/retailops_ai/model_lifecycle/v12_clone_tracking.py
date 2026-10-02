"""Explicit macOS clone transport for an operator-owned, host-mounted MLflow store."""

import ctypes
import os
import shutil
import sys
import urllib.parse
import uuid
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


def clone_regular_file(source_fd: int, destination_fd: int, name: str) -> None:
    """Fail if cloning is unavailable; never fall back to a physical copy or hard link."""
    if sys.platform != "darwin":
        raise ValueError("v12_artifact_clone_requires_macos")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    clone = library.fclonefileat
    clone.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32)
    clone.restype = ctypes.c_int
    # CLONE_NOOWNERCOPY: keep the operator's ownership, with source addressed by open fd.
    if clone(source_fd, destination_fd, os.fsencode(name), 0x0002) != 0:
        raise OSError(ctypes.get_errno(), "v12_artifact_clone_failed")


class CloneTracking(LocalTracking):
    """Clone bytes into an explicit bind mount; normal HTTP verifies every served artifact."""

    def __init__(self, root: Path, port: int, *, minimum_free_bytes: int = 50 * 1024**3) -> None:
        super().__init__(port)
        self.root = checked_directory(root)
        info = self.root.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("v12_artifact_clone_private_operator_store_required")
        if type(minimum_free_bytes) is not int or minimum_free_bytes < 0:
            raise ValueError("v12_artifact_clone_space_policy")
        self.minimum_free_bytes = minimum_free_bytes

    def upload(self, path: str, root: Path, name: str, receipt: V12ArtifactReceipt) -> None:
        prefix = "/api/2.0/mlflow-artifacts/artifacts/"
        if not path.startswith(prefix):
            raise ValueError("v12_artifact_clone_route")
        target_name = urllib.parse.unquote(path.removeprefix(prefix))
        target = relative_path(target_name)
        if self.root.is_relative_to(root.absolute()) or root.absolute().is_relative_to(self.root):
            raise ValueError("v12_artifact_clone_store_overlaps_export")
        if shutil.disk_usage(self.root).free < self.minimum_free_bytes + 16 * 1024**2:
            raise ValueError("v12_artifact_clone_free_space_limit")
        parent = self.root
        for component in target.parts[:-1]:
            parent /= component
            parent.mkdir(mode=0o700, exist_ok=True)
            checked_directory(parent)
            if parent.stat().st_uid != os.getuid() or parent.stat().st_mode & 0o077:
                raise ValueError("v12_artifact_clone_private_parent_required")
        staging = ".clone-" + uuid.uuid4().hex
        expected = (receipt.size_bytes, receipt.sha256)
        with regular_file(root, name) as source, directory_fd(parent) as destination:
            if os.fstat(source.fileno()).st_dev != os.fstat(destination).st_dev:
                raise ValueError("v12_artifact_clone_same_filesystem_required")
            try:
                clone_regular_file(source.fileno(), destination, staging)
                os.chmod(staging, 0o600, dir_fd=destination)
                if file_hash(parent, staging) != expected or file_hash(root, name) != expected:
                    raise ValueError("v12_artifact_clone_source_or_result_changed")
                # This link publishes the new clone, never the original source inode.
                os.link(staging, target.name, src_dir_fd=destination, dst_dir_fd=destination)
                os.fsync(destination)
            finally:
                try:
                    os.unlink(staging, dir_fd=destination)
                except FileNotFoundError:
                    pass
        if shutil.disk_usage(self.root).free < self.minimum_free_bytes:
            raise ValueError("v12_artifact_clone_free_space_limit")
