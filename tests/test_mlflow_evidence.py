"""Historical MLflow import preserves provenance and rejects ambiguous outcomes."""

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest
from test_forecast_run import _archive

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mlflow_evidence as evidence  # noqa: E402


def test_archive_is_deterministic_and_contains_original_reports(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "run_manifest.json").write_text('{"run_id":"original"}\n')
    (source / "signature.json").write_text('{"input":"schema"}\n')
    first = tmp_path / "first.gz"
    second = tmp_path / "second.gz"
    names = ["run_manifest.json", "signature.json"]
    assert evidence.archive_run(source, first, names) == evidence.archive_run(source, second, names)
    with tarfile.open(first, "r:gz") as archive:
        assert archive.getnames() == names
        assert archive.extractfile("signature.json").read() == b'{"input":"schema"}\n'


def test_record_keeps_export_times_and_blocks_serving(tmp_path, monkeypatch):
    staged, manifest = _archive(tmp_path)
    (staged / "config.json").write_text("{}")
    (staged / "metrics.json").write_text(
        json.dumps(
            {
                "quality_status": "not_ready",
                "gate_counts": {"passed": 1, "failed": 1, "not_ready": 1},
                "pooled_metrics": {
                    "validation:baseline": {
                        "status": "passed",
                        "wape_status": "not_evaluable",
                        "mae": 2.0,
                        "wape": None,
                    }
                },
            }
        )
    )
    handoff = {
        "original_run_id": manifest.run_id,
        "run_kind": "forecast_evidence_export",
        "quality_status": "not_ready",
        "registration_eligible": False,
        "promotion_eligible": False,
        "serving_eligible": False,
    }
    (staged / "handoff.json").write_text(json.dumps(handoff))
    monkeypatch.setattr(evidence, "verify_run", lambda _: manifest)
    meta = evidence.record(staged, 100, "a" * 64)
    assert meta["tags"]["retailops.original_run_id"] == manifest.run_id
    assert meta["tags"]["retailops.export_started_at"] == manifest.started_at.isoformat()
    assert meta["tags"]["retailops.imported_at"] != manifest.started_at.isoformat()
    assert meta["tags"]["retailops.training_executed"] == "false"
    assert meta["tags"]["retailops.serving_eligible"] == "false"
    assert meta["params"]["config_sha256"] == hashlib.sha256(b"{}").hexdigest()
    assert {m["key"] for m in meta["metrics"]} == {
        "gate_failed",
        "gate_not_ready",
        "gate_passed",
        "validation_baseline_mae",
    }
    handoff["serving_eligible"] = True
    (staged / "handoff.json").write_text(json.dumps(handoff))
    with pytest.raises(ValueError, match="handoff_mismatch"):
        evidence.record(staged, 100, "a" * 64)


def test_duplicate_original_run_is_not_silently_reimported(tmp_path, monkeypatch):
    staged, manifest = _archive(tmp_path)
    monkeypatch.setattr(evidence, "verify_run", lambda _: manifest)
    monkeypatch.setattr(evidence, "WORK", tmp_path / "work")
    monkeypatch.setattr(evidence, "archive_run", lambda *_: (100, "a" * 64))
    monkeypatch.setattr(evidence, "record", lambda *_: {"params": {}})
    monkeypatch.setattr(evidence, "experiment_id", lambda: "1")
    monkeypatch.setattr(
        evidence,
        "matching_runs",
        lambda *_: [
            {
                "info": {"status": "FAILED"},
                "data": {
                    "tags": [{"key": "retailops.import_status", "value": "failed"}],
                    "params": [],
                },
            }
        ],
    )
    with pytest.raises(ValueError, match="existing_run_conflict"):
        evidence.import_run(staged)
