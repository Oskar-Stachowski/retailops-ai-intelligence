"""Build the installed package from the actual API image's COPY inputs."""

import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def test_api_image_copy_inputs_build_complete_wheel(tmp_path):
    stage = tmp_path / "image-build"
    stage.mkdir()
    for line in (ROOT / "Dockerfile.api").read_text().splitlines():
        if line.startswith("FROM ") and (stage / "pyproject.toml").exists():
            break
        if not line.startswith("COPY "):
            continue
        fields = line.split()[1:]
        for name in fields[:-1]:
            source = ROOT / name
            target = stage / fields[-1] / (source.name if len(fields) > 2 else "")
            if source.is_dir():
                shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    result = subprocess.run(  # noqa: S603 - own interpreter/build context; no shell or network
        [sys.executable, "-m", "hatchling", "build", "--target", "wheel"],
        cwd=stage,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wheel = next((stage / "dist").glob("*.whl"))
    with ZipFile(wheel) as archive:
        for path in (ROOT / "src/retailops_ai").rglob("*.py"):
            assert archive.read(path.relative_to(ROOT / "src").as_posix()) == path.read_bytes()
        for source, packaged in (
            ("contracts/events/suggestion-source-v1", "intelligence_events/suggestion_source_v1"),
            ("contracts/evaluation/v14", "evaluation_campaign/v14"),
        ):
            for path in (ROOT / source).iterdir():
                assert (
                    archive.read("retailops_ai/" + packaged + "/" + path.name) == path.read_bytes()
                )
