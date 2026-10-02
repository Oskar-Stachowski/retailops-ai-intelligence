"""New compact evidence keeps exact source bytes and refuses damaged archives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from retailops_ai.forecasting.functional_v12_archive import (
    restore_checkpoint,
    seal_checkpoint,
    verify_checkpoint,
)
from retailops_ai.source_snapshot.files import SnapshotError


def _source(root: Path) -> dict[str, Path]:
    raw = root / "source"
    snapshot = root / "snapshot"
    raw.mkdir(parents=True)
    snapshot.mkdir()
    (raw / "rows.json").write_text('{"zero": 0, "missing": null}\n')
    (snapshot / ("a" * 120 + ".parquet")).write_bytes(bytes(range(256)) * 7)
    return {"raw_source": raw, "snapshot": snapshot}


def test_checkpoint_replays_exact_bytes_without_mutating_source(tmp_path: Path) -> None:
    sources = _source(tmp_path / "input")
    first = seal_checkpoint(sources, tmp_path / "archives", lineage={"seed": 710001})
    manifest = verify_checkpoint(first)
    assert manifest["descriptor"]["qualification"] == "storage_receipt_only"
    assert len(manifest["descriptor"]["files"]) == 2
    restored = restore_checkpoint(first, tmp_path / "restored")
    for namespace, source in sources.items():
        for path in source.iterdir():
            assert (restored / namespace / path.name).read_bytes() == path.read_bytes()
    second = seal_checkpoint(sources, tmp_path / "archives2", lineage={"seed": 710001})
    assert first.name == second.name
    assert (first / "payload.tar.gz").read_bytes() == (second / "payload.tar.gz").read_bytes()
    with pytest.raises(FileExistsError, match="immutable_destination_exists"):
        restore_checkpoint(first, tmp_path / "restored")


def test_checkpoint_detects_compressed_corruption_and_lineage_tampering(tmp_path: Path) -> None:
    sources = _source(tmp_path / "input")
    sealed = seal_checkpoint(sources, tmp_path / "archives", lineage={"seed": 710002})
    archive = sealed / "payload.tar.gz"
    original = archive.read_bytes()
    archive.write_bytes(original[:-8] + b"corrupt!")
    with pytest.raises(SnapshotError, match="checkpoint_archive_checksum"):
        verify_checkpoint(sealed)
    archive.write_bytes(original)
    manifest_path = sealed / "checkpoint_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["descriptor"]["lineage"]["seed"] = 710003
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="checkpoint_manifest_identity"):
        restore_checkpoint(sealed, tmp_path / "untrusted")
    assert not (tmp_path / "untrusted").exists()


def test_checkpoint_rejects_links_and_unsafe_roots(tmp_path: Path) -> None:
    sources = _source(tmp_path / "input")
    before = hashlib.sha256((sources["raw_source"] / "rows.json").read_bytes()).hexdigest()
    (sources["raw_source"] / "link.json").symlink_to(sources["raw_source"] / "rows.json")
    with pytest.raises(SnapshotError, match="checkpoint_special_file"):
        seal_checkpoint(sources, tmp_path / "archives", lineage={})
    with pytest.raises(SnapshotError, match="checkpoint_unsafe_member_name"):
        seal_checkpoint({"../outside": sources["snapshot"]}, tmp_path / "archives", lineage={})
    assert hashlib.sha256((sources["raw_source"] / "rows.json").read_bytes()).hexdigest() == before
    assert not (tmp_path / "archives").exists()
