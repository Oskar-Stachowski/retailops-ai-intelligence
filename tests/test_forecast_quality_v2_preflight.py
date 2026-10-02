"""Preparation checks do not need campaign execution or test labels."""

import json

import pytest

from scripts import forecast_quality_v2_preflight as preflight


def test_disk_estimate_includes_longer_calendar_uncompressed_verify_and_logical_limit_risk():
    estimate = preflight.estimate_space(
        {
            "origins": {"start": "2026-06-01", "end": "2026-09-08"},
            "source": {"products": 100, "stores": 2},
        }
    )
    assert estimate["planned_origins"] == 100
    assert estimate["feature_rows_upper_bound"] == 280000
    assert estimate["logical_feature_bytes_estimate"] > 3904302270
    assert estimate["logical_budget_risk"]
    assert estimate["minimum_free_bytes_before_full_run"] >= sum(
        estimate["components_bytes"].values()
    )
    assert estimate["minimum_free_bytes_before_full_run"] > 5 * preflight.GIB


def test_retention_and_freeze_detect_mutations(tmp_path, monkeypatch):
    payload = tmp_path / "result.json"
    payload.write_text('{"failed":29}\n')
    receipt = {
        "path": str(payload),
        "bytes": payload.stat().st_size,
        "sha256": preflight.digest(payload),
    }
    inventory = tmp_path / "retained.json"
    inventory.write_text(json.dumps({"files": [receipt]}))
    frozen = tmp_path / "freeze.json"
    frozen.write_text(
        json.dumps(
            {
                "protocol_version": "forecast-quality-2.0.0",
                "file_sha256": {"result.json": receipt["sha256"]},
            }
        )
    )
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight, "FROZEN_FILES", ("result.json",))
    assert preflight.verify_retained(inventory)["status"] == "passed"
    assert preflight.verify_freeze(frozen)["status"] == "passed"
    payload.write_text('{"failed":0}\n')
    with pytest.raises(ValueError, match="retained_artifact_changed"):
        preflight.verify_retained(inventory)
    with pytest.raises(ValueError, match="freeze_mismatch"):
        preflight.verify_freeze(frozen)
    frozen.write_text(json.dumps({"protocol_version": "forecast-quality-2.0.0", "file_sha256": {}}))
    with pytest.raises(ValueError, match="freeze_inventory"):
        preflight.verify_freeze(frozen)
