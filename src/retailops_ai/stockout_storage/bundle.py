"""V2.1 uses a disposable disk store; the v2.0 projection and layout stay frozen."""

import hashlib
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
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
from retailops_ai.stockout.feature_contract import DEFAULT_FEATURE_POLICY, FeaturePolicy
from retailops_ai.stockout.feature_dataset import MAX_FEATURE_POINTS
from retailops_ai.stockout_preparation.bundle import (
    DEFAULT_PARTITION_POLICY,
    MANIFEST,
    MAX_PARTITION_BYTES,
    PartitionPolicy,
    Preparation,
    _check_partition,
    _names,
)
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_storage.store import DEFAULT_STORAGE_POLICY, DiskFacts, StoragePolicy


def implementation() -> dict[str, Any]:
    code = {
        resource.name: hashlib.sha256(resource.read_bytes()).hexdigest()
        for resource in sorted(
            files("retailops_ai.stockout_storage").iterdir(), key=lambda r: r.name
        )
        if resource.is_file() and resource.name.endswith(".py")
    }
    return {"storage_code_sha256": json_sha256(code), "sqlite_version": sqlite3.sqlite_version}


class DiskIndex(FactIndex):
    """Use the same causal projection interface, with bounded indexed disk reads."""

    def __init__(self, facts: DiskFacts, through: dict[tuple[str, str], datetime]) -> None:
        self.facts = facts
        self.through = through
        self._series: tuple[str, str] | None = None
        self._index: FactIndex | None = None

    def known(self, product: str, stock: str, as_of: datetime) -> dict[str, list[dict[str, Any]]]:
        key = product, stock
        if key not in self.through or as_of > self.through[key]:
            raise SnapshotError("stockout_disk_index_origin_outside_prepared_series")
        if self._series != key:
            self._index = None
            self._series = None
            self._index = FactIndex(self.facts.known(product, stock, self.through[key]))
            self._series = key
        if self._index is None:
            raise SnapshotError("stockout_disk_index_unavailable")
        # Keep every known version through the series cutoff, then clip again at
        # each earlier origin. This never caches a global latest-version view.
        return self._index.known(product, stock, as_of)


class DiskPreparation(Preparation):
    """Reuse the frozen partition/point algorithm; replace only its fact provider."""

    def __init__(
        self, facts: DiskFacts, feature_policy: FeaturePolicy, partition_policy: PartitionPolicy
    ) -> None:
        self.facts = facts
        self.document = facts.document
        self.origins = facts.origins(MAX_FEATURE_POINTS)
        through = {(p, s): origin for p, s, origin in self.origins}
        self.index = DiskIndex(facts, through)
        self.feature_policy, self.partition_policy = feature_policy, partition_policy
        self.specs = []
        self.statuses = Counter()
        self.reasons = Counter()
        self.digest = hashlib.sha256(b"[")
        self.count = self.bytes = 0
        self.complete = False

    def manifest(self) -> dict[str, Any]:
        document = super().manifest()
        descriptor = document["descriptor"]
        descriptor["schema_version"] = "2.1.0"
        descriptor["storage"] = {
            "role": "private_disposable_verified_facts",
            "policy": self.facts.policy.model_dump(mode="json"),
            "implementation": implementation(),
            "input_seal": self.facts.seal,
        }
        document["feature_bundle_id"] = "feature-partitions-sha256-" + json_sha256(descriptor)
        if len(canonical_json(document)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_partition_manifest_byte_limit")
        return document


def build_disk_bundle(
    curated: Path,
    target: Path,
    *,
    feature_policy: FeaturePolicy = DEFAULT_FEATURE_POLICY,
    partition_policy: PartitionPolicy = DEFAULT_PARTITION_POLICY,
    storage_policy: StoragePolicy = DEFAULT_STORAGE_POLICY,
) -> tuple[dict[str, Any], str]:
    root, source = checked_directory(target.parent), checked_directory(curated)
    destination = root / target.name
    if (
        target.name in {"", ".", ".."}
        or ".." in target.parts
        or destination == source
        or source in destination.parents
        or destination in source.parents
    ):
        raise SnapshotError("stockout_partition_unsafe_output")
    with DiskFacts(source, policy=storage_policy, scratch=root) as facts:
        preparation = DiskPreparation(facts, feature_policy, partition_policy)
        with tempfile.TemporaryDirectory(prefix=".stockout-disk-bundle-", dir=root) as tmp:
            staged = Path(tmp) / "bundle"
            staged.mkdir(mode=0o700)
            for _, spec, blobs in preparation.partitions():
                for file in spec["files"]:
                    path = staged / file["path"]
                    path.parent.mkdir(mode=0o700, exist_ok=True)
                    write_private(path, blobs[file["role"]])
            document = preparation.manifest()
            write_private(staged / MANIFEST, canonical_json(document) + b"\n")
            fsync_tree(staged)
            try:
                publish_noreplace(staged, destination)
            except FileExistsError:
                inventory(destination, _names(document))
                if read_json(destination, MANIFEST) != document:
                    raise SnapshotError("stockout_partition_immutable_output_conflict") from None
                for spec in document["descriptor"]["partitions"]:
                    for file in spec["files"]:
                        if read_bytes(destination, file["path"], MAX_PARTITION_BYTES) != read_bytes(
                            staged, file["path"], MAX_PARTITION_BYTES
                        ):
                            raise SnapshotError(
                                "stockout_partition_immutable_output_conflict"
                            ) from None
                return document, "reused"
    return document, "published"


def verify_disk_bundle(target: Path, curated: Path) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    descriptor = stored["descriptor"]
    if descriptor["schema_version"] != "2.1.0":
        raise SnapshotError("stockout_disk_bundle_version_required")
    with DiskFacts(
        curated, policy=StoragePolicy.model_validate(descriptor["storage"]["policy"])
    ) as facts:
        preparation = DiskPreparation(
            facts,
            FeaturePolicy.model_validate(descriptor["feature_policy"]),
            PartitionPolicy.model_validate(descriptor["partition_policy"]),
        )
        for _, spec, blobs in preparation.partitions():
            _check_partition(target, spec, blobs)
        expected = preparation.manifest()
        inventory(target, _names(expected))
        if stored != expected:
            raise SnapshotError("stockout_partition_manifest_full_replay_mismatch")
        return expected
