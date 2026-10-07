"""Series-selected upstream partitions after global SQL ambiguity validation."""

import hashlib
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from importlib.resources import files
from itertools import groupby, islice
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StrictInt

from retailops_ai.data_contracts.common import Contract
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.source_snapshot.files import (
    MAX_METADATA_BYTES,
    SnapshotError,
    canonical_json,
    checked_directory,
    file_hash,
    inventory,
    json_sha256,
    read_bytes,
    read_json,
)
from retailops_ai.source_snapshot.importer import write_private
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace
from retailops_ai.stockout.dataset import INPUT_LIMITS
from retailops_ai.stockout.feature_contract import FeaturePolicy
from retailops_ai.stockout.feature_dataset import MAX_FEATURE_POINTS, feature_implementation
from retailops_ai.stockout.upstream import baseline_code, model_version, upstream_point
from retailops_ai.stockout.upstream_contract import (
    DEFAULT_UPSTREAM_POLICY,
    UpstreamPoint,
    UpstreamPolicy,
)
from retailops_ai.stockout_history.bundle import HistoryPreparation
from retailops_ai.stockout_preparation.bundle import PartitionPolicy, _check_partition, _names
from retailops_ai.stockout_storage.store import DiskFacts, StoragePolicy
from retailops_ai.stockout_upstream_series.store import (
    DEFAULT_STORAGE_POLICY,
    MAX_CACHE_BYTES,
    MAX_CACHE_ROWS,
    SeriesFacts,
    UpstreamStoragePolicy,
)
from retailops_ai.stockout_upstream_storage.schema import encode

MANIFEST = "manifest.json"
MAX_PARTITION_BYTES = 16 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**2
Origin = tuple[str, str, datetime, bool]


class UpstreamPartitionPolicy(Contract):
    version: Literal["stockout-upstream-partitions-2.1.0"] = "stockout-upstream-partitions-2.1.0"
    batch_origins: Annotated[StrictInt, Field(ge=1, le=256)] = 256
    max_partition_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_PARTITION_BYTES)] = (
        MAX_PARTITION_BYTES
    )


DEFAULT_PARTITION_POLICY = UpstreamPartitionPolicy()


def implementation() -> dict[str, Any]:
    import pyarrow  # type: ignore[import-untyped]

    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(
            files("retailops_ai.stockout_upstream_series").iterdir(), key=lambda r: r.name
        )
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(
        projection=feature_implementation(),
        forecast_code=baseline_code(),
        frozen_storage_code={
            r.name: hashlib.sha256(r.read_bytes()).hexdigest()
            for r in files("retailops_ai.stockout_upstream_storage").iterdir()
            if r.is_file() and r.name.endswith(".py")
        },
        preparation_code_sha256=json_sha256(code),
        pyarrow_version=pyarrow.__version__,
        sqlite_version=sqlite3.sqlite_version,
    )


def check_feature_parent(target: Path, document: dict[str, Any]) -> None:
    inventory(target, _names(document))
    if read_json(target, MANIFEST) != document:
        raise SnapshotError("stockout_upstream_feature_parent_changed")
    for spec in document["descriptor"]["partitions"]:
        for ref in spec["files"]:
            if file_hash(target, ref["path"]) != (ref["bytes"], ref["sha256"]):
                raise SnapshotError("stockout_upstream_feature_parent_changed")


def feature_parent(target: Path, curated: Path) -> tuple[dict[str, Any], list[Origin]]:
    """Replay every 2.2 feature part, retaining only <=10k keys and eligibility flags."""
    stored = read_json(target, MANIFEST)
    descriptor = stored["descriptor"]
    if (
        descriptor["schema_version"] != "2.2.0"
        or descriptor["role"] != "stockout_feature_partitions"
    ):
        raise SnapshotError("stockout_upstream_feature_bundle_22_required")
    origins: list[Origin] = []
    with DiskFacts(
        curated, policy=StoragePolicy.model_validate(descriptor["storage"]["policy"])
    ) as facts:
        preparation = HistoryPreparation(
            facts,
            FeaturePolicy.model_validate(descriptor["feature_policy"]),
            PartitionPolicy.model_validate(descriptor["partition_policy"]),
        )
        for points, spec, blobs in preparation.partitions():
            _check_partition(target, spec, blobs)
            origins.extend(
                (p.product_id, p.stock_location_id, p.as_of, p.status == "eligible") for p in points
            )
        expected = preparation.manifest()
        if stored != expected:
            raise SnapshotError("stockout_upstream_feature_parent_full_replay_mismatch")
    check_feature_parent(target, expected)
    return expected, sorted(origins, key=lambda r: (r[2], r[0], r[1]))


class UpstreamPreparation:
    """Global SQL validation once per origin; only one physical series in memory."""

    def __init__(
        self,
        facts: SeriesFacts,
        parent: dict[str, Any],
        origins: list[Origin],
        upstream_policy: UpstreamPolicy,
        partition_policy: UpstreamPartitionPolicy,
    ) -> None:
        if facts.document["curated_dataset_id"] != parent["descriptor"]["curated_dataset_id"]:
            raise SnapshotError("stockout_upstream_curated_parent_mismatch")
        self.facts, self.parent, self.origins = facts, parent, origins
        self.upstream_policy, self.policy = upstream_policy, partition_policy
        self.version = model_version(upstream_policy)
        self.specs: list[dict[str, Any]] = []
        self.statuses: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.count = self.bytes = self.eligible = self.available = 0
        self.digest = hashlib.sha256(b"[")
        self.started = self.complete = False

    def partitions(self) -> Iterator[tuple[list[UpstreamPoint], dict[str, Any], bytes]]:
        if self.started:
            raise SnapshotError("stockout_upstream_preparation_single_use")
        self.started = True
        for day, group in groupby(self.origins, key=lambda r: r[2].date()):
            origin = make_origin(day)
            self.facts.validate_origin(origin.forecast_origin)
            while batch := list(islice(group, self.policy.batch_origins)):
                points = []
                for p, s, t, _ in batch:
                    records = self.facts.known_series(p, s, origin.forecast_origin)
                    view = OriginFeatures(
                        {n: records[n] for n in self.facts.forecast_tables}, origin
                    )
                    points.append(
                        upstream_point(
                            records,
                            product=p,
                            stock=s,
                            as_of=t,
                            policy=self.upstream_policy,
                            version=self.version,
                            view=view,
                        )
                    )
                    # Retain <=256 output points, never several input series/views.
                    del records, view
                blob = encode(points, self.policy.max_partition_bytes)
                self.bytes += len(blob)
                if self.bytes > MAX_BUNDLE_BYTES:
                    raise SnapshotError("stockout_upstream_bundle_byte_limit")
                number = len(self.specs)
                spec = dict(
                    number=number,
                    origin_date=day.isoformat(),
                    rows=len(points),
                    path=f"part-{number:05d}.parquet",
                    bytes=len(blob),
                    sha256=hashlib.sha256(blob).hexdigest(),
                )
                self.specs.append(spec)
                for point, (_, _, _, eligible) in zip(points, batch, strict=True):
                    if self.count:
                        self.digest.update(b",")
                    self.digest.update(canonical_json(point.model_dump(mode="json")))
                    self.count += 1
                    self.statuses[point.status] += 1
                    if point.reason:
                        self.reasons[point.reason] += 1
                    self.eligible += int(eligible)
                    self.available += int(eligible and point.status == "available")
                yield points, spec, blob
        self.digest.update(b"]")
        self.complete = True

    def manifest(self) -> dict[str, Any]:
        if not self.complete:
            raise SnapshotError("stockout_upstream_preparation_incomplete")
        parent = self.parent["descriptor"]
        descriptor = dict(
            schema_version="2.1.0",
            role="stockout_upstream_partitions",
            data_class="features",
            grain=["product_id", "stock_location_id", "as_of"],
            order=["as_of", "product_id", "stock_location_id"],
            source_dataset_id=parent["source_dataset_id"],
            qualification_id=parent["qualification_id"],
            curated_dataset_id=parent["curated_dataset_id"],
            feature_bundle_id=self.parent["feature_bundle_id"],
            feature_points_sha256=parent["points_sha256"],
            upstream_policy=self.upstream_policy.model_dump(mode="json"),
            partition_policy=self.policy.model_dump(mode="json"),
            storage_policy=self.facts.policy.model_dump(mode="json"),
            selection_policy="physical-series-with-global-known-version-validation-1.0.0",
            decoded_cache_limits=dict(rows=MAX_CACHE_ROWS, canonical_bytes=MAX_CACHE_BYTES),
            input_seal=self.facts.seal,
            implementation=implementation(),
            upstream_model_version=self.version,
            rows=self.count,
            points_sha256=self.digest.hexdigest(),
            partitions=self.specs,
            limits=dict(
                input_bytes=INPUT_LIMITS.max_bytes,
                input_rows=INPUT_LIMITS.max_rows,
                points=MAX_FEATURE_POINTS,
                partition_bytes=self.policy.max_partition_bytes,
                bundle_bytes=MAX_BUNDLE_BYTES,
                manifest_bytes=MAX_METADATA_BYTES,
            ),
        )
        result = dict(
            upstream_bundle_id="upstream-partitions-sha256-" + json_sha256(descriptor),
            descriptor=descriptor,
            report=dict(
                selection={
                    k: self.facts.stats[k]
                    for k in (
                        "stored_rows",
                        "maximum_selected_rows",
                        "maximum_selected_bytes",
                        "globally_validated_origins",
                        "series_reads",
                        "decoded_cache_hits",
                        "maximum_decoded_cache_rows",
                        "maximum_decoded_cache_bytes",
                    )
                },
                statuses=dict(sorted(self.statuses.items())),
                reasons=dict(sorted(self.reasons.items())),
                base_eligible_rows=self.eligible,
                base_eligible_with_forecast=self.available,
                upstream_lineage_status="passed",
                upstream_forecast_ready=bool(self.eligible) and self.available == self.eligible,
                outcomes_used_for_selection=False,
                parameters_fitted=False,
                model_ready=False,
                full_profile_ready=False,
            ),
        )
        if len(canonical_json(result)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_upstream_manifest_byte_limit")
        return result


def names(document: dict[str, Any]) -> set[str]:
    return {MANIFEST} | {s["path"] for s in document["descriptor"]["partitions"]}


def check_partition(target: Path, spec: dict[str, Any], blob: bytes) -> None:
    if read_bytes(target, spec["path"], MAX_PARTITION_BYTES) != blob:
        raise SnapshotError("stockout_upstream_partition_full_replay_mismatch")


def policies(
    document: dict[str, Any],
) -> tuple[UpstreamPolicy, UpstreamPartitionPolicy, UpstreamStoragePolicy]:
    descriptor = document["descriptor"]
    if (
        descriptor["schema_version"] != "2.1.0"
        or descriptor["role"] != "stockout_upstream_partitions"
    ):
        raise SnapshotError("stockout_upstream_series_bundle_version_required")
    return (
        UpstreamPolicy.model_validate(descriptor["upstream_policy"]),
        UpstreamPartitionPolicy.model_validate(descriptor["partition_policy"]),
        UpstreamStoragePolicy.model_validate(descriptor["storage_policy"]),
    )


def build_upstream_bundle(
    curated: Path,
    features: Path,
    target: Path,
    *,
    upstream_policy: UpstreamPolicy = DEFAULT_UPSTREAM_POLICY,
    partition_policy: UpstreamPartitionPolicy = DEFAULT_PARTITION_POLICY,
    storage_policy: UpstreamStoragePolicy = DEFAULT_STORAGE_POLICY,
) -> tuple[dict[str, Any], str]:
    root = checked_directory(target.parent)
    sources = checked_directory(curated), checked_directory(features)
    destination = root / target.name
    if (
        target.name in {"", ".", ".."}
        or ".." in target.parts
        or any(
            destination == s or s in destination.parents or destination in s.parents
            for s in sources
        )
    ):
        raise SnapshotError("stockout_upstream_partition_unsafe_output")
    parent, origins = feature_parent(features, curated)
    with SeriesFacts(curated, policy=storage_policy, scratch=root) as facts:
        preparation = UpstreamPreparation(facts, parent, origins, upstream_policy, partition_policy)
        with tempfile.TemporaryDirectory(prefix=".stockout-upstream-bundle-", dir=root) as tmp:
            staged = Path(tmp) / "bundle"
            staged.mkdir(mode=0o700)
            for _, spec, blob in preparation.partitions():
                write_private(staged / spec["path"], blob)
            document = preparation.manifest()
            check_feature_parent(features, parent)
            write_private(staged / MANIFEST, canonical_json(document) + b"\n")
            fsync_tree(staged)
            try:
                publish_noreplace(staged, destination)
            except FileExistsError:
                if read_json(destination, MANIFEST) != document:
                    raise SnapshotError(
                        "stockout_upstream_partition_immutable_output_conflict"
                    ) from None
                inventory(destination, names(document))
                for spec in document["descriptor"]["partitions"]:
                    check_partition(
                        destination, spec, read_bytes(staged, spec["path"], MAX_PARTITION_BYTES)
                    )
                return document, "reused"
    return document, "published"


def verify_upstream_bundle(target: Path, curated: Path, features: Path) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    upstream_policy, partition_policy, storage_policy = policies(stored)
    parent, origins = feature_parent(features, curated)
    with SeriesFacts(curated, policy=storage_policy) as facts:
        preparation = UpstreamPreparation(facts, parent, origins, upstream_policy, partition_policy)
        for _, spec, blob in preparation.partitions():
            check_partition(target, spec, blob)
        expected = preparation.manifest()
        inventory(target, names(expected))
        check_feature_parent(features, parent)
        if stored != expected:
            raise SnapshotError("stockout_upstream_manifest_full_replay_mismatch")
        return expected


def iter_verified_upstream(target: Path, curated: Path, features: Path) -> Iterator[UpstreamPoint]:
    """Full verification before the first point, then sealed bounded replay.

    This reader supplies upstream features; it does not select temporal training roles.
    """
    verified = verify_upstream_bundle(target, curated, features)
    upstream_policy, partition_policy, storage_policy = policies(verified)
    parent, origins = feature_parent(features, curated)
    if parent["feature_bundle_id"] != verified["descriptor"]["feature_bundle_id"]:
        raise SnapshotError("stockout_upstream_feature_parent_changed")
    with SeriesFacts(curated, policy=storage_policy) as facts:
        if facts.seal != verified["descriptor"]["input_seal"]:
            raise SnapshotError("stockout_upstream_parent_changed_during_iteration")
        preparation = UpstreamPreparation(facts, parent, origins, upstream_policy, partition_policy)
        for points, spec, blob in preparation.partitions():
            if read_json(target, MANIFEST) != verified:
                raise SnapshotError("stockout_upstream_bundle_changed_during_iteration")
            check_feature_parent(features, parent)
            check_partition(target, spec, blob)
            yield from points
        inventory(target, names(verified))
        if preparation.manifest() != verified or read_json(target, MANIFEST) != verified:
            raise SnapshotError("stockout_upstream_bundle_changed_during_iteration")
