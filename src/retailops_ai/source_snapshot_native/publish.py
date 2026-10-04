"""Durable directory publication that refuses even an empty existing destination."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path

from retailops_ai.source_snapshot.files import SnapshotError, directory_fd, regular_file


def fsync_tree(root: Path) -> None:
    directories = [root]
    for path in root.rglob("*"):
        if path.is_dir():
            directories.append(path)
        else:
            with regular_file(root, path.relative_to(root).as_posix()) as stream:
                os.fsync(stream.fileno())
    for path in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        with directory_fd(path) as descriptor:
            os.fsync(descriptor)


def publish_noreplace(source: Path, destination: Path) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    with directory_fd(source.parent) as source_fd, directory_fd(destination.parent) as target_fd:
        if sys.platform == "linux":
            function = getattr(library, "renameat2", None)
            flag = 1  # Linux RENAME_NOREPLACE.
        elif sys.platform == "darwin":
            function = getattr(library, "renameatx_np", None)
            flag = 4  # Darwin RENAME_EXCL.
        else:
            function = None
            flag = 0
        if function is None:
            raise SnapshotError("atomic_no_replace_unavailable")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            source_fd, os.fsencode(source.name), target_fd, os.fsencode(destination.name), flag
        )
        if result:
            code = ctypes.get_errno()
            if code in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(code, "immutable_destination_exists")
            raise OSError(code, "atomic_publication_failed")
        os.fsync(target_fd)
