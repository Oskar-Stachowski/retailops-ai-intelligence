"""Bounded replay of comparison and split; development arrays exclude final-test labels."""

import hashlib
import sqlite3
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from retailops_ai.source_snapshot.files import (
    MAX_METADATA_BYTES,
    SnapshotError,
    canonical_json,
    checked_directory,
    inventory,
    json_sha256,
    read_bytes,
    read_json,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout.split import ROLES, SplitPolicy
from retailops_ai.stockout_temporal_series.store import TemporalStore
from retailops_ai.stockout_temporal_storage.bundle import (
    DEFAULT_PARTITION_POLICY as DEFAULT_PARTITION_POLICY,
)
from retailops_ai.stockout_temporal_storage.bundle import (
    TemporalPartitionPolicy as TemporalPartitionPolicy,
)
from retailops_ai.stockout_temporal_storage.bundle import (
    TemporalPreparation as FrozenTemporalPreparation,
)
from retailops_ai.stockout_temporal_storage.bundle import (
    implementation as frozen_temporal_implementation,
)
from retailops_ai.stockout_temporal_storage.store import (
    DEFAULT_STORAGE_POLICY,
    MAX_PARENT_FILES,
    PartitionInputs,
    TemporalStoragePolicy,
    capture,
    physical_key,
)
from retailops_ai.stockout_training.contract import MAX_BYTES
from retailops_ai.stockout_training.development import implementation as training_implementation
from retailops_ai.stockout_training.inputs import DevelopmentData

MANIFEST = "manifest.json"
MAX_PARTITION_BYTES = 16 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**2


def implementation() -> dict[str, Any]:
    import pyarrow  # type: ignore[import-untyped]

    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(
            files("retailops_ai.stockout_temporal_series").iterdir(), key=lambda r: r.name
        )
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(
        code_sha256=json_sha256(code),
        frozen_training=training_implementation(),
        frozen_temporal=frozen_temporal_implementation(),
        pyarrow_version=pyarrow.__version__,
        sqlite_version=sqlite3.sqlite_version,
    )


class TemporalPreparation(FrozenTemporalPreparation):
    """Identical frozen decisions; identities bind the new replay orchestration."""

    def manifest(self) -> dict[str, Any]:
        document = super().manifest()
        descriptor = document["descriptor"]
        if 1 + sum(len(s["files"]) for s in descriptor["partitions"]) > MAX_PARENT_FILES:
            raise SnapshotError("stockout_temporal_output_file_resource_limit")
        descriptor.update(
            schema_version="2.1.0",
            replay_policy="single-private-parent-replay-1.0.0",
            implementation=implementation(),
        )
        document["temporal_bundle_id"] = "temporal-partitions-sha256-" + json_sha256(descriptor)
        if len(canonical_json(document)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_temporal_manifest_resource_limit")
        return document


def names(document: dict[str, Any]) -> set[str]:
    return {MANIFEST} | {
        f["path"] for s in document["descriptor"]["partitions"] for f in s["files"]
    }


def check_partition(target: Path, spec: dict[str, Any], blobs: dict[str, bytes]) -> None:
    for ref in spec["files"]:
        if read_bytes(target, ref["path"], MAX_PARTITION_BYTES) != blobs[ref["role"]]:
            raise SnapshotError("stockout_temporal_partition_full_replay_mismatch")


def policies(
    document: dict[str, Any],
) -> tuple[SplitPolicy, TemporalPartitionPolicy, TemporalStoragePolicy]:
    descriptor = document["descriptor"]
    if (
        descriptor["schema_version"] != "2.1.0"
        or descriptor["role"] != "stockout_temporal_partitions"
    ):
        raise SnapshotError("stockout_temporal_bundle_version_required")
    return (
        SplitPolicy.model_validate(descriptor["split_policy"]),
        TemporalPartitionPolicy.model_validate(descriptor["partition_policy"]),
        TemporalStoragePolicy.model_validate(descriptor["storage_policy"]),
    )


def replay(target: Path, store: TemporalStore, stored: dict[str, Any]) -> dict[str, Any]:
    target_seal = capture(target)
    split_policy, partition_policy, storage_policy = policies(stored)
    if storage_policy != store.policy:
        raise SnapshotError("stockout_temporal_storage_policy_mismatch")
    preparation = TemporalPreparation(store, split_policy, partition_policy)
    for _, spec, blobs in preparation.partitions():
        check_partition(target, spec, blobs)
    expected = preparation.manifest()
    inventory(target, names(expected))
    if stored != expected:
        raise SnapshotError("stockout_temporal_manifest_full_replay_mismatch")
    if capture(target) != target_seal or read_json(target, MANIFEST) != stored:
        raise SnapshotError("stockout_temporal_changed_during_development")
    return expected


def build_temporal_bundle(
    inputs: PartitionInputs,
    target: Path,
    *,
    split_policy: SplitPolicy,
    allow_evaluation_truth: bool = False,
    partition_policy: TemporalPartitionPolicy = DEFAULT_PARTITION_POLICY,
    storage_policy: TemporalStoragePolicy = DEFAULT_STORAGE_POLICY,
) -> tuple[dict[str, Any], str]:
    root = checked_directory(target.parent)
    destination = root / target.name
    if (
        target.name in {"", ".", ".."}
        or ".." in target.parts
        or any(
            destination == p or p in destination.parents or destination in p.parents
            for p in map(checked_directory, inputs.roots().values())
        )
    ):
        raise SnapshotError("stockout_temporal_unsafe_output")
    with TemporalStore(
        inputs, allow_evaluation_truth=allow_evaluation_truth, policy=storage_policy, scratch=root
    ) as store:
        preparation = TemporalPreparation(store, split_policy, partition_policy)
        with tempfile.TemporaryDirectory(prefix=".stockout-temporal-bundle-", dir=root) as tmp:
            staged = Path(tmp) / "bundle"
            staged.mkdir(mode=0o700)
            for _, spec, blobs in preparation.partitions():
                for ref in spec["files"]:
                    write_private(staged / ref["path"], blobs[ref["role"]])
            document = preparation.manifest()
            write_private(staged / MANIFEST, canonical_json(document) + b"\n")
            fsync_tree(staged)
            try:
                publish_noreplace(staged, destination)
            except FileExistsError:
                if read_json(destination, MANIFEST) != document:
                    raise SnapshotError("stockout_temporal_immutable_output_conflict") from None
                inventory(destination, names(document))
                for spec in document["descriptor"]["partitions"]:
                    check_partition(
                        destination,
                        spec,
                        {
                            f["role"]: read_bytes(staged, f["path"], MAX_PARTITION_BYTES)
                            for f in spec["files"]
                        },
                    )
                return document, "reused"
    return document, "published"


def verify_temporal_bundle(
    target: Path, inputs: PartitionInputs, *, allow_evaluation_truth: bool = False
) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    _, _, storage_policy = policies(stored)
    with TemporalStore(
        inputs, allow_evaluation_truth=allow_evaluation_truth, policy=storage_policy
    ) as store:
        return replay(target, store, stored)


def assemble_partitioned_development(
    target: Path, inputs: PartitionInputs, *, allow_evaluation_truth: bool = False
) -> DevelopmentData:
    stored = read_json(target, MANIFEST)
    target_seal = capture(target)
    split_policy, _, storage_policy = policies(stored)
    with TemporalStore(
        inputs, allow_evaluation_truth=allow_evaluation_truth, policy=storage_policy
    ) as store:
        # Private pending development arrays are filled during one temporal replay.
        # Nothing is returned to a fitter until all parts/metadata and parent seals pass.
        rows: dict[str, list[dict[str, Any]]] = {r: [] for r in ROLES[:-1]}
        outcomes: dict[str, list[int]] = {r: [] for r in ROLES[:-1]}
        lineage_entries: dict[str, list[list[Any]]] = {r: [] for r in ROLES[:-1]}
        coverage: dict[str, Any] = {}
        count = size = 0
        _, partition_policy, _ = policies(stored)
        preparation = TemporalPreparation(store, split_policy, partition_policy)
        for batch, spec, blobs in preparation.partitions():
            check_partition(target, spec, blobs)
            comparison = {physical_key(p): p for p in batch["comparison"]}
            membership = [
                m for m in batch["membership"] if m["eligible"] and m["role"] in ROLES[:-1]
            ]
            labels = {
                physical_key(p): p
                for p in store.points("labels", [physical_key(m) for m in membership])
            }
            for m in membership:
                key, role = physical_key(m), m["role"]
                row, point = comparison[key], labels[key]
                if row["status"] != "eligible" or point["incident_stockout"] not in (0, 1):
                    raise SnapshotError("stockout_training_ineligible_or_unknown_label")
                category, source_hash = store.category(m["product_id"], m["as_of"])
                selected = dict(
                    product_id=m["product_id"],
                    stock_location_id=m["stock_location_id"],
                    as_of=m["as_of"],
                    category_id=category,
                    values={**row["base"], **row["upstream"]},
                )
                entry = [
                    role,
                    m["product_id"],
                    m["stock_location_id"],
                    m["as_of"],
                    category,
                    source_hash,
                ]
                size += len(canonical_json([selected, point["incident_stockout"], entry]))
                count += 1
                if size > MAX_BYTES or count > store.policy.max_rows:
                    raise SnapshotError("stockout_temporal_development_array_resource_limit")
                rows[role].append(selected)
                outcomes[role].append(point["incident_stockout"])
                lineage_entries[role].append(entry)
        verified = preparation.manifest()
        inventory(target, names(verified))
        if stored != verified or read_json(target, MANIFEST) != verified:
            raise SnapshotError("stockout_temporal_manifest_full_replay_mismatch")
        store.check_database()
        store.check_parents()
        if capture(target) != target_seal:
            raise SnapshotError("stockout_temporal_changed_during_development")
        lineage_digest = hashlib.sha256(b"[")
        lineage_count = 0
        for role in ROLES[:-1]:
            for entry in lineage_entries[role]:
                if lineage_count:
                    lineage_digest.update(b",")
                lineage_digest.update(canonical_json(entry))
                lineage_count += 1
            coverage[role] = dict(
                eligible=len(rows[role]),
                forecast_available=sum(
                    r["values"]["forecast_unavailable"] == 0 for r in rows[role]
                ),
            )
        lineage_digest.update(b"]")
        if any(not rows[r] or set(outcomes[r]) != {0, 1} for r in ROLES[:-1]):
            raise SnapshotError("stockout_training_requires_both_development_classes")
        coverage["final_test"] = dict(
            eligible_membership_only=verified["report"]["split"]["eligible_by_role"]["test"],
            outcomes_evaluated=False,
        )
        coverage["raw_global_upstream_forecast_ready"] = verified["report"]["comparison"][
            "upstream_forecast_ready"
        ]
        return DevelopmentData(
            rows=rows,
            outcomes=outcomes,
            parents={
                **verified["descriptor"]["parents"],
                "temporal_bundle_id": verified["temporal_bundle_id"],
                "source_dataset_id": verified["descriptor"]["source_dataset_id"],
                "curated_dataset_id": verified["descriptor"]["curated_dataset_id"],
            },
            split_policy=split_policy,
            coverage=coverage,
            categorical_lineage_sha256=lineage_digest.hexdigest(),
        )
