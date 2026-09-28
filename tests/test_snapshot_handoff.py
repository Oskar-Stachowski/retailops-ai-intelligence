"""Independent handoff acceptance: no RetailOps package or generator dependency."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "handoff_checker", ROOT / "scripts/check_snapshot_handoff.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture_copy(tmp_path):
    package = tmp_path / "fixture"
    shutil.copytree(checker.FIXTURE, package)
    return package


def test_reviewed_contract_and_expected_fixture_pass_without_generator():
    before = {
        p.relative_to(checker.FIXTURE).as_posix(): checker.file_sha256(p)
        for p in checker.FIXTURE.rglob("*")
        if p.is_file()
    }
    first, second = checker.verify_fixture(), checker.verify_fixture()
    assert first == second
    assert first["tables"] == 25 and first["rows"] == 31171
    assert first["typed_import"] == "not_implemented"
    assert before == {
        p.relative_to(checker.FIXTURE).as_posix(): checker.file_sha256(p)
        for p in checker.FIXTURE.rglob("*")
        if p.is_file()
    }
    assert not any(name.startswith("data.generator") for name in sys.modules)


def test_schema_is_self_contained():
    schema = checker.read_json(checker.REGISTRY / "snapshot_manifest.schema.json")
    Draft202012Validator.check_schema(schema)

    def references(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$ref":
                    assert item.startswith("#/")
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(schema)


def test_detached_self_contained_transfer_twice(tmp_path):
    destination = tmp_path / "consumer"
    for name in ("contracts/source_snapshot/v1", "data/fixtures/ai-smoke-v1"):
        shutil.copytree(ROOT / name, destination / name)
    (destination / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/check_snapshot_handoff.py",
        destination / "scripts/check_snapshot_handoff.py",
    )
    command = [sys.executable, "-I", str(destination / "scripts/check_snapshot_handoff.py")]
    results = [
        subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=True)
        for _ in range(2)
    ]
    assert json.loads(results[0].stdout) == json.loads(results[1].stdout)
    assert json.loads(results[0].stdout)["status"] == "passed"


@pytest.mark.parametrize(
    "fault",
    [
        "corrupt",
        "missing",
        "extra",
        "directory",
        "symlink",
        "expected",
        "contract",
        "version",
        "source_version",
        "identity",
        "path",
        "classification",
    ],
)
def test_transfer_corruption_is_rejected(tmp_path, fault):
    package = fixture_copy(tmp_path)
    manifest_path = package / "snapshot/snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if fault == "corrupt":
        path = next((package / "snapshot/facts").rglob("*.parquet"))
        with path.open("ab") as stream:
            stream.write(b"bad")
    elif fault == "missing":
        next((package / "snapshot/facts").rglob("*.parquet")).unlink()
    elif fault == "extra":
        (package / "snapshot/extra.txt").write_text("unexpected")
    elif fault == "directory":
        (package / "extra").mkdir()
    elif fault == "symlink":
        path = package / "snapshot/manifest.sha256"
        path.unlink()
        path.symlink_to(checker.FIXTURE / "snapshot/manifest.sha256")
    elif fault == "expected":
        path = package / "expected_manifest.json"
        payload = json.loads(path.read_text())
        payload["rows"] += 1
        path.write_text(json.dumps(payload))
    elif fault == "contract":
        path = package / "contract.json"
        payload = json.loads(path.read_text())
        payload["contract_version"] = "9.0.0"
        path.write_text(json.dumps(payload))
    else:
        if fault == "version":
            manifest["schema_version"] = "9.0.0"
        elif fault == "source_version":
            manifest["source"]["schema_version"] = "9.0.0"
        elif fault == "identity":
            manifest["source_dataset_id"] = "source-sha256-" + "0" * 64
        elif fault == "path":
            manifest["tables"][0]["files"][0]["path"] = "../outside.parquet"
        elif fault == "classification":
            manifest["tables"][0]["data_class"] = "simulation_truth"
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        checker.verify_fixture(package)
