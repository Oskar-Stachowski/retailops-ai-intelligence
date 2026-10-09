"""Immutable detector mechanics; verification rebuilds parents, fitting and predictions."""

import hashlib
import platform
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.anomaly_detectors.contract import (
    MAX_MODEL_BYTES,
    FitPolicy,
    ModelManifest,
    Resources,
    RunDescriptor,
    RunManifest,
    Runtime,
)
from retailops_ai.anomaly_detectors.engine import run
from retailops_ai.anomaly_detectors.protocol import Protocol
from retailops_ai.day_qualification.parents import read_artifact
from retailops_ai.qualified_anomalies.contract import MAX_BYTES, Point
from retailops_ai.qualified_anomalies.store import verify as verify_features
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    inventory,
    json_sha256,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

FILES = {
    "model.json",
    "membership.jsonl",
    "validation_scores.jsonl",
    "test_scores.jsonl",
    "run_manifest.json",
    "manifest.sha256",
}
MAX_RUN_BYTES = 32 * 1024**2


def runtime() -> Runtime:
    package = files("retailops_ai.anomaly_detectors")
    return Runtime(
        code_files={
            **{
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in package.iterdir()
                if p.name.endswith(".py")
            },
            "retailops_ai/worker_resources.py": hashlib.sha256(
                files("retailops_ai").joinpath("worker_resources.py").read_bytes()
            ).hexdigest(),
        },
        contract_files={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in package.joinpath("contracts").iterdir()
            if p.name.endswith(".json")
        },
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
    )


def material(
    feature_dir: Path,
    replay_dir: Path,
    coverage_dir: Path,
    curated_dir: Path,
    import_dir: Path,
    protocol: Protocol,
    policy: FitPolicy,
) -> tuple[RunManifest, dict[str, bytes], list[Resources]]:
    feature_manifest = verify_features(
        feature_dir, replay_dir, coverage_dir, curated_dir, import_dir
    )
    raw = read_artifact(checked_directory(feature_dir), "features.jsonl", MAX_BYTES)
    if hashlib.sha256(raw).hexdigest() != feature_manifest.descriptor.rows_sha256:
        raise SnapshotError("anomaly_parent_feature_bytes_changed")
    points = [Point.model_validate_json(line) for line in raw.splitlines()]
    model, membership, validation, test, resources, counts, predictions = run(
        feature_manifest, points, protocol, policy, runtime()
    )
    document = canonical_json(model.model_dump(mode="json")) + b"\n"
    if (
        len(document) > MAX_MODEL_BYTES
        or max(len(membership), len(validation), len(test)) > MAX_RUN_BYTES
    ):
        raise SnapshotError("anomaly_detector_artifact_budget")
    descriptor = RunDescriptor(
        detector_id=model.detector_id,
        model_manifest_sha256=hashlib.sha256(document).hexdigest(),
        membership_sha256=hashlib.sha256(membership).hexdigest(),
        validation_scores_sha256=hashlib.sha256(validation).hexdigest(),
        test_scores_sha256=hashlib.sha256(test).hexdigest(),
        requested_rows=sum(counts.values()),
        role_status_counts=counts,
        prediction_status_counts=predictions,
    )
    manifest = RunManifest(
        run_artifact_id="anomaly-detector-run-sha256-"
        + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    run_json = canonical_json(manifest.model_dump(mode="json")) + b"\n"
    return (
        manifest,
        {
            "model.json": document,
            "membership.jsonl": membership,
            "validation_scores.jsonl": validation,
            "test_scores.jsonl": test,
            "run_manifest.json": run_json,
            "manifest.sha256": (hashlib.sha256(run_json).hexdigest() + "\n").encode(),
        },
        resources,
    )


def check_payload(root: Path, payloads: dict[str, bytes]) -> None:
    root = checked_directory(root)
    inventory(root, FILES)
    if any(
        read_artifact(root, name, MAX_RUN_BYTES) != expected for name, expected in payloads.items()
    ):
        raise SnapshotError("anomaly_detector_payload_changed")


def verify(
    root: Path,
    feature_dir: Path,
    replay_dir: Path,
    coverage_dir: Path,
    curated_dir: Path,
    import_dir: Path,
) -> RunManifest:
    root = checked_directory(root)
    inventory(root, FILES)
    model_json = read_artifact(root, "model.json", MAX_MODEL_BYTES)
    model = ModelManifest.model_validate_json(model_json)
    run_json = read_artifact(root, "run_manifest.json", 1024**2)
    manifest = RunManifest.model_validate_json(run_json)
    descriptor = manifest.descriptor
    if (
        model_json != canonical_json(model.model_dump(mode="json")) + b"\n"
        or run_json != canonical_json(manifest.model_dump(mode="json")) + b"\n"
        or model.detector_id
        != "anomaly-detector-sha256-" + json_sha256(model.descriptor.model_dump(mode="json"))
        or manifest.run_artifact_id
        != "anomaly-detector-run-sha256-" + json_sha256(descriptor.model_dump(mode="json"))
        or descriptor.detector_id != model.detector_id
        or descriptor.model_manifest_sha256 != hashlib.sha256(model_json).hexdigest()
        or model.descriptor.runtime != runtime()
        or read_artifact(root, "manifest.sha256", 128)
        != (hashlib.sha256(run_json).hexdigest() + "\n").encode()
        or any(
            hashlib.sha256(read_artifact(root, name, MAX_RUN_BYTES)).hexdigest() != expected
            for name, expected in (
                ("membership.jsonl", descriptor.membership_sha256),
                ("validation_scores.jsonl", descriptor.validation_scores_sha256),
                ("test_scores.jsonl", descriptor.test_scores_sha256),
            )
        )
    ):
        raise SnapshotError("anomaly_detector_identity_runtime_or_seal_mismatch")
    expected, payloads, _ = material(
        feature_dir,
        replay_dir,
        coverage_dir,
        curated_dir,
        import_dir,
        model.descriptor.protocol,
        model.descriptor.policy,
    )
    if expected != manifest:
        raise SnapshotError("anomaly_detector_reconstruction_mismatch")
    check_payload(root, payloads)
    return expected


def build(
    feature_dir: Path,
    replay_dir: Path,
    coverage_dir: Path,
    curated_dir: Path,
    import_dir: Path,
    generated_root: Path,
    protocol: Protocol,
    policy: FitPolicy | None = None,
) -> dict[str, Any]:
    inputs = (
        checked_directory(feature_dir),
        checked_directory(replay_dir),
        checked_directory(coverage_dir),
        checked_directory(curated_dir),
        checked_directory(import_dir),
    )
    root = generated_root.absolute()
    target = root / "anomaly-detectors"
    if (
        root.parts[-2:] != ("data", "generated")
        or ".." in root.parts
        or any(p.is_symlink() for p in (root, *root.parents))
        or any(root.is_relative_to(p) or p.is_relative_to(target) for p in inputs)
    ):
        raise SnapshotError("anomaly_detector_requires_separate_generated_root")
    manifest, payloads, resources = material(*inputs, protocol, policy or FitPolicy())
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    checked_directory(target)
    destination = target / manifest.run_artifact_id
    status = "reused"
    if destination.exists():
        check_payload(destination, payloads)
    else:
        with tempfile.TemporaryDirectory(prefix=".anomaly-detector-build-", dir=root) as tmp:
            stage = Path(tmp) / "payload"
            stage.mkdir(mode=0o700)
            for name, raw in payloads.items():
                write_private(stage / name, raw)
            check_payload(stage, payloads)
            fsync_tree(stage)
            try:
                publish_noreplace(stage, destination)
                status = "created"
            except FileExistsError:
                check_payload(destination, payloads)
    return {
        "status": status,
        "directory": str(destination),
        "run_artifact_id": manifest.run_artifact_id,
        "detector_id": manifest.descriptor.detector_id,
        "role_status_counts": manifest.descriptor.role_status_counts,
        "prediction_status_counts": manifest.descriptor.prediction_status_counts,
        "fit_resources": [r.model_dump(mode="json") for r in resources],
        "detector_readiness": "not_qualified",
        "model_quality": "not_evaluated",
    }
