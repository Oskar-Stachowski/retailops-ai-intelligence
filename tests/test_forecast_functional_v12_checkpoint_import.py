"""Downloaded archives retain exact bytes and cannot smuggle arbitrary ZIP paths."""

from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

from retailops_ai.forecasting.functional_v12_archive import seal_checkpoint
from retailops_ai.source_snapshot.files import SnapshotError

SPEC = importlib.util.spec_from_file_location(
    "cohort_zip_import", Path(__file__).parents[1] / "scripts/import_forecast_cohort_checkpoint.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "facts.csv").write_text("observed_sales_units\n0\n1\n")
    lineage = {
        "freeze_id": "functional-v12-freeze-sha256-" + "a" * 64,
        "seed": 11,
        "scope": "cohort_preparation_only",
    }
    checkpoint = seal_checkpoint({"source": source}, tmp_path / "sealed", lineage=lineage)
    archive = tmp_path / "download.zip"
    with zipfile.ZipFile(archive, "x") as zipped:
        for path in checkpoint.iterdir():
            zipped.write(path, arcname=path.name)
    options = {
        "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "freeze_id": lineage["freeze_id"],
        "seed": 11,
        "maximum_bytes": 1024**2,
    }
    return archive, checkpoint, options


def test_download_import_is_exact_idempotent_and_never_regenerates(tmp_path: Path) -> None:
    archive, original, options = fixture(tmp_path)
    result = MODULE.import_checkpoint_zip(archive, tmp_path / "imported", **options)
    for path in original.iterdir():
        assert (Path(result["directory"]) / path.name).read_bytes() == path.read_bytes()
    assert result["source_regenerated"] is False
    assert MODULE.import_checkpoint_zip(archive, tmp_path / "imported", **options) == result
    assert archive.exists() and original.exists()


def test_wrong_seed_checksum_and_traversal_are_rejected(tmp_path: Path) -> None:
    archive, _, options = fixture(tmp_path)
    with pytest.raises(SnapshotError, match="wrong_frozen_parent_or_seed"):
        MODULE.import_checkpoint_zip(archive, tmp_path / "imported", **(options | {"seed": 12}))
    with pytest.raises(SnapshotError, match="download_checksum"):
        MODULE.import_checkpoint_zip(
            archive, tmp_path / "imported", **(options | {"zip_sha256": "b" * 64})
        )
    with zipfile.ZipFile(archive, "a") as zipped:
        zipped.writestr("../escaped", "untrusted")
    options["zip_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(SnapshotError, match="inventory_or_expansion_budget"):
        MODULE.import_checkpoint_zip(archive, tmp_path / "imported", **options)
    assert not (tmp_path / "escaped").exists()
    assert not list((tmp_path / "imported").iterdir())


def test_backstop_expansion_does_not_override_selected_import_budget(tmp_path: Path) -> None:
    archive, checkpoint, options = fixture(tmp_path)
    total = sum(path.stat().st_size for path in checkpoint.iterdir())
    assert MODULE.MAX_CHECKPOINT_BYTES == 768 * 1024**2
    result = MODULE.import_checkpoint_zip(
        archive, tmp_path / "imported", **(options | {"maximum_bytes": MODULE.MAX_CHECKPOINT_BYTES})
    )
    assert result["checkpoint_bytes"] == total
    with pytest.raises(SnapshotError, match="input_or_budget"):
        MODULE.import_checkpoint_zip(
            archive,
            tmp_path / "over-backstop",
            **(options | {"maximum_bytes": MODULE.MAX_CHECKPOINT_BYTES + 1}),
        )
    with pytest.raises(SnapshotError, match="inventory_or_expansion_budget"):
        MODULE.import_checkpoint_zip(
            archive, tmp_path / "under-selected-budget", **(options | {"maximum_bytes": total - 1})
        )
    assert archive.exists() and checkpoint.exists()
