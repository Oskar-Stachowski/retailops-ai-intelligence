"""A real sdist excludes private state and rebuilds the complete runtime wheel."""

import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile


def test_sdist_rejects_private_state_and_preserves_complete_runtime(tmp_path):
    root = Path(__file__).resolve().parents[1]
    local = root / ".local"
    local.mkdir(exist_ok=True)
    credential = root / (".env.package-probe-" + uuid4().hex)
    outside = tmp_path / "private-target"
    outside.write_text("private packaging control, not a credential\n")
    try:
        credential.write_text("PRIVATE_PACKAGE_CONTROL=must-not-ship\n")
        with tempfile.TemporaryDirectory(prefix="package-probe-", dir=local) as private:
            (Path(private) / "absolute-symlink").symlink_to(outside)
            (Path(private) / "private-model.bin").write_bytes(b"private runtime state")
            outputs = {}
            for target in ("sdist", "wheel"):
                destination = tmp_path / target
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "hatchling",
                        "build",
                        "-t",
                        target,
                        "-d",
                        str(destination),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=120,
                )
                outputs[target] = next(destination.iterdir())
        extracted = tmp_path / "extracted"
        with tarfile.open(outputs["sdist"], "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                path = Path(member.name)
                assert not path.is_absolute() and ".." not in path.parts
                assert not member.issym() and not member.islnk()
                assert all(
                    p not in {".local", ".venv", ".tools", "data", "reports", "dist"}
                    for p in path.parts
                )
                assert not any(p.startswith(".env") for p in path.parts)
                if member.isfile():
                    assert path.parts[1] in {
                        "src",
                        "contracts",
                        "environments",
                        "pyproject.toml",
                        "uv.lock",
                        "README.md",
                        "LICENSE",
                        "PKG-INFO",
                        ".gitignore",
                    }
            archive.extractall(extracted, filter="data")
        project = next(extracted.iterdir())
        rebuilt = tmp_path / "rebuilt"
        subprocess.run(
            [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(rebuilt)],
            cwd=project,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        with ZipFile(outputs["wheel"]) as direct, ZipFile(next(rebuilt.iterdir())) as actual:
            expected = {
                n: direct.read(n) for n in direct.namelist() if n.startswith("retailops_ai/")
            }
            observed = {
                n: actual.read(n) for n in actual.namelist() if n.startswith("retailops_ai/")
            }
            assert observed == expected
            assert len([n for n in observed if n.endswith(".py")]) >= 600
            assert not any(".local" in n or ".env" in n for n in actual.namelist())
    finally:
        credential.unlink(missing_ok=True)
