"""Origin-local history indexes in an isolated 2.2 bundle; earlier versions stay frozen."""

import hashlib
import tempfile
from collections.abc import Iterator
from importlib.resources import files
from itertools import groupby
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
from retailops_ai.stockout.feature_contract import (
    DEFAULT_FEATURE_POLICY,
    FeaturePoint,
    FeaturePolicy,
)
from retailops_ai.stockout_history.projection import feature_point
from retailops_ai.stockout_preparation.bundle import (
    DEFAULT_PARTITION_POLICY,
    MANIFEST,
    MAX_BUNDLE_BYTES,
    MAX_PARTITION_BYTES,
    PartitionPolicy,
    _check_partition,
    _names,
)
from retailops_ai.stockout_preparation.schema import encode_partition
from retailops_ai.stockout_storage.bundle import DiskPreparation
from retailops_ai.stockout_storage.store import DEFAULT_STORAGE_POLICY, DiskFacts, StoragePolicy


def implementation() -> dict[str, Any]:
    code = {
        resource.name: hashlib.sha256(resource.read_bytes()).hexdigest()
        for resource in sorted(
            files("retailops_ai.stockout_history").iterdir(), key=lambda r: r.name
        )
        if resource.is_file() and resource.name.endswith(".py")
    }
    return {"history_code_sha256": json_sha256(code)}


class HistoryPreparation(DiskPreparation):
    """Keep the v2 partition layout and v2.1 bounded input; index only causal history."""

    def partitions(self) -> Iterator[tuple[list[FeaturePoint], dict[str, Any], dict[str, bytes]]]:
        if self.complete or self.count or self.specs:
            raise SnapshotError("stockout_preparation_single_use")
        for (product, stock), group in groupby(self.origins, key=lambda r: r[:2]):
            origins = list(group)
            batch = self.partition_policy.batch_origins
            for start in range(0, len(origins), batch):
                points = [
                    feature_point(
                        self.index.known(product, stock, origin),
                        product=product,
                        stock=stock,
                        as_of=origin,
                        policy=self.feature_policy,
                    )
                    for _, _, origin in origins[start : start + batch]
                ]
                blobs = encode_partition(points, self.partition_policy.max_partition_bytes)
                self.bytes += sum(len(raw) for raw in blobs.values())
                if self.bytes > MAX_BUNDLE_BYTES:
                    raise SnapshotError("stockout_bundle_byte_limit")
                number = len(self.specs)
                spec = {
                    "number": number,
                    "product_id": product,
                    "stock_location_id": stock,
                    "first_as_of": points[0].model_dump(mode="json")["as_of"],
                    "last_as_of": points[-1].model_dump(mode="json")["as_of"],
                    "rows": len(points),
                    "files": [
                        {
                            "role": role,
                            "path": f"part-{number:05d}/{role}.parquet",
                            "bytes": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                        for role, raw in sorted(blobs.items())
                    ],
                }
                self.specs.append(spec)
                for point in points:
                    if self.count:
                        self.digest.update(b",")
                    self.digest.update(canonical_json(point.model_dump(mode="json")))
                    self.count += 1
                    self.statuses[point.status] += 1
                    if point.reason:
                        self.reasons[point.reason] += 1
                yield points, spec, blobs
        self.digest.update(b"]")
        self.complete = True

    def manifest(self) -> dict[str, Any]:
        document = super().manifest()
        descriptor = document["descriptor"]
        descriptor["schema_version"] = "2.2.0"
        descriptor["history_index"] = {
            "version": "stockout-indexed-history-1.0.0",
            "scope": "origin_local_cumulative_ledger_and_daily_demand",
            "implementation": implementation(),
        }
        document["feature_bundle_id"] = "feature-partitions-sha256-" + json_sha256(descriptor)
        if len(canonical_json(document)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_partition_manifest_byte_limit")
        return document


def build_history_bundle(
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
        preparation = HistoryPreparation(facts, feature_policy, partition_policy)
        with tempfile.TemporaryDirectory(prefix=".stockout-history-bundle-", dir=root) as tmp:
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


def verify_history_bundle(target: Path, curated: Path) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    descriptor = stored["descriptor"]
    if descriptor["schema_version"] != "2.2.0":
        raise SnapshotError("stockout_history_bundle_version_required")
    with DiskFacts(
        curated, policy=StoragePolicy.model_validate(descriptor["storage"]["policy"])
    ) as facts:
        preparation = HistoryPreparation(
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
