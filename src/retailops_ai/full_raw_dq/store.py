"""Content-addressed offline DQ publication and full operational replay verification."""

import hashlib
import json
import platform
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from retailops_ai.anomalies.store import Parent
from retailops_ai.data_contracts.common import Contract, Sha256
from retailops_ai.full_raw_dq.contract import (
    MAX_BYTES,
    MAX_RECORDS,
    Binding,
    PortfolioBinding,
    contract_bytes,
    parse_binding,
)
from retailops_ai.full_raw_dq.replay import Replay
from retailops_ai.full_raw_dq.source import ParentFacts, parent_facts
from retailops_ai.raw_dq.contract import contract_bytes as wire_contract_bytes
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    decode_json,
    inventory,
    json_sha256,
    read_bytes,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

FILES = {"raw/events.jsonl", "source_binding.json"}


class Runtime(Contract):
    version: Literal["ai-full-parent-replay-2.0.0"]
    code_files: dict[str, Sha256]
    contract_files: dict[str, Sha256]
    dependency_lock_sha256: Sha256
    python_version: str


class Descriptor(Contract):
    schema_version: Literal["2.0.0", "2.1.0"] = "2.0.0"
    parent: Parent
    source_binding: Binding | PortfolioBinding
    raw_sha256: Sha256
    binding_sha256: Sha256
    runtime: Runtime
    replay_sha256: Sha256
    input_records: Annotated[int, Field(ge=1, le=MAX_RECORDS)]
    scope: Literal["all_parent_sales_and_return_claims"] = "all_parent_sales_and_return_claims"
    evaluation_truth: Literal["excluded"] = "excluded"
    curated_completeness: Literal["not_qualified"] = "not_qualified"
    model_readiness: Literal["not_qualified"] = "not_qualified"
    transport_durability_proven: Literal[False] = False

    @model_validator(mode="after")
    def capacity_version(self) -> "Descriptor":
        expected = "2.1.0" if isinstance(self.source_binding, PortfolioBinding) else "2.0.0"
        if self.schema_version != expected:
            raise ValueError("full_dq_capacity_binding_version")
        return self


class Manifest(Contract):
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    descriptor: Descriptor


def runtime() -> Runtime:
    codes = {}
    for package in ("full_raw_dq", "raw_dq", "anomalies", "curated", "source_snapshot"):
        for item in sorted(files("retailops_ai." + package).iterdir(), key=lambda p: p.name):
            if item.name.endswith(".py"):
                codes[package + "/" + item.name] = hashlib.sha256(item.read_bytes()).hexdigest()
    return Runtime(
        version="ai-full-parent-replay-2.0.0",
        code_files=codes,
        contract_files={
            **{
                name: hashlib.sha256(contract_bytes(name)).hexdigest()
                for name in (
                    "producer_capture.schema.json",
                    "producer_binding.schema.json",
                    "binding.schema.json",
                    "manifest.schema.json",
                    "producer_binding_v21.schema.json",
                    "binding_v21.schema.json",
                    "manifest_v21.schema.json",
                )
            },
            **{
                "v1/" + name: hashlib.sha256(wire_contract_bytes(name)).hexdigest()
                for name in (
                    "realtime-events.schema.json",
                    "realtime-events.contract.json",
                )
            },
        },
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
    )


def replay_id(descriptor: Descriptor) -> str:
    return "full-dq-replay-sha256-" + json_sha256(descriptor.model_dump(mode="json"))


def replay_capture(raw: bytes, expected: ParentFacts) -> Replay:
    if len(raw) > MAX_BYTES:
        raise SnapshotError("full_dq_capture_size_limit")
    replay = Replay(expected)
    lines = raw.splitlines(keepends=True)
    if not 1 <= len(lines) <= MAX_RECORDS:
        raise SnapshotError("full_dq_capture_record_limit")
    for line in lines:
        if len(line) > 128 * 1024:
            raise SnapshotError("full_dq_capture_line_limit")
        value = decode_json(line)
        if line != canonical_json(value) + b"\n":
            raise SnapshotError("full_dq_noncanonical_capture_line")
        replay.consume(value)
    # File-level duplicates would hide capture positions/counts; per-record replay
    # remains idempotent, while a persisted capture has one immutable receipt each.
    if len(replay.receipts) != len(lines) or replay.next_offset < 1:
        raise SnapshotError("full_dq_duplicate_capture_record_or_empty_events")
    return replay


def material(
    root: Path, curated_dir: Path, import_dir: Path
) -> tuple[Descriptor, bytes, bytes, bytes]:
    binding_raw = read_bytes(root, "source_binding.json", 65536)
    binding = parse_binding(canonical_json(decode_json(binding_raw)))
    if binding_raw != canonical_json(binding.model_dump(mode="json")) + b"\n":
        raise SnapshotError("full_dq_noncanonical_source_binding")
    parent, facts = parent_facts(curated_dir, import_dir, binding)
    raw = read_bytes(root, "raw/events.jsonl", MAX_BYTES)
    replay = replay_capture(raw, facts)
    replay_raw = canonical_json(replay.snapshot()) + b"\n"
    if len(replay_raw) > MAX_BYTES:
        raise SnapshotError("full_dq_replay_output_limit")
    return (
        Descriptor(
            schema_version="2.1.0" if isinstance(binding, PortfolioBinding) else "2.0.0",
            parent=Parent.model_validate(parent),
            source_binding=binding,
            raw_sha256=hashlib.sha256(raw).hexdigest(),
            binding_sha256=hashlib.sha256(binding_raw).hexdigest(),
            runtime=runtime(),
            replay_sha256=hashlib.sha256(replay_raw).hexdigest(),
            input_records=len(replay.receipts),
        ),
        raw,
        binding_raw,
        replay_raw,
    )


def verify_replay(root: Path, curated_dir: Path, import_dir: Path) -> Manifest:
    root = checked_directory(root)
    inventory(root, FILES | {"dq_manifest.json", "manifest.sha256", "replay.json"})
    raw = read_bytes(root, "dq_manifest.json", 1024**2)
    manifest = Manifest.model_validate_json(canonical_json(decode_json(raw)))
    if (
        raw != canonical_json(manifest.model_dump(mode="json")) + b"\n"
        or read_bytes(root, "manifest.sha256", 128)
        != (hashlib.sha256(raw).hexdigest() + "\n").encode()
    ):
        raise SnapshotError("full_dq_manifest_checksum_or_canonicalization_mismatch")
    descriptor, _, _, replay_raw = material(root, curated_dir, import_dir)
    if manifest.descriptor != descriptor or manifest.full_dq_replay_id != replay_id(descriptor):
        raise SnapshotError("full_dq_identity_runtime_or_parent_mismatch")
    if read_bytes(root, "replay.json", MAX_BYTES) != replay_raw:
        raise SnapshotError("full_dq_semantic_replay_mismatch")
    return manifest


def check_staged_payload(
    root: Path, manifest: Manifest, raw: bytes, binding_raw: bytes, replay_raw: bytes
) -> None:
    """Check staged bytes against the independently reconstructed build material.

    The builder owns this stage and the expected bytes. Public verification
    independently reconstructs the parent and replay in verify_replay.
    """
    root = checked_directory(root)
    inventory(root, FILES | {"dq_manifest.json", "manifest.sha256", "replay.json"})
    document = canonical_json(manifest.model_dump(mode="json")) + b"\n"
    for name, expected, limit in (
        ("raw/events.jsonl", raw, MAX_BYTES),
        ("source_binding.json", binding_raw, 65536),
        ("replay.json", replay_raw, MAX_BYTES),
        ("dq_manifest.json", document, 1024**2),
        ("manifest.sha256", (hashlib.sha256(document).hexdigest() + "\n").encode(), 128),
    ):
        if read_bytes(root, name, limit) != expected:
            raise SnapshotError("full_dq_staged_payload_changed")
    if runtime() != manifest.descriptor.runtime:
        raise SnapshotError("full_dq_runtime_changed_during_build")


def build_replay(
    capture_dir: Path, curated_dir: Path, import_dir: Path, generated_root: Path
) -> dict[str, Any]:
    capture_dir, curated_dir = checked_directory(capture_dir), checked_directory(curated_dir)
    inventory(capture_dir, FILES)
    import_dir = checked_directory(import_dir)
    root = generated_root.absolute()
    target = root / "full-dq-replay"
    if (
        ".." in root.parts
        or root.parts[-2:] != ("data", "generated")
        or root.is_relative_to(capture_dir)
        or root.is_relative_to(curated_dir)
        or root.is_relative_to(import_dir)
        or import_dir.is_relative_to(target)
        or capture_dir.is_relative_to(target)
        or curated_dir.is_relative_to(target)
        or any(p.is_symlink() for p in (root, *root.parents))
    ):
        raise SnapshotError("full_dq_requires_separate_data_generated_root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(root)
    target.mkdir(exist_ok=True, mode=0o700)
    checked_directory(target)
    descriptor, raw, binding_raw, replay_raw = material(capture_dir, curated_dir, import_dir)
    manifest = Manifest(full_dq_replay_id=replay_id(descriptor), descriptor=descriptor)
    destination = target / manifest.full_dq_replay_id
    if destination.exists():
        if verify_replay(destination, curated_dir, import_dir) != manifest:
            raise SnapshotError("immutable_full_dq_replay_conflict")
        return build_result("reused", destination, manifest, replay_raw)
    with tempfile.TemporaryDirectory(prefix=".dq-build-", dir=root) as tmp:
        stage = Path(tmp) / "payload"
        stage.mkdir(mode=0o700)
        for path, value in (
            ("raw/events.jsonl", raw),
            ("source_binding.json", binding_raw),
            ("replay.json", replay_raw),
        ):
            (stage / path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_private(stage / path, value)
        document = canonical_json(manifest.model_dump(mode="json")) + b"\n"
        write_private(stage / "dq_manifest.json", document)
        write_private(
            stage / "manifest.sha256", (hashlib.sha256(document).hexdigest() + "\n").encode()
        )
        check_staged_payload(stage, manifest, raw, binding_raw, replay_raw)
        fsync_tree(stage)
        try:
            publish_noreplace(stage, destination)
            status = "created"
        except FileExistsError:
            if verify_replay(destination, curated_dir, import_dir) != manifest:
                raise SnapshotError("immutable_full_dq_replay_conflict") from None
            status = "reused"
    return build_result(status, destination, manifest, replay_raw)


def build_result(
    status: str, destination: Path, manifest: Manifest, replay_raw: bytes
) -> dict[str, Any]:
    return {
        "status": status,
        "directory": str(destination),
        "full_dq_replay_id": manifest.full_dq_replay_id,
        "parent_curated_dataset_id": manifest.descriptor.parent.curated_dataset_id,
        "report": json.loads(replay_raw)["report"],
        "model_readiness": "not_qualified",
    }
