"""Portable, content-addressed AI 04.8 evidence sink for the AI 05 handoff."""

from __future__ import annotations

import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from retailops_ai.curated.builder import verify_curated
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.backtest import load_backtest
from retailops_ai.forecasting.backtest_contract import BacktestManifest
from retailops_ai.forecasting.manifest_contract import FeatureManifest
from retailops_ai.forecasting.manifests import input_models, verify_feature_set
from retailops_ai.forecasting.models import load_comparison
from retailops_ai.forecasting.quality import load_quality
from retailops_ai.forecasting.quality_contract import QualityManifest
from retailops_ai.forecasting.run_contract import (
    ArtifactReceipt,
    ForecastRunDescriptor,
    ForecastRunManifest,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    file_hash,
    inventory,
    read_bytes,
    read_json,
    regular_file,
    relative_path,
)
from retailops_ai.source_snapshot.importer import verify_import
from retailops_ai.source_snapshot.protocol import Snapshot
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

PARTS = ("source", "curated", "features", "backtest", "quality")
REPORTS = (
    "config.json",
    "metrics.json",
    "model_card.json",
    "handoff.json",
    "signature.json",
    "input_example.json",
)
MAX_ARCHIVE_BYTES = 1024**3
MAX_ARCHIVE_FILES = 1000
Parents = tuple[Snapshot, dict[str, Any], FeatureManifest, BacktestManifest, QualityManifest]


class ArtifactSink(Protocol):
    def publish(self, staged: Path, manifest: ForecastRunManifest) -> Path: ...


class FileArtifactSink:
    """Atomic no-replace publication; an existing run is accepted only if all bytes agree."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.absolute()

    def publish(self, staged: Path, manifest: ForecastRunManifest) -> Path:
        destination = self.output_root / manifest.run_id
        try:
            publish_noreplace(staged, destination)
        except FileExistsError:
            previous = load_run(destination)
            if previous.descriptor != manifest.descriptor or previous.receipts != manifest.receipts:
                raise SnapshotError("run_immutable_publication_conflict") from None
        return destination


def _paths(root: Path) -> tuple[str, ...]:
    checked_directory(root)
    names: list[str] = []
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise SnapshotError("run_symlink_or_special_artifact")
            if stat.S_ISREG(mode):
                names.append(path.relative_to(root).as_posix())
    if len(names) > MAX_ARCHIVE_FILES:
        raise SnapshotError("run_file_limit")
    return tuple(sorted(names))


def _copy_file(source: Path, target: Path, name: str) -> ArtifactReceipt:
    relative_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    with regular_file(source, name) as stream, target.open("xb") as output:
        while block := stream.read(1024**2):
            output.write(block)
    source_receipt = file_hash(source, name)
    copied_receipt = file_hash(target.parent, target.name)
    # The target receipt is checked again against the final archive path below.
    if source_receipt != copied_receipt:
        raise SnapshotError("run_source_changed_during_copy")
    return ArtifactReceipt(size_bytes=source_receipt[0], sha256=source_receipt[1])


def _parents(source: Path, curated: Path, features: Path, backtest: Path, quality: Path) -> Parents:
    source_manifest = verify_import(source)
    curated_manifest = verify_curated(curated)
    feature_manifest = verify_feature_set(features)
    backtest_manifest = load_backtest(backtest)
    quality_manifest = load_quality(quality)
    b = backtest_manifest.descriptor
    q = quality_manifest.descriptor
    p = b.parent
    if (
        p.source_dataset_id != source_manifest.source_id
        or p.snapshot_id != source_manifest.snapshot_id
        or p.curated_dataset_id != curated_manifest["curated_dataset_id"]
        or feature_manifest.feature_set_id != b.feature_set_id
        or feature_manifest.descriptor.parent != p
        or q.backtest_id != backtest_manifest.backtest_id
        or q.backtest_descriptor_sha256 != canonical_sha256(b.model_dump(mode="json"))
        or q.feature_set_id != feature_manifest.feature_set_id
    ):
        raise SnapshotError("run_parent_lineage_mismatch")
    return source_manifest, curated_manifest, feature_manifest, backtest_manifest, quality_manifest


def _descriptor(
    parent: Parents, source: Path, curated: Path, ai_commit: str
) -> ForecastRunDescriptor:
    source_manifest, _, features, backtest, quality = parent
    b, q = backtest.descriptor, quality.descriptor
    snapshot = read_json(source / "snapshot", "snapshot_manifest.json")
    curated_raw = read_json(curated, "curated_manifest.json")
    return ForecastRunDescriptor(
        source_dataset_id=source_manifest.source_id,
        snapshot_id=source_manifest.snapshot_id,
        curated_dataset_id=b.parent.curated_dataset_id,
        feature_set_id=features.feature_set_id,
        label_dataset_id=b.label_dataset_id,
        split_id=b.split_id,
        comparison_id=b.comparison_id,
        backtest_id=backtest.backtest_id,
        quality_id=quality.quality_id,
        source_code_commit=snapshot["exporter"]["git_commit"],
        ai_code_commit=ai_commit,
        dependency_lock_sha256=q.dependency_lock_sha256,
        data_seed=curated_raw["descriptor"]["source_parameters"]["seed"],
        model_seed=b.policy.model.random_state,
        quality_status=q.quality_status,
        gate_counts={str(k): v for k, v in q.gate_counts.items()},
    )


def _reports(
    parent: Parents, descriptor: ForecastRunDescriptor, feature_dir: Path, backtest_dir: Path
) -> dict[str, Any]:
    _, _, features, backtest, quality = parent
    comparison = load_comparison(backtest_dir / "comparison")
    b, q = backtest.descriptor, quality.descriptor
    return {
        "config.json": {
            "task": "daily_observed_sales_forecast",
            "origin_window": b.origin_window.model_dump(mode="json"),
            "feature_types": features.descriptor.feature_types,
            "split": b.resolved_split.model_dump(mode="json"),
            "backtest_policy": b.policy.model_dump(mode="json"),
            "quality_policy": q.policy.model_dump(mode="json"),
            "code_pins": {
                "feature": features.descriptor.code.model_dump(mode="json"),
                "model": b.code.model.model_dump(mode="json"),
                "backtest": b.code.model_dump(mode="json"),
                "quality": q.code_files,
            },
        },
        "metrics.json": {
            "pooled_metrics": {k: v.model_dump(mode="json") for k, v in b.pooled_metrics.items()},
            "fold_selections": [
                x.model_dump(mode="json") for x in comparison.descriptor.selections
            ],
            "fit_resources": {
                k: v.model_dump(mode="json") for k, v in comparison.resources.items()
            },
            "full_segments": "quality/segments.json",
            "full_gates": "quality/gates.json",
            "gate_counts": q.gate_counts,
            "quality_status": q.quality_status,
        },
        "model_card.json": {
            "task": descriptor.task,
            "intended_use": "development_diagnostics_only",
            "limitations": [
                "quality_gate_not_ready_for_serving",
                "portfolio_final_test_not_opened",
                "historical_training_timestamps_not_available",
            ],
            "original_card": "backtest/model_card.json",
            "quality_card": "quality/model_card.json",
            "decision": "no_registration_no_promotion_no_serving",
        },
        "handoff.json": {
            "destination_stage": "AI_05_MLflow_lifecycle",
            "run_kind": "forecast_evidence_export",
            "original_run_id": "run-" + canonical_sha256(descriptor.model_dump(mode="json"))[:32],
            "preserve_original_run_id_and_export_timestamps": True,
            "imported_at": None,
            "training_run_id": None,
            "mlflow_run_id": None,
            "training_executed_in_this_run": False,
            "do_not_claim_historical_training_occurred_in_mlflow": True,
            "registration_eligible": False,
            "promotion_eligible": False,
            "serving_eligible": False,
            "quality_status": q.quality_status,
            "ai01_run_record_compatible": False,
        },
        "signature.json": {
            "input_schema": features.descriptor.feature_types,
            "horizon_days": list(range(1, 15)),
            "target_type": "observed_sales_units",
            "example": "input_example.json",
            "pipeline_manifest": "backtest/comparison/comparison_manifest.json",
            "prediction_table": "backtest/comparison/predictions.jsonl",
            "interval_predictions": "quality/intervals.jsonl",
        },
        "input_example.json": {
            "row": next(input_models(feature_dir, "features")).model_dump(mode="json"),
            "use": "schema_example_only_no_training_or_inference",
        },
    }


def _write_report(root: Path, name: str, value: object) -> None:
    (root / name).write_bytes(canonical_bytes(value) + b"\n")


def build_run(
    source: Path,
    curated: Path,
    features: Path,
    backtest: Path,
    quality: Path,
    output_root: Path,
    ai_commit: str,
    sink: ArtifactSink | None = None,
) -> Path:
    inputs = dict(zip(PARTS, (source, curated, features, backtest, quality), strict=True))
    output_root = output_root.absolute()
    if any(output_root.is_relative_to(path.absolute()) for path in inputs.values()):
        raise SnapshotError("run_output_inside_input")
    parent = _parents(source, curated, features, backtest, quality)
    descriptor = _descriptor(parent, source, curated, ai_commit)
    run_id = "run-" + canonical_sha256(descriptor.model_dump(mode="json"))[:32]
    expected_reports = _reports(parent, descriptor, features, backtest)
    output_root.mkdir(parents=True, exist_ok=True)
    checked_directory(output_root)
    started = datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix=".run-", dir=output_root) as temporary:
        root = Path(temporary)
        for part, input_root in inputs.items():
            (root / part).mkdir()
            for name in _paths(input_root):
                _copy_file(input_root, root / part / name, name)
        for name, value in expected_reports.items():
            _write_report(root, name, value)
        names = [name for name in _paths(root) if name != "run_manifest.json"]
        if len(names) > MAX_ARCHIVE_FILES:
            raise SnapshotError("run_file_limit")
        receipts = {}
        total = 0
        for name in names:
            size, digest = file_hash(root, name)
            total += size
            if total > MAX_ARCHIVE_BYTES:
                raise SnapshotError("run_byte_limit")
            receipts[name] = ArtifactReceipt(size_bytes=size, sha256=digest)
        manifest = ForecastRunManifest(
            run_id=run_id,
            descriptor=descriptor,
            started_at=started,
            completed_at=datetime.now(UTC),
            receipts=receipts,
        )
        _write_report(root, "run_manifest.json", manifest.model_dump(mode="json"))
        load_run(root)
        fsync_tree(root)
        return (sink or FileArtifactSink(output_root)).publish(root, manifest)


def load_run(root: Path) -> ForecastRunManifest:
    checked_directory(root)
    raw = read_bytes(root, "run_manifest.json")
    decode_json(raw)
    manifest = ForecastRunManifest.model_validate_json(raw)
    if root.name not in {manifest.run_id} and not root.name.startswith(".run-"):
        raise SnapshotError("run_directory_id_mismatch")
    names = set(manifest.receipts) | {"run_manifest.json"}
    if not all(any(name.startswith(part + "/") for name in manifest.receipts) for part in PARTS):
        raise SnapshotError("run_missing_parent_archive")
    if not set(REPORTS).issubset(names):
        raise SnapshotError("run_missing_report")
    inventory(root, names)
    if sum(r.size_bytes for r in manifest.receipts.values()) > MAX_ARCHIVE_BYTES:
        raise SnapshotError("run_byte_limit")
    for name, receipt in manifest.receipts.items():
        relative_path(name)
        if file_hash(root, name) != (receipt.size_bytes, receipt.sha256):
            raise SnapshotError("run_artifact_checksum_mismatch")
    return manifest


def verify_run(root: Path) -> ForecastRunManifest:
    manifest = load_run(root)
    parent = _parents(*(root / part for part in PARTS))
    descriptor = _descriptor(
        parent, root / "source", root / "curated", manifest.descriptor.ai_code_commit
    )
    if descriptor != manifest.descriptor:
        raise SnapshotError("run_parent_descriptor_mismatch")
    expected = _reports(parent, descriptor, root / "features", root / "backtest")
    if any(
        read_bytes(root, name) != canonical_bytes(value) + b"\n" for name, value in expected.items()
    ):
        raise SnapshotError("run_report_replay_mismatch")
    return manifest
