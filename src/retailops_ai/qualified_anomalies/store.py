"""Sealed residual inputs verified against receipt/closure parents, never final outcomes."""

import hashlib
import platform
import tempfile
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.anomalies.store import Implementation, implementation, parent_inputs
from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.day_qualification.contract import Coverage
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.day_qualification.parents import parents, read_artifact
from retailops_ai.day_qualification.store import Runtime as QualificationRuntime
from retailops_ai.day_qualification.store import runtime as qualification_runtime
from retailops_ai.qualified_anomalies.contract import (
    MAX_BYTES,
    MAX_ROWS,
    MODEL_FEATURES,
    ModelFeatures,
    Policy,
)
from retailops_ai.qualified_anomalies.features import Features
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    inventory,
    json_sha256,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

FILES = {"features.jsonl", "feature_manifest.json", "manifest.sha256"}


class Runtime(Contract):
    day_qualification: QualificationRuntime
    context: Implementation
    code_files: dict[str, Sha256]
    contract_files: dict[str, Sha256]
    python_version: str


class Descriptor(Contract):
    version: Literal["qualified-anomaly-inputs-1.0.0"] = "qualified-anomaly-inputs-1.0.0"
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    full_dq_descriptor_sha256: Sha256
    coverage: Coverage
    policy: Policy
    runtime: Runtime
    row_count: Annotated[int, Field(ge=1, le=MAX_ROWS)]
    rows_sha256: Sha256
    status_counts: dict[str, Annotated[int, Field(ge=0)]]
    model_feature_allowlist: ModelFeatures = MODEL_FEATURES
    return_scope: Literal["purchases_in_parent_source_only"] = "purchases_in_parent_source_only"
    evaluation_truth: Literal["excluded"] = "excluded"
    detector_readiness: Literal["not_qualified"] = "not_qualified"


class Manifest(Contract):
    qualified_anomaly_input_id: Annotated[
        str, Field(pattern=r"^qualified-anomaly-inputs-sha256-[0-9a-f]{64}$")
    ]
    descriptor: Descriptor


def runtime() -> Runtime:
    return Runtime(
        day_qualification=qualification_runtime(),
        context=implementation(),
        code_files={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files("retailops_ai.qualified_anomalies").iterdir()
            if p.name.endswith(".py")
        },
        contract_files={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files("retailops_ai.qualified_anomalies").joinpath("contracts").iterdir()
            if p.name.endswith(".json")
        },
        python_version=platform.python_version(),
    )


def material(
    replay_dir: Path, coverage_dir: Path, curated_dir: Path, import_dir: Path, policy: Policy
) -> tuple[Manifest, bytes]:
    full, coverage, days, replay, raw, parent = parents(
        replay_dir, coverage_dir, curated_dir, import_dir
    )
    context_parent, tables = parent_inputs(curated_dir)
    if (
        context_parent.curated_dataset_id != full.descriptor.parent.curated_dataset_id
        or context_parent.curated_descriptor_sha256
        != full.descriptor.parent.curated_descriptor_sha256
    ):
        raise SnapshotError("qualified_anomaly_context_parent_changed")
    features = Features(DayGate(days, replay, raw, parent), tables, policy)
    payload = bytearray()
    counts: Counter[str] = Counter()
    for point in features.rows():
        payload.extend(canonical_json(point.model_dump(mode="json")) + b"\n")
        counts[point.status] += 1
        if len(payload) > MAX_BYTES or sum(counts.values()) > MAX_ROWS:
            raise SnapshotError("qualified_anomaly_output_limit")
    rows = bytes(payload)
    descriptor = Descriptor(
        full_dq_replay_id=full.full_dq_replay_id,
        full_dq_descriptor_sha256=json_sha256(full.descriptor.model_dump(mode="json")),
        coverage=coverage,
        policy=policy,
        runtime=runtime(),
        row_count=sum(counts.values()),
        rows_sha256=hashlib.sha256(rows).hexdigest(),
        status_counts=dict(counts),
    )
    return Manifest(
        qualified_anomaly_input_id="qualified-anomaly-inputs-sha256-"
        + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    ), rows


def payloads(manifest: Manifest, rows: bytes) -> dict[str, bytes]:
    document = canonical_json(manifest.model_dump(mode="json")) + b"\n"
    return {
        "features.jsonl": rows,
        "feature_manifest.json": document,
        "manifest.sha256": (hashlib.sha256(document).hexdigest() + "\n").encode(),
    }


def check_payload(root: Path, manifest: Manifest, rows: bytes) -> None:
    root = checked_directory(root)
    inventory(root, FILES)
    if (
        any(
            read_artifact(root, name, MAX_BYTES) != value
            for name, value in payloads(manifest, rows).items()
        )
        or runtime() != manifest.descriptor.runtime
    ):
        raise SnapshotError("qualified_anomaly_payload_changed")


def verify(
    root: Path, replay_dir: Path, coverage_dir: Path, curated_dir: Path, import_dir: Path
) -> Manifest:
    root = checked_directory(root)
    inventory(root, FILES)
    document = read_artifact(root, "feature_manifest.json", 1024**2)
    manifest = Manifest.model_validate_json(canonical_json(decode_json(document)))
    artifact_rows = read_artifact(root, "features.jsonl", MAX_BYTES)
    if (
        document != canonical_json(manifest.model_dump(mode="json")) + b"\n"
        or read_artifact(root, "manifest.sha256", 128)
        != (hashlib.sha256(document).hexdigest() + "\n").encode()
        or manifest.qualified_anomaly_input_id
        != "qualified-anomaly-inputs-sha256-"
        + json_sha256(manifest.descriptor.model_dump(mode="json"))
        or hashlib.sha256(artifact_rows).hexdigest() != manifest.descriptor.rows_sha256
    ):
        raise SnapshotError("qualified_anomaly_identity_or_seal_mismatch")
    expected, rows = material(
        replay_dir, coverage_dir, curated_dir, import_dir, manifest.descriptor.policy
    )
    if manifest != expected:
        raise SnapshotError("qualified_anomaly_identity_runtime_or_parent_mismatch")
    check_payload(root, expected, rows)
    return expected


def build(
    replay_dir: Path,
    coverage_dir: Path,
    curated_dir: Path,
    import_dir: Path,
    generated_root: Path,
    policy: Policy | None = None,
) -> dict[str, Any]:
    inputs = (
        checked_directory(replay_dir),
        checked_directory(coverage_dir),
        checked_directory(curated_dir),
        checked_directory(import_dir),
    )
    root = generated_root.absolute()
    target = root / "qualified-anomaly-inputs"
    if (
        root.parts[-2:] != ("data", "generated")
        or ".." in root.parts
        or any(p.is_symlink() for p in (root, *root.parents))
        or any(root.is_relative_to(p) or p.is_relative_to(target) for p in inputs)
    ):
        raise SnapshotError("qualified_anomaly_requires_separate_generated_root")
    manifest, rows = material(*inputs, policy or Policy())
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    checked_directory(target)
    destination = target / manifest.qualified_anomaly_input_id
    if destination.exists():
        check_payload(destination, manifest, rows)
        status = "reused"
    else:
        with tempfile.TemporaryDirectory(prefix=".qualified-feature-build-", dir=root) as tmp:
            stage = Path(tmp) / "payload"
            stage.mkdir(mode=0o700)
            for name, content in payloads(manifest, rows).items():
                write_private(stage / name, content)
            check_payload(stage, manifest, rows)
            fsync_tree(stage)
            try:
                publish_noreplace(stage, destination)
                status = "created"
            except FileExistsError:
                check_payload(destination, manifest, rows)
                status = "reused"
    return {
        "status": status,
        "directory": str(destination),
        "qualified_anomaly_input_id": manifest.qualified_anomaly_input_id,
        "status_counts": manifest.descriptor.status_counts,
        "detector_readiness": "not_qualified",
    }
