"""The evidence export cannot become a training/serving run by changing metadata."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from retailops_ai.forecasting.run import FileArtifactSink, load_run
from retailops_ai.forecasting.run_contract import (
    ArtifactReceipt,
    ForecastRunDescriptor,
    ForecastRunManifest,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


def _archive(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    staged = tmp_path / ".run-stage"
    staged.mkdir()
    for part in ("source", "curated", "features", "backtest", "quality"):
        directory = staged / part
        directory.mkdir()
        (directory / "evidence.json").write_text('{"value":1}\n')
    for report in ("config", "metrics", "model_card", "handoff", "signature", "input_example"):
        (staged / (report + ".json")).write_text('{"value":1}\n')
    descriptor = ForecastRunDescriptor(
        source_dataset_id="source",
        snapshot_id="snapshot",
        curated_dataset_id="curated",
        feature_set_id="features",
        label_dataset_id="labels",
        split_id="split",
        comparison_id="comparison",
        backtest_id="backtest",
        quality_id="quality",
        source_code_commit="a" * 40,
        ai_code_commit="b" * 40,
        dependency_lock_sha256="c" * 64,
        data_seed=42,
        model_seed=42,
        quality_status="not_ready",
        gate_counts={"passed": 1, "failed": 1, "not_ready": 1},
    )
    now = datetime.now(UTC)
    receipts = {
        file.relative_to(staged).as_posix(): ArtifactReceipt(
            size_bytes=file.stat().st_size,
            sha256=file_hash(staged, file.relative_to(staged).as_posix())[1],
        )
        for file in staged.rglob("*")
        if file.is_file()
    }
    from retailops_ai.data_contracts.identity import canonical_sha256

    manifest = ForecastRunManifest(
        run_id="run-" + canonical_sha256(descriptor.model_dump(mode="json"))[:32],
        descriptor=descriptor,
        started_at=now,
        completed_at=now,
        receipts=receipts,
    )
    (staged / "run_manifest.json").write_text(json.dumps(manifest.model_dump(mode="json")))
    return staged, manifest


def test_export_receipts_immutable_and_tamper_detected(tmp_path):
    staged, manifest = _archive(tmp_path)
    sink = FileArtifactSink(tmp_path / "published")
    sink.output_root.mkdir()
    destination = sink.publish(staged, manifest)
    assert load_run(destination) == manifest
    second, same = _archive(tmp_path / "second")
    assert sink.publish(second, same) == destination
    (destination / "backtest/evidence.json").write_text('{"value":2}\n')
    with pytest.raises(SnapshotError, match="checksum"):
        load_run(destination)
    with pytest.raises(SnapshotError, match="checksum"):
        sink.publish(second, same)


def test_export_does_not_claim_training_or_promotion(tmp_path):
    _, manifest = _archive(tmp_path)
    assert manifest.descriptor.training_run_id is None
    assert manifest.training_executed_in_this_run is False
    for change in (
        {"training_executed_in_this_run": True},
        {"serving_alias": "champion"},
        {"mlflow_run_id": "fabricated"},
        {"forecast_model_status": "ready"},
        {"completed_at": manifest.started_at - timedelta(seconds=1)},
    ):
        with pytest.raises(ValidationError):
            ForecastRunManifest.model_validate({**manifest.model_dump(), **change})
