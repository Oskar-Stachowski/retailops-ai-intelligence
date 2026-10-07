"""Offline preparation, exact specification bytes and no-replace publication."""

import hashlib
import os
import platform
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.contract import (
    EvaluationPreparation,
    PreparationDescriptor,
    PreparationManifest,
    PreparationRuntime,
    Repository,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    decode_json,
    directory_fd,
    read_bytes,
)
from retailops_ai.source_snapshot.protocol import resource_bytes

MAX_PREPARATION_BYTES = 64 * 1024
BLOCKERS = (
    "ai07_final_model_and_evaluation_acceptance_not_bound",
    "ai08_final_model_and_evaluation_acceptance_not_bound",
    "portfolio_source_curated_features_labels_splits_not_bound",
    "use_case_thresholds_calibrators_segment_gates_and_weights_not_bound",
    "tensorflow_environment_model_and_reload_evidence_not_bound",
    "reviewed_final_protocol_and_test_access_journal_not_implemented",
    "final_evaluation_runner_not_implemented",
)


def default_plan() -> EvaluationPreparation:
    resource = files("retailops_ai.evaluation_campaign").joinpath("preparation.default.json")
    raw = (
        resource.read_bytes()
        if resource.is_file()
        else (
            Path(__file__).absolute().parents[3]
            / "contracts/evaluation/v1/preparation.default.json"
        ).read_bytes()
    )
    return EvaluationPreparation.model_validate_json(canonical_bytes(decode_json(raw)))


def runtime_pin() -> PreparationRuntime:
    root = files("retailops_ai")
    names = (
        "evaluation_campaign/contract.py",
        "evaluation_campaign/preparation.py",
        "evaluation_campaign/comparison.py",
        "evaluation_campaign/cli.py",
        "data_contracts/common.py",
        "data_contracts/identity.py",
        "source_snapshot/files.py",
        "source_snapshot/protocol.py",
    )
    hashes = {name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() for name in names}
    return PreparationRuntime(
        code_files=hashes,
        code_sha256=canonical_sha256(hashes),
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
    )


def verify_specifications(
    plan: EvaluationPreparation, repositories: Mapping[Repository, Path]
) -> None:
    """Verify specified bytes; the checkout's current HEAD is deliberately irrelevant."""
    for pin in plan.specifications:
        if pin.repository not in repositories:
            raise SnapshotError("evaluation_specification_repository_missing")
        raw = read_bytes(repositories[pin.repository], pin.relative_path, 1024 * 1024)
        if len(raw) != pin.size_bytes or hashlib.sha256(raw).hexdigest() != pin.sha256:
            raise SnapshotError("evaluation_specification_bytes_changed")


def manifest_for(plan: EvaluationPreparation) -> PreparationManifest:
    descriptor = PreparationDescriptor(plan=plan, runtime=runtime_pin())
    return PreparationManifest(
        preparation_id="ai09-preparation-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )


def manifest_bytes(manifest: PreparationManifest) -> bytes:
    return canonical_bytes(manifest.model_dump(mode="json")) + b"\n"


def verify_preparation(path: Path, repositories: Mapping[Repository, Path]) -> PreparationManifest:
    raw = read_bytes(path.parent, path.name, MAX_PREPARATION_BYTES)
    manifest = PreparationManifest.model_validate_json(canonical_bytes(decode_json(raw)))
    expected = manifest_for(manifest.descriptor.plan)
    if manifest != expected or raw != manifest_bytes(expected):
        raise SnapshotError("evaluation_preparation_identity_or_runtime_mismatch")
    verify_specifications(manifest.descriptor.plan, repositories)
    return manifest


def prepare(
    plan: EvaluationPreparation, repositories: Mapping[Repository, Path], output_root: Path
) -> Path:
    verify_specifications(plan, repositories)
    manifest = manifest_for(plan)
    raw = manifest_bytes(manifest)
    if len(raw) > MAX_PREPARATION_BYTES:
        raise SnapshotError("evaluation_preparation_size_limit")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = manifest.preparation_id + ".json"
    temporary = ".ai09-preparation-" + uuid4().hex
    with directory_fd(output_root) as root_fd:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Re-read specifications before the artifact becomes visible.
            verify_specifications(plan, repositories)
            if read_bytes(output_root, temporary, MAX_PREPARATION_BYTES) != raw:
                raise SnapshotError("evaluation_preparation_staging_changed")
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
                os.fsync(root_fd)
            except FileExistsError:
                existing = verify_preparation(output_root / name, repositories)
                if existing != manifest:
                    raise SnapshotError("evaluation_preparation_publication_conflict") from None
        finally:
            os.unlink(temporary, dir_fd=root_fd)
    return output_root / name


def readiness(manifest: PreparationManifest) -> dict[str, object]:
    """An intact planning artifact is never an approval to score a final holdout."""
    return {
        "preparation_id": manifest.preparation_id,
        "preparation_status": "verified",
        "evaluation_status": "not_ready",
        "final_test_access_authorized": False,
        "model_promotion_authorized": False,
        "blockers": list(BLOCKERS),
    }
