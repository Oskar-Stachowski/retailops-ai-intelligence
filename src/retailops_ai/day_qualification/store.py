"""Publish sealed qualification views; verify by independent parent reconstruction."""

import hashlib
import platform
import tempfile
from collections import Counter
from datetime import timedelta
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.day_qualification.contract import (
    GRAIN,
    MAX_BYTES,
    MAX_ROWS,
    VERSION,
    Coverage,
    Policy,
)
from retailops_ai.day_qualification.gate import DayGate
from retailops_ai.day_qualification.parents import parents, read_artifact
from retailops_ai.full_raw_dq.store import Runtime as FullRuntime
from retailops_ai.full_raw_dq.store import runtime as full_runtime
from retailops_ai.raw_dq.contract import stamp
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

FILES = {"qualified_days.jsonl", "qualification_manifest.json", "manifest.sha256"}


class Runtime(Contract):
    full_dq: FullRuntime
    code_files: dict[str, Sha256]
    contract_files: dict[str, Sha256]
    python_version: str


class Descriptor(Contract):
    version: Literal["ai-business-day-qualification-1.0.0"] = "ai-business-day-qualification-1.0.0"
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    full_dq_descriptor_sha256: Sha256
    coverage: Coverage
    policy: Policy
    runtime: Runtime
    row_count: Annotated[int, Field(ge=1, le=MAX_ROWS)]
    rows_sha256: Sha256
    status_counts: dict[str, Annotated[int, Field(ge=0)]]
    model_readiness: Literal["not_qualified"] = "not_qualified"
    transport_durability_proven: Literal[False] = False


class Manifest(Contract):
    day_qualification_id: Annotated[str, Field(pattern=r"^day-qualification-sha256-[0-9a-f]{64}$")]
    descriptor: Descriptor


def runtime() -> Runtime:
    return Runtime(
        full_dq=full_runtime(),
        code_files={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files("retailops_ai.day_qualification").iterdir()
            if p.name.endswith(".py")
        },
        contract_files={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files("retailops_ai.day_qualification").joinpath("contracts").iterdir()
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
    gate = DayGate(days, replay, raw, parent)
    points = []
    for day in days:
        delay = (
            policy.sales_delay_hours
            if day.event_type == "sale_completed"
            else policy.returns_delay_hours
        )
        origin = policy.as_of or (stamp(day.window_end) + timedelta(hours=delay)).isoformat()
        points.append(
            gate.point(tuple(getattr(day, k) for k in GRAIN), origin).model_dump(mode="json")
        )
    payload = b"".join(canonical_json(p) + b"\n" for p in points)
    if len(payload) > MAX_BYTES:
        raise SnapshotError("day_qualification_output_limit")
    descriptor = Descriptor(
        full_dq_replay_id=full.full_dq_replay_id,
        full_dq_descriptor_sha256=json_sha256(full.descriptor.model_dump(mode="json")),
        coverage=coverage,
        policy=policy,
        runtime=runtime(),
        row_count=len(points),
        rows_sha256=hashlib.sha256(payload).hexdigest(),
        status_counts=dict(Counter(p["status"] for p in points)),
    )
    return Manifest(
        day_qualification_id="day-qualification-sha256-"
        + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    ), payload


def verify(
    root: Path, replay_dir: Path, coverage_dir: Path, curated_dir: Path, import_dir: Path
) -> Manifest:
    root = checked_directory(root)
    inventory(root, FILES)
    document = read_artifact(root, "qualification_manifest.json", 1024**2)
    manifest = Manifest.model_validate_json(canonical_json(decode_json(document)))
    expected, rows = material(
        replay_dir, coverage_dir, curated_dir, import_dir, manifest.descriptor.policy
    )
    if (
        document != canonical_json(expected.model_dump(mode="json")) + b"\n"
        or manifest != expected
        or read_artifact(root, "manifest.sha256", 128)
        != (hashlib.sha256(document).hexdigest() + "\n").encode()
    ):
        raise SnapshotError("day_qualification_identity_runtime_or_parent_mismatch")
    if read_artifact(root, "qualified_days.jsonl", MAX_BYTES) != rows:
        raise SnapshotError("day_qualification_semantic_mismatch")
    return manifest


def payloads(manifest: Manifest, rows: bytes) -> dict[str, bytes]:
    document = canonical_json(manifest.model_dump(mode="json")) + b"\n"
    return {
        "qualified_days.jsonl": rows,
        "qualification_manifest.json": document,
        "manifest.sha256": (hashlib.sha256(document).hexdigest() + "\n").encode(),
    }


def check_payload(root: Path, manifest: Manifest, rows: bytes) -> None:
    """Compare to material reconstructed independently in this invocation."""
    root = checked_directory(root)
    inventory(root, FILES)
    if (
        any(
            read_artifact(root, name, MAX_BYTES) != value
            for name, value in payloads(manifest, rows).items()
        )
        or runtime() != manifest.descriptor.runtime
    ):
        raise SnapshotError("day_qualification_payload_changed")


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
    target = root / "day-qualification"
    if (
        root.parts[-2:] != ("data", "generated")
        or ".." in root.parts
        or any(p.is_symlink() for p in (root, *root.parents))
        or any(root.is_relative_to(p) or p.is_relative_to(target) for p in inputs)
    ):
        raise SnapshotError("day_qualification_requires_separate_generated_root")
    manifest, rows = material(*inputs, policy or Policy())
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.mkdir(exist_ok=True, mode=0o700)
    checked_directory(target)
    destination = target / manifest.day_qualification_id
    if destination.exists():
        check_payload(destination, manifest, rows)
        status = "reused"
    else:
        with tempfile.TemporaryDirectory(prefix=".day-build-", dir=root) as tmp:
            stage = Path(tmp) / "payload"
            stage.mkdir(mode=0o700)
            contents = payloads(manifest, rows)
            for name, content in contents.items():
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
        "day_qualification_id": manifest.day_qualification_id,
        "status_counts": manifest.descriptor.status_counts,
        "model_readiness": "not_qualified",
        "version": VERSION,
    }
