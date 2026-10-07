"""Bounded label partitions with immutable publication and two-pass verified iteration."""

import hashlib
import tempfile
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import datetime
from importlib.resources import files
from itertools import groupby, islice
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
from retailops_ai.stockout.contract import (
    DEFAULT_POLICY,
    Eligibility,
    LabelPoint,
    LabelPolicy,
    LedgerMovement,
    StockState,
)
from retailops_ai.stockout.dataset import INPUT_LIMITS
from retailops_ai.stockout.dataset import implementation as label_implementation
from retailops_ai.stockout.labels import label_window
from retailops_ai.stockout_label_partitions.schema import encode
from retailops_ai.stockout_label_partitions.store import (
    DEFAULT_STORAGE_POLICY,
    LabelFacts,
    LabelStoragePolicy,
)

MANIFEST = "manifest.json"
MAX_PARTITION_BYTES = 16 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**2


class LabelPartitionPolicy(Contract):
    version: Literal["stockout-label-partitions-2.0.0"] = "stockout-label-partitions-2.0.0"
    batch_origins: Annotated[StrictInt, Field(ge=1, le=256)] = 256
    max_partition_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_PARTITION_BYTES)] = (
        MAX_PARTITION_BYTES
    )


DEFAULT_PARTITION_POLICY = LabelPartitionPolicy()


def implementation() -> dict[str, Any]:
    import pyarrow  # type: ignore[import-untyped]

    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(
            files("retailops_ai.stockout_label_partitions").iterdir(), key=lambda r: r.name
        )
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(
        label_replay=label_implementation(),
        preparation_code_sha256=json_sha256(code),
        pyarrow_version=pyarrow.__version__,
    )


class LabelPreparation:
    """One physical ledger and one <=256-origin point batch; never all result points."""

    def __init__(self, facts: LabelFacts, partition_policy: LabelPartitionPolicy) -> None:
        self.facts, self.policy = facts, partition_policy
        self.specs: list[dict[str, Any]] = []
        self.statuses: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.positive = self.negative = self.count = self.bytes = 0
        self.digest = hashlib.sha256(b"[")
        self.started = self.complete = False

    def point(self, window: dict[str, Any], ledger: Sequence[LedgerMovement]) -> LabelPoint:
        origin = datetime.fromisoformat(window["origin"])
        key = window["product_id"], window["stock_location_id"]
        raw = self.facts.snapshot_at(*key, origin)
        if raw is None:
            raise SnapshotError("stockout_qualified_origin_snapshot_missing")
        state = StockState.model_validate(
            dict(
                product_id=key[0],
                stock_location_id=key[1],
                snapshot_at=raw["snapshot_at"],
                status=raw["status"],
                **{k: raw[k] for k in ("on_hand", "reserved_qty", "available_qty")},
                available_at=max(raw["snapshot_at"], raw["source_available_at"])
                if raw["status"] == "known" and raw["source_available_at"] is not None
                else None,
            )
        )
        certificate = self.facts.get("inventory_history_coverage", window["inventory_coverage_id"])
        if (
            certificate is not None
            and (certificate["product_id"], certificate["stock_location_id"]) != key
        ):
            raise SnapshotError("stockout_coverage_physical_grain_mismatch")
        evidence = Eligibility.model_validate(
            dict(
                reason=window["reason"],
                covered_from_at=certificate["covered_from_at"] if certificate else None,
                covered_through_at=certificate["covered_through_at"] if certificate else None,
                available_at=max(
                    certificate["available_at"],
                    datetime.fromisoformat(window["label_available_at"]),
                )
                if certificate and window["label_available_at"]
                else None,
                truth_delay_seconds=self.facts.snapshot.manifest["source"]["descriptor"]["context"][
                    "projection"
                ]["truth_delay_seconds"],
            )
        )
        point = label_window(
            state,
            ledger,
            as_of=origin,
            evaluated_at=datetime.fromisoformat(window["evaluated_at"]),
            eligibility=evidence,
            policy=self.facts.label_policy,
        )
        if (point.status, point.incident_stockout) != (
            window["status"],
            window["incident_stockout"],
        ):
            raise SnapshotError("stockout_qualified_label_disagrees_with_ledger")
        if point.status == "evaluable" and point.label_available_at != datetime.fromisoformat(
            window["label_available_at"]
        ):
            raise SnapshotError("stockout_qualified_label_availability_mismatch")
        return point

    def partitions(self) -> Iterator[tuple[list[LabelPoint], dict[str, Any], bytes]]:
        if self.started:
            raise SnapshotError("stockout_label_preparation_single_use")
        self.started = True
        ledger: list[LedgerMovement] = []
        for (product, stock), windows in groupby(
            self.facts.windows(), key=lambda w: (w["product_id"], w["stock_location_id"])
        ):
            ledger = []
            ledger = self.facts.ledger(product, stock)
            while batch := list(islice(windows, self.policy.batch_origins)):
                points = [self.point(window, ledger) for window in batch]
                blob = encode(points, self.policy.max_partition_bytes)
                self.bytes += len(blob)
                if self.bytes > MAX_BUNDLE_BYTES:
                    raise SnapshotError("stockout_label_bundle_byte_limit")
                number = len(self.specs)
                spec = dict(
                    number=number,
                    product_id=product,
                    stock_location_id=stock,
                    first_as_of=points[0].model_dump(mode="json")["as_of"],
                    last_as_of=points[-1].model_dump(mode="json")["as_of"],
                    rows=len(points),
                    path=f"part-{number:05d}.parquet",
                    bytes=len(blob),
                    sha256=hashlib.sha256(blob).hexdigest(),
                )
                self.specs.append(spec)
                for point in points:
                    if self.count:
                        self.digest.update(b",")
                    self.digest.update(canonical_json(point.model_dump(mode="json")))
                    self.count += 1
                    self.statuses[point.status] += 1
                    if point.reason:
                        self.reasons[point.reason] += 1
                    self.positive += int(point.incident_stockout == 1)
                    self.negative += int(point.incident_stockout == 0)
                yield points, spec, blob
        self.digest.update(b"]")
        self.complete = True

    def manifest(self) -> dict[str, Any]:
        if not self.complete:
            raise SnapshotError("stockout_label_preparation_incomplete")
        snapshot, parent = self.facts.snapshot, self.facts.snapshot.manifest["descriptor"]
        descriptor = dict(
            schema_version="2.0.0",
            role="stockout_label_partitions",
            data_class="labels",
            target="incident_stockout_7d",
            grain=["product_id", "stock_location_id", "as_of"],
            source_dataset_id=snapshot.source_id,
            snapshot_id=snapshot.snapshot_id,
            qualification_id=parent["parent_qualification_id"],
            qualification_descriptor=parent["qualification"],
            label_policy=self.facts.label_policy.model_dump(mode="json"),
            partition_policy=self.policy.model_dump(mode="json"),
            storage_policy=self.facts.policy.model_dump(mode="json"),
            input_seal=self.facts.seal,
            implementation=implementation(),
            rows=self.count,
            points_sha256=self.digest.hexdigest(),
            partitions=self.specs,
            limits=dict(
                input_bytes=INPUT_LIMITS.max_bytes,
                input_rows=INPUT_LIMITS.max_rows,
                qualification_bytes=MAX_METADATA_BYTES,
                windows=self.facts.label_policy.max_windows,
                ledger_rows=self.facts.label_policy.max_ledger_rows,
                partition_bytes=self.policy.max_partition_bytes,
                bundle_bytes=MAX_BUNDLE_BYTES,
                manifest_bytes=MAX_METADATA_BYTES,
            ),
        )
        document = dict(
            label_bundle_id="label-partitions-sha256-" + json_sha256(descriptor),
            descriptor=descriptor,
            report=dict(
                statuses=dict(sorted(self.statuses.items())),
                reasons=dict(sorted(self.reasons.items())),
                positive_labels=self.positive,
                negative_labels=self.negative,
                label_ledger_replay="passed",
                model_ready=False,
                full_profile_ready=False,
            ),
        )
        if len(canonical_json(document)) + 1 > MAX_METADATA_BYTES:
            raise SnapshotError("stockout_label_manifest_byte_limit")
        return document


def names(document: dict[str, Any]) -> set[str]:
    return {MANIFEST} | {spec["path"] for spec in document["descriptor"]["partitions"]}


def check_partition(target: Path, spec: dict[str, Any], blob: bytes) -> None:
    if read_bytes(target, spec["path"], MAX_PARTITION_BYTES) != blob:
        raise SnapshotError("stockout_label_partition_full_replay_mismatch")


def build_label_bundle(
    source: Path,
    target: Path,
    *,
    allow_evaluation_truth: bool = False,
    label_policy: LabelPolicy = DEFAULT_POLICY,
    partition_policy: LabelPartitionPolicy = DEFAULT_PARTITION_POLICY,
    storage_policy: LabelStoragePolicy = DEFAULT_STORAGE_POLICY,
) -> tuple[dict[str, Any], str]:
    root, source = checked_directory(target.parent), checked_directory(source)
    destination = root / target.name
    if (
        target.name in {"", ".", ".."}
        or ".." in target.parts
        or destination == source
        or source in destination.parents
        or destination in source.parents
    ):
        raise SnapshotError("stockout_label_partition_unsafe_output")
    with LabelFacts(
        source,
        label_policy,
        allow_evaluation_truth=allow_evaluation_truth,
        storage_policy=storage_policy,
        scratch=root,
    ) as facts:
        preparation = LabelPreparation(facts, partition_policy)
        with tempfile.TemporaryDirectory(prefix=".stockout-label-bundle-", dir=root) as tmp:
            staged = Path(tmp) / "bundle"
            staged.mkdir(mode=0o700)
            for _, spec, blob in preparation.partitions():
                write_private(staged / spec["path"], blob)
            document = preparation.manifest()
            write_private(staged / MANIFEST, canonical_json(document) + b"\n")
            fsync_tree(staged)
            try:
                publish_noreplace(staged, destination)
            except FileExistsError:
                if read_json(destination, MANIFEST) != document:
                    raise SnapshotError(
                        "stockout_label_partition_immutable_output_conflict"
                    ) from None
                inventory(destination, names(document))
                for spec in document["descriptor"]["partitions"]:
                    check_partition(
                        destination, spec, read_bytes(staged, spec["path"], MAX_PARTITION_BYTES)
                    )
                return document, "reused"
    return document, "published"


def policies(
    document: dict[str, Any],
) -> tuple[LabelPolicy, LabelPartitionPolicy, LabelStoragePolicy]:
    descriptor = document["descriptor"]
    if descriptor["schema_version"] != "2.0.0" or descriptor["role"] != "stockout_label_partitions":
        raise SnapshotError("stockout_label_bundle_version_required")
    return (
        LabelPolicy.model_validate(descriptor["label_policy"]),
        LabelPartitionPolicy.model_validate(descriptor["partition_policy"]),
        LabelStoragePolicy.model_validate(descriptor["storage_policy"]),
    )


def verify_label_bundle(
    target: Path, source: Path, *, allow_evaluation_truth: bool = False
) -> dict[str, Any]:
    stored = read_json(target, MANIFEST)
    label_policy, partition_policy, storage_policy = policies(stored)
    with LabelFacts(
        source,
        label_policy,
        allow_evaluation_truth=allow_evaluation_truth,
        storage_policy=storage_policy,
    ) as facts:
        preparation = LabelPreparation(facts, partition_policy)
        for _, spec, blob in preparation.partitions():
            check_partition(target, spec, blob)
        expected = preparation.manifest()
        inventory(target, names(expected))
        if stored != expected:
            raise SnapshotError("stockout_label_manifest_full_replay_mismatch")
        return expected


def iter_verified_labels(
    target: Path, source: Path, *, allow_evaluation_truth: bool = False
) -> Iterator[LabelPoint]:
    """Verify all output before any yield; recheck current partition during a second replay.

    This is an explicit labels-only reader, not a final-test or training-role selector.
    """
    verified = verify_label_bundle(target, source, allow_evaluation_truth=allow_evaluation_truth)
    label_policy, partition_policy, storage_policy = policies(verified)
    with LabelFacts(
        source,
        label_policy,
        allow_evaluation_truth=allow_evaluation_truth,
        storage_policy=storage_policy,
    ) as facts:
        if (
            facts.snapshot.snapshot_id != verified["descriptor"]["snapshot_id"]
            or facts.seal != verified["descriptor"]["input_seal"]
        ):
            raise SnapshotError("stockout_label_parent_changed_during_iteration")
        preparation = LabelPreparation(facts, partition_policy)
        for points, spec, blob in preparation.partitions():
            if read_json(target, MANIFEST) != verified:
                raise SnapshotError("stockout_label_bundle_changed_during_iteration")
            check_partition(target, spec, blob)
            yield from points
        inventory(target, names(verified))
        if preparation.manifest() != verified or read_json(target, MANIFEST) != verified:
            raise SnapshotError("stockout_label_bundle_changed_during_iteration")
