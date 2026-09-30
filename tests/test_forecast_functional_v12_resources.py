"""Resource exhaustion cannot silently shrink the preregistered sample."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from retailops_ai.forecasting.functional_v12_resources import (
    GIB,
    MIB,
    disk_preflight,
    resource_plan,
    validate_resource_plan,
)
from retailops_ai.source_snapshot.files import SnapshotError


def plan() -> dict:
    return resource_plan(
        cohort_count=64,
        max_checkpoint_bytes=200 * MIB,
        max_expanded_checkpoint_bytes=2 * GIB,
        max_prediction_bytes_per_cohort=32 * MIB,
        max_scoring_rows_per_cohort=500000,
        pilot_receipt_sha256="a" * 64,
    )


def test_full_campaign_includes_retained_data_restore_index_and_replay() -> None:
    body = plan()
    assert body["retained_budget_bytes"] == 64 * 232 * MIB
    assert body["transient_and_safety_budget_bytes"] == 6 * GIB + 496 * MIB
    validate_resource_plan(body)
    body["initial_free_bytes_required"] -= GIB
    with pytest.raises(SnapshotError, match="arithmetic_or_policy_drift"):
        validate_resource_plan(body)


def test_insufficient_disk_reports_deficit_without_dropping_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _: type("Disk", (), {"free": 10 * GIB})())
    body = plan()
    result = disk_preflight(body, archive_volume=tmp_path, work_volume=tmp_path)
    assert result["status"] == "blocked_insufficient_space"
    assert (
        result["additional_free_bytes_required"] == body["initial_free_bytes_required"] - 10 * GIB
    )
    assert body["cohort_count"] == 64
    assert list(tmp_path.iterdir()) == []
    resumed = disk_preflight(
        body,
        archive_volume=tmp_path,
        work_volume=tmp_path,
        retained_checkpoint_bytes=10 * GIB,
    )
    assert (
        resumed["remaining_free_bytes_required"] == body["initial_free_bytes_required"] - 10 * GIB
    )


def test_row_and_output_budgets_are_finite_and_cannot_hide_previous_storage(tmp_path: Path) -> None:
    body = plan()
    with pytest.raises(SnapshotError, match="retained_bytes_exceed_budget"):
        disk_preflight(
            body,
            archive_volume=tmp_path,
            work_volume=tmp_path,
            retained_checkpoint_bytes=body["retained_budget_bytes"],
        )
    body["max_scoring_rows_per_cohort"] = 1000000
    with pytest.raises(SnapshotError, match="invalid_or_unmeasured"):
        validate_resource_plan(body)


def test_download_and_import_reserve_is_on_archive_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archives, work = tmp_path / "archives", tmp_path / "work"
    archives.mkdir()
    work.mkdir()
    original_stat = Path.stat

    def fake_stat(path: Path, **kwargs: object) -> os.stat_result:
        values = list(original_stat(path, **kwargs))
        if path == archives:
            values[2] += 1
        return os.stat_result(values)

    body = plan()
    archive_budget = 66 * body["max_checkpoint_bytes"]
    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda path: type(
            "Disk", (), {"free": archive_budget - 1 if path == archives else 20 * GIB}
        )(),
    )
    receipt = disk_preflight(body, archive_volume=archives, work_volume=work)
    assert receipt["same_device"] is False
    assert receipt["archive_remaining_budget_bytes"] == archive_budget
    assert receipt["status"] == "blocked_insufficient_space"
    assert receipt["additional_free_bytes_required"] == 1
