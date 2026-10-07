"""Strictly type-check native owner copies under their actual isolated runtime namespace."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="retailops-ai10-native-types-") as directory:
        src = Path(directory) / "src"
        package = src / "retailops_ai"
        package.mkdir(parents=True)
        shutil.copyfile(ROOT / "src/retailops_ai/__init__.py", package / "__init__.py")
        overlay = package / "source_snapshot"
        shutil.copytree(
            ROOT / "src/retailops_ai/source_snapshot",
            overlay,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for source in (ROOT / "src/retailops_ai/source_snapshot_native").glob("*.py"):
            if source.name != "__init__.py":
                shutil.copyfile(source, overlay / source.name)
        result = subprocess.run(  # noqa: S603 - fixed mypy command, own temporary source overlay
            [
                sys.executable,
                "-m",
                "mypy",
                "--config-file",
                str(ROOT / "pyproject.toml"),
                "--no-incremental",
                "--cache-dir",
                str(Path(directory) / "cache"),
                str(overlay),
            ],
            cwd=ROOT,
            env=dict(os.environ, MYPYPATH=str(src) + os.pathsep + str(ROOT / "src")),
            check=False,
        )
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
