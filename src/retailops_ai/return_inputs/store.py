"""Immutable as-of return views, replayed against their verified operational parent."""

from __future__ import annotations

import hashlib
import platform
import tempfile
from collections import Counter
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from retailops_ai.curated.builder import iter_rows, verify_curated
from retailops_ai.curated.contract import Digest, columns_for, encoded
from retailops_ai.data_contracts.common import Contract, CuratedID, Sha256, SourceID
from retailops_ai.return_inputs.contract import (
    MAX_INPUT_BYTES,
    MAX_INPUT_ROWS,
    MAX_OUTPUT_BYTES,
    MAX_OUTPUT_ROWS,
    TABLES,
    Point,
    Policy,
)
from retailops_ai.return_inputs.projection import Returns
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    canonical_json,
    checked_directory,
    file_hash,
    inventory,
    json_sha256,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

InputID = Annotated[str, Field(pattern=r"^return-inputs-sha256-[0-9a-f]{64}$")]
SnapshotID = Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]


class Implementation(Contract):
    version: Literal["return-event-day-view-1.0.0"]
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    contract_files: dict[str, Sha256]
    dependency_lock_sha256: Sha256
    python_version: str
    input_row_limit: Literal[100000]
    input_byte_limit: Literal[134217728]
    output_row_limit: Literal[100000]
    output_byte_limit: Literal[134217728]


class Parent(Contract):
    curated_dataset_id: CuratedID
    curated_descriptor_sha256: Sha256
    source_dataset_id: SourceID
    snapshot_id: SnapshotID
    qualification_id: str


class Descriptor(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    role: Literal["operational_view"] = "operational_view"
    parent: Parent
    policy: Policy
    policy_sha256: Sha256
    implementation: Implementation
    input_tables: tuple[str, ...]
    model_feature_allowlist: tuple[()]
    row_count: Annotated[int, Field(ge=1, le=MAX_OUTPUT_ROWS)]
    content_sha256: Sha256
    status_counts: dict[str, Annotated[int, Field(ge=0)]]
    evaluation_truth: Literal["excluded"] = "excluded"
    detector_readiness: Literal["not_qualified"] = "not_qualified"


class Manifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    return_input_id: InputID
    descriptor: Descriptor
    file_path: Literal["returns.jsonl"] = "returns.jsonl"
    file_bytes: Annotated[int, Field(ge=1, le=MAX_OUTPUT_BYTES)]
    file_sha256: Sha256


def contract_bytes(name: str) -> bytes:
    path = files("retailops_ai.return_inputs").joinpath("contracts/" + name)
    return (
        path.read_bytes()
        if path.is_file()
        else (
            Path(__file__).resolve().parents[3] / "contracts/return_inputs/v1" / name
        ).read_bytes()
    )


def implementation() -> Implementation:
    hashes = {}
    for package in ("return_inputs", "curated", "source_snapshot"):
        directory = files("retailops_ai." + package)
        for p in sorted(directory.iterdir(), key=lambda p: p.name):
            if p.name.endswith(".py"):
                hashes[package + "/" + p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    return Implementation(
        version="return-event-day-view-1.0.0",
        code_files=hashes,
        code_sha256=json_sha256(hashes),
        contract_files={
            name: hashlib.sha256(contract_bytes(name)).hexdigest()
            for name in ("policy.schema.json", "point.schema.json", "manifest.schema.json")
        },
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        python_version=platform.python_version(),
        input_row_limit=100000,
        input_byte_limit=134217728,
        output_row_limit=100000,
        output_byte_limit=134217728,
    )


def parent_inputs(root: Path) -> tuple[Parent, dict[str, list[dict[str, Any]]]]:
    document = verify_curated(root)
    if document["schema_version"] != "1.2.0":
        raise SnapshotError("return_inputs_require_curated_1_2")
    descriptor = document["descriptor"]
    parent = Parent(
        curated_dataset_id=document["curated_dataset_id"],
        curated_descriptor_sha256=json_sha256(descriptor),
        source_dataset_id=descriptor["parent_source_dataset_id"],
        snapshot_id=descriptor["parent_snapshot_id"],
        qualification_id=descriptor["parent_qualification_id"],
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    rows = size = 0
    with tempfile.TemporaryDirectory(prefix="return-source-check-") as temporary:
        for name in TABLES:
            table = next(t for t in document["tables"] if t["table"] == name)
            digest = Digest(
                Path(temporary) / (name + ".sqlite"), columns_for(name, "1.2.0"), table["grain"]
            )
            tables[name] = []
            try:
                for row in iter_rows(root, table["files"], 256):
                    rows += 1
                    size += len(encoded(row))
                    if rows > MAX_INPUT_ROWS or size > MAX_INPUT_BYTES:
                        raise SnapshotError("return_source_input_limit")
                    digest.add(row)
                    tables[name].append(row)
                if any(table[k] != v for k, v in digest.summary().items()):
                    raise SnapshotError("return_source_changed_during_load")
            finally:
                digest.close()
    return parent, tables


def input_id(descriptor: Descriptor) -> str:
    return "return-inputs-sha256-" + json_sha256(descriptor.model_dump(mode="json"))


def verify_inputs(root: Path, curated_dir: Path) -> Manifest:
    root = checked_directory(root)
    raw = read_bytes(root, "return_manifest.json", 1024**2)
    manifest = Manifest.model_validate_json(raw)
    descriptor = manifest.descriptor
    if (
        read_bytes(root, "manifest.sha256", 128)
        != (hashlib.sha256(raw).hexdigest() + "\n").encode()
    ):
        raise SnapshotError("return_manifest_checksum_mismatch")
    inventory(root, {"return_manifest.json", "manifest.sha256", "returns.jsonl"})
    if (
        manifest.return_input_id != input_id(descriptor)
        or descriptor.policy_sha256 != json_sha256(descriptor.policy.model_dump(mode="json"))
        or descriptor.input_tables != TABLES
        or descriptor.model_feature_allowlist != ()
        or descriptor.implementation != implementation()
        or file_hash(root, "returns.jsonl") != (manifest.file_bytes, manifest.file_sha256)
    ):
        raise SnapshotError("return_identity_or_policy_mismatch")
    parent, tables = parent_inputs(curated_dir)
    if parent != descriptor.parent:
        raise SnapshotError("return_parent_mismatch")
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    count = size = 0
    with regular_file(root, "returns.jsonl") as stream:
        for expected in Returns(tables, descriptor.policy).rows():
            line = stream.readline(65537)
            if not line or len(line) > 65536:
                raise SnapshotError("return_input_record_limit_or_missing")
            point = Point.model_validate_json(line)
            canonical = canonical_json(point.model_dump(mode="json")) + b"\n"
            if point != expected or line != canonical:
                raise SnapshotError("return_input_semantic_replay_mismatch")
            digest.update(canonical)
            counts[point.status] += 1
            count += 1
            size += len(canonical)
            if count > MAX_OUTPUT_ROWS or size > MAX_OUTPUT_BYTES:
                raise SnapshotError("return_output_limit")
        if stream.read(1):
            raise SnapshotError("extra_return_input_rows")
    if (
        descriptor.row_count != count
        or descriptor.content_sha256 != digest.hexdigest()
        or descriptor.status_counts != dict(counts)
        or manifest.file_bytes != size
    ):
        raise SnapshotError("return_content_receipt_mismatch")
    return manifest


@dataclass(frozen=True)
class Result:
    status: str
    directory: Path
    manifest: Manifest

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "directory": str(self.directory),
            "return_input_id": self.manifest.return_input_id,
            "parent_curated_dataset_id": self.manifest.descriptor.parent.curated_dataset_id,
            "row_count": self.manifest.descriptor.row_count,
            "status_counts": self.manifest.descriptor.status_counts,
            "detector_readiness": "not_qualified",
        }


def build_inputs(curated_dir: Path, generated_root: Path, policy: Policy) -> Result:
    curated_dir = checked_directory(curated_dir)
    root = generated_root.absolute()
    if (
        ".." in root.parts
        or root.parts[-2:] != ("data", "generated")
        or root.is_relative_to(curated_dir)
        or any(p.is_symlink() for p in (root, *root.parents))
        or curated_dir.is_relative_to(root / "return-inputs")
    ):
        raise SnapshotError("return_requires_separate_data_generated_root")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(root)
    parent, tables = parent_inputs(curated_dir)
    with tempfile.TemporaryDirectory(prefix=".return-build-", dir=root) as temporary:
        payload = Path(temporary) / "payload"
        payload.mkdir(mode=0o700)
        path = payload / "returns.jsonl"
        counts: Counter[str] = Counter()
        digest = hashlib.sha256()
        count = size = 0
        with path.open("xb") as stream:
            path.chmod(0o600)
            for point in Returns(tables, policy).rows():
                raw = canonical_json(point.model_dump(mode="json")) + b"\n"
                size += len(raw)
                count += 1
                if len(raw) > 65536 or size > MAX_OUTPUT_BYTES or count > MAX_OUTPUT_ROWS:
                    raise SnapshotError("return_output_limit")
                stream.write(raw)
                digest.update(raw)
                counts[point.status] += 1
        if not count:
            raise SnapshotError("empty_return_inputs")
        descriptor = Descriptor(
            parent=parent,
            policy=policy,
            policy_sha256=json_sha256(policy.model_dump(mode="json")),
            implementation=implementation(),
            input_tables=TABLES,
            model_feature_allowlist=(),
            row_count=count,
            content_sha256=digest.hexdigest(),
            status_counts=dict(counts),
        )
        size, sha = file_hash(payload, "returns.jsonl")
        manifest = Manifest(
            return_input_id=input_id(descriptor),
            descriptor=descriptor,
            file_bytes=size,
            file_sha256=sha,
        )
        raw = canonical_json(manifest.model_dump(mode="json")) + b"\n"
        write_private(payload / "return_manifest.json", raw)
        write_private(
            payload / "manifest.sha256", (hashlib.sha256(raw).hexdigest() + "\n").encode()
        )
        verify_inputs(payload, curated_dir)
        directory = root / "return-inputs"
        directory.mkdir(exist_ok=True, mode=0o700)
        checked_directory(directory)
        destination = directory / manifest.return_input_id
        fsync_tree(payload)
        try:
            publish_noreplace(payload, destination)
        except FileExistsError:
            existing = verify_inputs(destination, curated_dir)
            if existing != manifest:
                raise SnapshotError("immutable_return_input_conflict") from None
            return Result("reused", destination, existing)
        return Result("published", destination, manifest)
