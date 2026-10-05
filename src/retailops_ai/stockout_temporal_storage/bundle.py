"""Bounded replay of comparison and split; development arrays exclude final-test labels."""

import hashlib
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Iterator
from importlib.resources import files
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
from retailops_ai.stockout.split import ROLES, DevelopmentRole, SplitPolicy, build_split
from retailops_ai.stockout.upstream_dataset import build_comparison
from retailops_ai.stockout_temporal_storage.schema import encode
from retailops_ai.stockout_temporal_storage.store import (
    DEFAULT_STORAGE_POLICY,
    PartitionInputs,
    TemporalStoragePolicy,
    TemporalStore,
    physical_key,
)
from retailops_ai.stockout_training.contract import MAX_BYTES
from retailops_ai.stockout_training.development import implementation as training_implementation
from retailops_ai.stockout_training.inputs import DevelopmentData

MANIFEST = "manifest.json"
MAX_PARTITION_BYTES = 16 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**2


class TemporalPartitionPolicy(Contract):
    version: Literal["stockout-temporal-partitions-2.0.0"] = "stockout-temporal-partitions-2.0.0"
    batch_rows: Annotated[StrictInt, Field(ge=1, le=256)] = 256
    max_partition_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_PARTITION_BYTES)] = (
        MAX_PARTITION_BYTES
    )


DEFAULT_PARTITION_POLICY = TemporalPartitionPolicy()


def implementation() -> dict[str, Any]:
    import pyarrow  # type: ignore[import-untyped]

    code = {
        r.name: hashlib.sha256(r.read_bytes()).hexdigest()
        for r in sorted(
            files("retailops_ai.stockout_temporal_storage").iterdir(), key=lambda r: r.name
        )
        if r.is_file() and r.name.endswith(".py")
    }
    return dict(
        code_sha256=json_sha256(code),
        frozen_training=training_implementation(),
        pyarrow_version=pyarrow.__version__,
        sqlite_version=sqlite3.sqlite_version,
    )


class TemporalPreparation:
    """Reuse frozen v1 decisions on bounded batches, then combine their additive counts."""

    def __init__(
        self,
        store: TemporalStore,
        split_policy: SplitPolicy,
        partition_policy: TemporalPartitionPolicy,
    ) -> None:
        self.store, self.split_policy, self.policy = store, split_policy, partition_policy
        self.specs: list[dict[str, Any]] = []
        self.counts = dict(comparison=0, membership=0)
        self.digests = {r: hashlib.sha256(b"[") for r in self.counts}
        self.eligible: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.classes: dict[str, Counter[str]] = {r: Counter() for r in ROLES[:-1]}
        self.bytes = 0
        self.started = self.complete = False

    def batch(self, keys: list[tuple[str, str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        parent = self.store.parents["features"]
        common = {k: parent["descriptor"][k] for k in ("source_dataset_id", "qualification_id")}
        # v1 functions only see replayed points; these are explicit bundle identities,
        # not invented legacy IDs or a materialized legacy parent document.
        features = dict(
            feature_dataset_id=parent["feature_bundle_id"],
            descriptor=dict(role="stockout_features", **common),
            points=self.store.points("features", keys),
            report=parent["report"],
        )
        upstream = dict(
            upstream_id=self.store.parents["upstream"]["upstream_bundle_id"],
            descriptor=dict(feature_dataset_id=parent["feature_bundle_id"]),
            points=self.store.points("upstream", keys),
            report=self.store.parents["upstream"]["report"],
        )
        labels = dict(
            label_dataset_id=self.store.parents["labels"]["label_bundle_id"],
            descriptor=dict(role="stockout_labels", **common),
            points=self.store.points("labels", keys),
        )
        return build_comparison(features, upstream), build_split(
            features, labels, self.split_policy
        )

    def partitions(self) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]]:
        if self.started:
            raise SnapshotError("stockout_temporal_preparation_single_use")
        self.started = True
        for keys in self.store.batches(self.policy.batch_rows):
            comparison, split = self.batch(keys)
            rows = dict(comparison=comparison["rows"], membership=split["membership"])
            blobs = encode(rows, self.policy.max_partition_bytes)
            self.bytes += sum(map(len, blobs.values()))
            if self.bytes > MAX_BUNDLE_BYTES:
                raise SnapshotError("stockout_temporal_bundle_resource_limit")
            number = len(self.specs)
            spec = dict(
                number=number,
                keys=len(keys),
                files=[
                    dict(
                        role=r,
                        path=f"part-{number:05d}-{r}.parquet",
                        rows=len(rows[r]),
                        bytes=len(blobs[r]),
                        sha256=hashlib.sha256(blobs[r]).hexdigest(),
                    )
                    for r in rows
                ],
            )
            self.specs.append(spec)
            for role, batch in rows.items():
                for row in batch:
                    if self.counts[role]:
                        self.digests[role].update(b",")
                    self.digests[role].update(canonical_json(row))
                    self.counts[role] += 1
            self.eligible.update(split["report"]["eligible_by_role"])
            self.reasons.update(split["report"]["reasons"])
            for role in self.classes:
                self.classes[role].update(split["report"]["development_classes"][role])
            yield rows, spec, blobs
        for digest in self.digests.values():
            digest.update(b"]")
        self.store.check_database()
        self.store.check_parents()
        self.complete = True

    def manifest(self) -> dict[str, Any]:
        if not self.complete:
            raise SnapshotError("stockout_temporal_preparation_incomplete")
        feature = self.store.parents["features"]
        descriptor = dict(
            schema_version="2.0.0",
            role="stockout_temporal_partitions",
            data_class="features_and_membership_no_outcome_columns",
            grain=["product_id", "stock_location_id", "as_of"],
            order=["product_id", "stock_location_id", "as_of"],
            source_dataset_id=feature["descriptor"]["source_dataset_id"],
            curated_dataset_id=feature["descriptor"]["curated_dataset_id"],
            qualification_id=feature["descriptor"]["qualification_id"],
            parents=dict(
                feature_bundle_id=feature["feature_bundle_id"],
                upstream_bundle_id=self.store.parents["upstream"]["upstream_bundle_id"],
                label_bundle_id=self.store.parents["labels"]["label_bundle_id"],
            ),
            split_policy=self.split_policy.model_dump(mode="json"),
            partition_policy=self.policy.model_dump(mode="json"),
            storage_policy=self.store.policy.model_dump(mode="json"),
            input_seal=self.store.seal,
            implementation=implementation(),
            rows=self.counts,
            rows_sha256={r: d.hexdigest() for r, d in self.digests.items()},
            partitions=self.specs,
            limits=dict(
                keys=self.store.policy.max_rows,
                batch_keys=self.policy.batch_rows,
                database_bytes=self.store.policy.max_db_bytes,
                payload_bytes=self.store.policy.max_payload_bytes,
                batch_payload_bytes=self.store.policy.max_batch_bytes,
                partition_bytes=self.policy.max_partition_bytes,
                bundle_bytes=MAX_BUNDLE_BYTES,
                manifest_bytes=MAX_METADATA_BYTES,
                development_array_bytes=MAX_BYTES,
            ),
        )
        report = dict(
            comparison=dict(
                common_keys_identical=True,
                model_specific_drops=0,
                preprocessing_fitted=False,
                ablation_model_results="pending",
                upstream_forecast_ready=self.store.parents["upstream"]["report"][
                    "upstream_forecast_ready"
                ],
                model_ready=False,
            ),
            split=dict(
                eligible_by_role={r: self.eligible[r] for r in ROLES},
                reasons=dict(sorted(self.reasons.items())),
                development_classes={r: dict(sorted(c.items())) for r, c in self.classes.items()},
                temporal_membership_ready=all(self.eligible[r] > 0 for r in ROLES)
                and all(len(c) == 2 for c in self.classes.values()),
                final_test_outcomes_evaluated=False,
                upstream_forecast_ready=feature["report"]["upstream_forecast_ready"],
                model_ready=False,
            ),
            full_profile_ready=False,
            model_ready=False,
        )
        document = dict(
            temporal_bundle_id="temporal-partitions-sha256-" + json_sha256(descriptor),
            descriptor=descriptor,
            report=report,
        )
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
        descriptor["schema_version"] != "2.0.0"
        or descriptor["role"] != "stockout_temporal_partitions"
    ):
        raise SnapshotError("stockout_temporal_bundle_version_required")
    return (
        SplitPolicy.model_validate(descriptor["split_policy"]),
        TemporalPartitionPolicy.model_validate(descriptor["partition_policy"]),
        TemporalStoragePolicy.model_validate(descriptor["storage_policy"]),
    )


def replay(target: Path, store: TemporalStore, stored: dict[str, Any]) -> dict[str, Any]:
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


def _development_batches(
    target: Path,
    store: TemporalStore,
    verified: dict[str, Any],
    role: DevelopmentRole,
) -> Iterator[tuple[list[dict[str, Any]], list[int], list[list[Any]]]]:
    if role not in ROLES[:-1]:
        raise SnapshotError("stockout_final_test_requires_separate_approved_campaign")
    split_policy, partition_policy, _ = policies(verified)
    preparation = TemporalPreparation(store, split_policy, partition_policy)
    for rows, spec, blobs in preparation.partitions():
        if read_json(target, MANIFEST) != verified:
            raise SnapshotError("stockout_temporal_changed_during_development")
        check_partition(target, spec, blobs)
        selected_membership = [m for m in rows["membership"] if m["eligible"] and m["role"] == role]
        keys = [physical_key(m) for m in selected_membership]
        # This is the only target extraction: SQL is restricted to replayed eligible
        # development keys. No final-test target list or model matrix is created.
        labels = {physical_key(p): p for p in store.points("labels", keys)}
        comparison = {physical_key(p): p for p in rows["comparison"]}
        selected, outcomes, lineage = [], [], []
        for m in selected_membership:
            key = physical_key(m)
            row, point = comparison[key], labels[key]
            if row["status"] != "eligible" or point["incident_stockout"] not in (0, 1):
                raise SnapshotError("stockout_training_ineligible_or_unknown_label")
            category, source_hash = store.category(m["product_id"], m["as_of"])
            selected.append(
                dict(
                    product_id=m["product_id"],
                    stock_location_id=m["stock_location_id"],
                    as_of=m["as_of"],
                    category_id=category,
                    values={**row["base"], **row["upstream"]},
                )
            )
            outcomes.append(point["incident_stockout"])
            lineage.append(
                [role, m["product_id"], m["stock_location_id"], m["as_of"], category, source_hash]
            )
        yield selected, outcomes, lineage
    inventory(target, names(verified))
    if preparation.manifest() != verified or read_json(target, MANIFEST) != verified:
        raise SnapshotError("stockout_temporal_changed_during_development")


def assemble_partitioned_development(
    target: Path, inputs: PartitionInputs, *, allow_evaluation_truth: bool = False
) -> DevelopmentData:
    stored = read_json(target, MANIFEST)
    split_policy, _, storage_policy = policies(stored)
    with TemporalStore(
        inputs, allow_evaluation_truth=allow_evaluation_truth, policy=storage_policy
    ) as store:
        verified = replay(target, store, stored)
        rows: dict[str, list[dict[str, Any]]] = {}
        outcomes: dict[str, list[int]] = {}
        coverage: dict[str, Any] = {}
        lineage_digest = hashlib.sha256(b"[")
        count = size = lineage_count = 0
        for role in ROLES[:-1]:
            selected, targets = [], []
            for batch, labels, lineage in _development_batches(target, store, verified, role):  # type: ignore[arg-type]
                size += len(canonical_json([batch, labels]))
                count += len(batch)
                if size > MAX_BYTES or count > store.policy.max_rows:
                    raise SnapshotError("stockout_temporal_development_array_resource_limit")
                selected.extend(batch)
                targets.extend(labels)
                for entry in lineage:
                    if lineage_count:
                        lineage_digest.update(b",")
                    lineage_digest.update(canonical_json(entry))
                    lineage_count += 1
            rows[role], outcomes[role] = selected, targets
            coverage[role] = dict(
                eligible=len(selected),
                forecast_available=sum(r["values"]["forecast_unavailable"] == 0 for r in selected),
            )
        lineage_digest.update(b"]")
        if any(not rows[r] or set(outcomes[r]) != {0, 1} for r in ROLES[:-1]):
            raise SnapshotError("stockout_training_requires_both_development_classes")
        store.check_database()
        store.check_parents()
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
