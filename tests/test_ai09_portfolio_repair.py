"""Repaired fitting must retain exact original data and reject changed archives."""

import hashlib
import json
import stat
import zipfile
from copy import deepcopy

import pytest

from scripts import run_ai09_portfolio_repair as repair


def archive(tmp_path, names):
    path = tmp_path / "download.zip"
    with zipfile.ZipFile(path, "w") as zipped:
        for name in names:
            zipped.writestr(name, b"original")
    plan = deepcopy(repair.frozen_plan())
    plan["original"]["zip_size_bytes"] = path.stat().st_size
    plan["original"]["zip_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, plan


def test_exact_archive_restores_without_overwriting_an_attempt(tmp_path):
    path, plan = archive(tmp_path, ["control/features/original.json"])
    output = tmp_path / "restored"
    repair.restore_archive(path, output, plan)
    assert (output / "control/features/original.json").read_bytes() == b"original"
    with pytest.raises(ValueError, match="archive_identity"):
        repair.restore_archive(path, output, plan)


def test_changed_bytes_cannot_supply_repair_data(tmp_path):
    path, plan = archive(tmp_path, ["control/features/original.json"])
    path.write_bytes(path.read_bytes().replace(b"original", b"modified"))
    with pytest.raises(ValueError, match="archive_identity"):
        repair.restore_archive(path, tmp_path / "restored", plan)


@pytest.mark.parametrize(
    "name", ["control/../../outside", "/control/file", "control/../file", "unexpected/file"]
)
def test_paths_outside_original_control_are_refused_before_extraction(tmp_path, name):
    path, plan = archive(tmp_path, [name])
    with pytest.raises(ValueError, match="unsafe_archive"):
        repair.restore_archive(path, tmp_path / "restored", plan)
    assert not (tmp_path / "restored").exists()


def test_link_cannot_redirect_parent_restoration(tmp_path):
    link = zipfile.ZipInfo("control/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    path, plan = archive(tmp_path, [link])
    with pytest.raises(ValueError, match="unsafe_archive"):
        repair.restore_archive(path, tmp_path / "restored", plan)


def test_original_failure_and_cost_remain_bound():
    plan = repair.frozen_plan()
    assert plan["original"]["charged_preparation_wall_seconds"] > 400
    assert plan["original"]["charged_forecast_wall_seconds"] > 700
    assert plan["original"]["accepted_model_fits"] == 0
    assert plan["budgets"]["tree_rss_bytes"] == 12 * 1024**3


def test_repair_cannot_silently_authorize_more_attempts(tmp_path, monkeypatch):
    plan = repair.frozen_plan()
    plan["maximum_additional_comparison_attempts"] = 2
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(repair, "PLAN", path)
    with pytest.raises(ValueError, match="scope_changed"):
        repair.frozen_plan()
