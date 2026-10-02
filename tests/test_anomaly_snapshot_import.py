"""Frozen anomaly handoffs verified without any producer implementation."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from retailops_ai.curated.builder import build_curated
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json
from retailops_ai.source_snapshot.importer import import_snapshot, verify_import, verify_snapshot

ARCHIVE = Path(__file__).resolve().parents[1] / "data/fixtures/anomaly-v1_2.zip"


def hashes(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("anomaly12").resolve()
    with ZipFile(ARCHIVE) as archive:
        assert sum(r.file_size for r in archive.infolist()) < 32 * 1024**2
        assert all(
            not r.filename.startswith("/") and ".." not in Path(r.filename).parts
            for r in archive.infolist()
        )
        archive.extractall(root)
    return root


@pytest.mark.parametrize("case", ["demand", "physical"])
def test_public_import_is_immutable_and_truth_free(fixture, tmp_path, case):
    source = fixture / case / "public"
    before = hashes(source)
    imported = import_snapshot(
        source, tmp_path / "data/generated", required_use_cases=("anomaly_source",)
    )
    manifest = imported.snapshot.manifest
    assert imported.status == "published" and manifest["schema_version"] == "1.2.0"
    assert manifest["source"]["schema_version"] == "2.8.0" and len(manifest["tables"]) == 43
    assert not (imported.directory / "snapshot/evaluation_truth").exists()
    after = hashes(imported.directory)
    assert (
        import_snapshot(
            source, tmp_path / "data/generated", required_use_cases=("anomaly_source",)
        ).status
        == "reused"
    )
    assert hashes(imported.directory) == after and hashes(source) == before
    assert (
        verify_import(imported.directory, required_use_cases=("anomaly_source",)).snapshot_id
        == imported.snapshot.snapshot_id
    )


@pytest.mark.parametrize("case", ["demand", "physical"])
def test_private_import_requires_opt_in_and_preserves_separate_artifacts(fixture, tmp_path, case):
    source = fixture / case / "private"
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        import_snapshot(source, tmp_path / "blocked/data/generated")
    imported = import_snapshot(
        source,
        tmp_path / "private/data/generated",
        allow_evaluation_truth=True,
        required_use_cases=("anomaly_source",),
    )
    assert len(imported.snapshot.manifest["tables"]) == 55
    assert (imported.directory / "snapshot/evaluation_truth/anomaly_scenario.json").is_file()
    assert (
        verify_import(
            imported.directory, allow_evaluation_truth=True, required_use_cases=("anomaly_source",)
        ).snapshot_id
        == imported.snapshot.snapshot_id
    )
    with pytest.raises(SnapshotError, match="explicit_opt_in"):
        verify_import(imported.directory)


@pytest.mark.parametrize("case", ["demand", "physical"])
def test_private_plan_cannot_change_when_artifact_hashes_are_resealed(fixture, tmp_path, case):
    root = Path(shutil.copytree(fixture / case / "private", tmp_path / "input"))
    path = root / "evaluation_truth/anomaly_scenario.json"
    scenario = json.loads(path.read_text())
    injection = scenario["plan"]["injections"][0]
    injection["magnitude"] = (
        injection["magnitude"] + 1 if isinstance(injection["magnitude"], int) else "4"
    )
    path.write_bytes(canonical_json(scenario) + b"\n")
    manifest_path = root / "snapshot_manifest.json"
    document = json.loads(manifest_path.read_text())
    document["source"]["scenario"].update(
        size_bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    (root / "manifests/dataset_manifest.v2.json").write_bytes(
        canonical_json(document["source"]) + b"\n"
    )
    for ref in document["metadata_files"]:
        raw = (root / ref["path"]).read_bytes()
        ref.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest_path.write_bytes(canonical_json(document) + b"\n")
    (root / "manifest.sha256").write_text(
        hashlib.sha256(manifest_path.read_bytes()).hexdigest() + "\n"
    )
    with pytest.raises(SnapshotError, match="private_anomaly_binding"):
        verify_snapshot(root, allow_evaluation_truth=True, required_use_cases=("anomaly_source",))


def test_public_truth_insertion_and_unknown_version_fail_closed(fixture, tmp_path):
    root = Path(shutil.copytree(fixture / "demand/public", tmp_path / "input"))
    (root / "facts/anomaly_injections.json").write_text("{}")
    with pytest.raises(SnapshotError, match="missing_or_extra_artifact"):
        verify_snapshot(root, required_use_cases=("anomaly_source",))
    (root / "facts/anomaly_injections.json").unlink()
    path = root / "snapshot_manifest.json"
    document = json.loads(path.read_text())
    document["schema_version"] = "1.3.0"
    path.write_bytes(canonical_json(document) + b"\n")
    with pytest.raises(SnapshotError, match="unsupported_snapshot"):
        verify_snapshot(root, required_use_cases=("anomaly_source",))


def test_import_does_not_claim_curated_or_model_readiness(fixture, tmp_path):
    imported = import_snapshot(fixture / "demand/public", tmp_path / "import/data/generated")
    curated = build_curated(imported.directory, tmp_path / "curated/data/generated")
    assert curated.manifest["schema_version"] == "1.2.0"
    assert curated.manifest["readiness"]["anomaly"] == "not_ready"
    assert curated.manifest["readiness"]["forecast_model"] == "not_ready"
