"""Bounded normalized partitions, immutable publication and full causal replay."""

import hashlib
import tempfile
from collections import Counter
from collections.abc import Iterator
from importlib.resources import files
from itertools import groupby
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt

from retailops_ai.data_contracts.common import Contract
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
from retailops_ai.stockout.dataset import INPUT_LIMITS
from retailops_ai.stockout.feature_contract import (
    DEFAULT_FEATURE_POLICY,
    FeaturePoint,
    FeaturePolicy,
)
from retailops_ai.stockout.feature_dataset import (
    MAX_FEATURE_POINTS,
    feature_implementation,
    load_features_input,
)
from retailops_ai.stockout.features import feature_point
from retailops_ai.stockout_preparation.index import FactIndex
from retailops_ai.stockout_preparation.schema import encode_partition

MAX_PARTITION_BYTES = 16 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**2
MANIFEST = "manifest.json"


class PartitionPolicy(Contract):
    version: Literal["stockout-partitions-2.0.0"] = "stockout-partitions-2.0.0"
    batch_origins: Annotated[StrictInt, Field(ge=1, le=256)] = 256
    max_partition_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_PARTITION_BYTES)] = (
        MAX_PARTITION_BYTES
    )


DEFAULT_PARTITION_POLICY = PartitionPolicy()


def implementation() -> dict[str, Any]:
    import pyarrow  # type: ignore[import-untyped]

    code = {
        resource.name: hashlib.sha256(resource.read_bytes()).hexdigest()
        for resource in sorted(
            files("retailops_ai.stockout_preparation").iterdir(), key=lambda r: r.name
        )
        if resource.is_file() and resource.name.endswith(".py")
    }
    return {
        "projection": feature_implementation(),
        "preparation_code_sha256": json_sha256(code),
        "pyarrow_version": pyarrow.__version__,
    }


class Preparation:
    """One bounded sealed input per run; at most one physical batch of output."""

    def __init__(
        self,
        curated: Path,
        feature_policy: FeaturePolicy,
        partition_policy: PartitionPolicy,
    ) -> None:
        self.document, records = load_features_input(curated)
        self.origins = sorted(
            {
                (row["product_id"], row["stock_location_id"], row["snapshot_at"])
                for row in records["inventory_daily_snapshots"]
            }
        )
        if len(self.origins) > MAX_FEATURE_POINTS:
            raise SnapshotError("stockout_feature_point_limit")
        self.index = FactIndex(records)
        self.feature_policy = feature_policy
        self.partition_policy = partition_policy
        self.specs: list[dict[str, Any]] = []
        self.statuses: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.digest = hashlib.sha256(b"[")
        self.count = 0
        self.bytes = 0
        self.complete = False

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
        if not self.complete:
            raise SnapshotError("stockout_preparation_incomplete")
        parent = self.document["descriptor"]
        descriptor = {
            "schema_version": "2.0.0",
            "role": "stockout_feature_partitions",
            "data_class": "features",
            "grain": ["product_id", "stock_location_id", "as_of"],
            "source_dataset_id": parent["parent_source_dataset_id"],
            "snapshot_id": parent["parent_snapshot_id"],
            "qualification_id": parent["parent_qualification_id"],
            "curated_dataset_id": self.document["curated_dataset_id"],
            "feature_policy": self.feature_policy.model_dump(mode="json"),
            "partition_policy": self.partition_policy.model_dump(mode="json"),
            "implementation": implementation(),
            "rows": self.count,
            "points_sha256": self.digest.hexdigest(),
            "partitions": self.specs,
            "limits": {
                "input_bytes": INPUT_LIMITS.max_bytes,
                "input_rows": INPUT_LIMITS.max_rows,
                "points": MAX_FEATURE_POINTS,
                "partition_bytes": self.partition_policy.max_partition_bytes,
                "bundle_bytes": MAX_BUNDLE_BYTES,
                "manifest_bytes": MAX_METADATA_BYTES,
            },
        }
        document = {
            "feature_bundle_id": "feature-partitions-sha256-" + json_sha256(descriptor),
            "descriptor": descriptor,
            "report": {
                "statuses": dict(sorted(self.statuses.items())),
                "reasons": dict(sorted(self.reasons.items())),
                "preprocessing_fitted": False,
                "upstream_forecast_ready": False,
                "model_ready": False,
                "full_profile_ready": False,
            },
        }
        if len(canonical_json(document)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_partition_manifest_byte_limit")
        return document


def _names(document: dict[str, Any]) -> set[str]:
    return {MANIFEST} | {
        file["path"] for spec in document["descriptor"]["partitions"] for file in spec["files"]
    }


def _check_partition(root: Path, spec: dict[str, Any], blobs: dict[str, bytes]) -> None:
    for file in spec["files"]:
        # Compare to replay-generated bytes; never decode attacker-controlled Parquet.
        raw = read_bytes(root, file["path"], MAX_PARTITION_BYTES)
        if raw != blobs[file["role"]]:
            raise SnapshotError("stockout_partition_full_replay_mismatch")


def build_bundle(
    curated: Path,
    target: Path,
    *,
    feature_policy: FeaturePolicy = DEFAULT_FEATURE_POLICY,
    partition_policy: PartitionPolicy = DEFAULT_PARTITION_POLICY,
) -> tuple[dict[str, Any], str]:
    root = checked_directory(target.parent)
    source = checked_directory(curated)
    destination = root / target.name
    if (
        target.name in {"", ".", ".."}
        or ".." in target.parts
        or destination == source
        or source in destination.parents
        or destination in source.parents
    ):
        raise SnapshotError("stockout_partition_unsafe_output")
    preparation = Preparation(source, feature_policy, partition_policy)
    with tempfile.TemporaryDirectory(prefix=".stockout-partitions-", dir=root) as tmp:
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
            # A race or existing path is accepted only if the entire bundle matches.
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


def verify_bundle(target: Path, curated: Path) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    feature_policy = FeaturePolicy.model_validate(stored["descriptor"]["feature_policy"])
    partition_policy = PartitionPolicy.model_validate(stored["descriptor"]["partition_policy"])
    preparation = Preparation(curated, feature_policy, partition_policy)
    for _, spec, blobs in preparation.partitions():
        _check_partition(target, spec, blobs)
    expected = preparation.manifest()
    inventory(target, _names(expected))
    if stored != expected:
        raise SnapshotError("stockout_partition_manifest_full_replay_mismatch")
    return expected


def iter_verified_points(target: Path, curated: Path) -> Iterator[FeaturePoint]:
    """Yield only after all files/manifest replay; recheck each partition before use.

    Two bounded passes prevent partial consumption of an invalid later partition.
    Replayed points are semantically identical to the checked serialized content.
    """
    verified = verify_bundle(target, curated)
    descriptor = verified["descriptor"]
    preparation = Preparation(
        curated,
        FeaturePolicy.model_validate(descriptor["feature_policy"]),
        PartitionPolicy.model_validate(descriptor["partition_policy"]),
    )
    if preparation.document["curated_dataset_id"] != descriptor["curated_dataset_id"]:
        raise SnapshotError("stockout_partition_parent_changed_during_read")
    for points, spec, blobs in preparation.partitions():
        if read_json(target, MANIFEST) != verified:
            raise SnapshotError("stockout_partition_changed_during_read")
        _check_partition(target, spec, blobs)
        yield from points
    if preparation.manifest() != verified or read_json(target, MANIFEST) != verified:
        raise SnapshotError("stockout_partition_changed_during_read")
